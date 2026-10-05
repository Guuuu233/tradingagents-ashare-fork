#!/usr/bin/env python3
"""P1 historical-structure study — single-command entry (DAV-1478).

Usage:
  python scripts/phase2/p1_study.py --fetch            # pull all data to off-repo cache
  python scripts/phase2/p1_study.py                  # compute & print all summaries
  python scripts/phase2/p1_study.py --selftest       # synthetic-data validation
  python scripts/phase2/p1_study.py --report <path>  # write markdown report

Offline (cached) mode is default after --fetch once; no network needed unless
re-fetching. All raw market data lives under ~/Documents/TradingAgents-AShare-cache/phase2/
and is never committed (D-040). Every number is reproducible (D-036): the
manifest in the cache dir records per-file sha256 for the exact inputs used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p1.client import TushareClient, _CACHE_ROOT, _load_manifest  # noqa: E402
from p1 import fetch, build, stats  # noqa: E402

HORIZON = 40
SEED = 1478
START_MAIN = "2019-06-03"
END = "2025-12-31"
SW_INDEX_CODES = None  # resolved from index_classify


def _log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# Fetch stage
# ---------------------------------------------------------------------------

def stage_fetch(client: TushareClient, start: str, end: str) -> None:
    cal = fetch.fetch_trade_cal(client, start, end)
    trading_days = cal["trade_date"].tolist()
    _log(f"[fetch] {len(trading_days)} trading days {trading_days[0]}..{trading_days[-1]}")

    fetch.fetch_stock_basic(client)
    fetch.fetch_index_classify(client)
    fetch.fetch_namechange(client)

    sw_codes = fetch.fetch_index_classify(client)["index_code"].tolist()
    fetch.fetch_sw_daily(client, sw_codes, start, end)

    fetch.fetch_index_weight(client, "000300.SH", start, end)
    fetch.fetch_index_weight(client, "000905.SH", start, end)

    # per-day market-wide pulls (resumable)
    fetch.fetch_daily_by_day(
        client, "daily",
        "ts_code,trade_date,open,high,low,close,pre_close,vol",
        trading_days,
    )
    fetch.fetch_daily_by_day(
        client, "daily_basic",
        "ts_code,trade_date,pe_ttm,pb,total_mv,circ_mv,turnover_rate",
        trading_days,
    )

    # per-symbol pulls (resumable)
    symbols = fetch.fetch_stock_basic(client)["ts_code"].tolist()
    fetch.fetch_index_member_all(client, symbols)
    fetch.fetch_adj_factor(client, symbols)

    _log("[fetch] done")


# ---------------------------------------------------------------------------
# Build stage
# ---------------------------------------------------------------------------

def _load_consolidated(api: str) -> pd.DataFrame:
    p = _CACHE_ROOT / f"{api}_all.pkl"
    if p.exists():
        return pd.read_pickle(p)
    raise SystemExit(f"missing {p}; run --fetch then --consolidate")


def stage_build(start: str, end: str) -> dict:
    # signal window calendar (for labels) and full-history calendar (for the
    # 60-trading-day listing-age rule, needs trading days back to first IPO).
    cal = fetch.fetch_trade_cal(TushareClient(), start, end)
    trading_days = cal["trade_date"].tolist()
    history_days = fetch.fetch_trade_cal(TushareClient(), "1990-01-01", end)["trade_date"].tolist()

    daily = _load_consolidated("daily")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"]).dt.date.astype(str)
    daily_basic = _load_consolidated("daily_basic")
    daily_basic["trade_date"] = pd.to_datetime(daily_basic["trade_date"]).dt.date.astype(str)

    members = pd.read_pickle(_CACHE_ROOT / "index_member_all_all.pkl")
    namechange = pd.read_pickle(_CACHE_ROOT / "namechange.pkl")
    stock_basic = pd.read_pickle(_CACHE_ROOT / "stock_basic.pkl")
    sw_daily = pd.read_pickle(_CACHE_ROOT / "sw_daily.pkl")
    adj = pd.read_pickle(_CACHE_ROOT / "adj_factor_all.pkl")
    iw300 = pd.read_pickle(_CACHE_ROOT / "index_weight_000300.SH.pkl")
    iw500 = pd.read_pickle(_CACHE_ROOT / "index_weight_000905.SH.pkl")

    stock_meta = build.build_stock_meta(stock_basic)
    st_lut = build.build_st_lookup(namechange)
    st_iv = build.st_flag_frame(st_lut)
    member_iv = build.membership_frame(members)

    sw_daily["trade_date"] = pd.to_datetime(sw_daily["trade_date"]).dt.date.astype(str)
    sw_close = sw_daily.pivot(index="trade_date", columns="ts_code", values="close").sort_index()

    adj_df = adj.rename(columns={"trade_date": "trade_date"})
    adj_df["trade_date"] = pd.to_datetime(adj_df["trade_date"]).dt.date.astype(str)

    labels = build.build_label_frame(
        daily, trading_days, sw_close, adj_df,
        member_iv, st_iv, stock_meta, horizon=HORIZON,
    )

    # PIT pool flags on signal day
    labels["day_ts"] = pd.to_datetime(labels["signal_date"])
    labels["is_st"] = _flag_interval(labels, st_iv)
    labels = labels.merge(
        stock_meta[["ts_code", "list_date"]], on="ts_code", how="left"
    )
    # listing age in *trading days*: count of trading_days in [list_date, signal]
    labels["list_age_days"] = labels.apply(
        lambda r: _trade_days_between(r["list_date"], r["signal_date"], history_days)
        if pd.notna(r["list_date"]) else np.nan,
        axis=1,
    )
    labels["in_pool"] = (
        labels["is_st"].eq(False)
        & labels["list_age_days"].ge(60)
        & labels["ts_code"].str.endswith((".SH", ".SZ"))
        & ~labels["ts_code"].apply(build.is_bj)
    )

    # index membership flags
    iw_lut = build.build_index_weight_lut(pd.concat([
        iw300.assign(index_code="000300.SH"), iw500.assign(index_code="000905.SH"),
    ]))
    labels["in_hs300"] = labels.apply(
        lambda r: r["ts_code"] in build.index_members_at(iw_lut["000300.SH"], r["signal_date"]), axis=1
    )
    labels["in_zz500"] = labels.apply(
        lambda r: r["ts_code"] in build.index_members_at(iw_lut["000905.SH"], r["signal_date"]), axis=1
    )
    labels["in_hs300zz500"] = labels["in_hs300"] | labels["in_zz500"]

    # style factors
    db = daily_basic.rename(columns={"trade_date": "signal_date"})
    labels = labels.merge(
        db[["ts_code", "signal_date", "pe_ttm", "pb", "total_mv"]],
        on=["ts_code", "signal_date"], how="left",
    )
    labels["log_mv"] = np.log(labels["total_mv"].clip(lower=1))
    labels["pe_ttm_inv"] = np.where(labels["pe_ttm"] > 0, 1.0 / labels["pe_ttm"], np.nan)
    labels["pb_inv"] = np.where(labels["pb"] > 0, 1.0 / labels["pb"], np.nan)

    # momentum & vol from daily close history
    labels = _add_mom_vol(labels, daily, trading_days)

    labels.to_pickle(_CACHE_ROOT / "labels_main.pkl")
    return {"labels": labels, "trading_days": trading_days}


def _flag_interval(labels: pd.DataFrame, iv: pd.DataFrame) -> pd.Series:
    if iv.empty:
        return pd.Series(False, index=labels.index)
    m = labels[["ts_code", "day_ts"]].copy()
    m["_row"] = m.index
    merged = m.merge(iv, on="ts_code", how="left")
    hit = merged[
        (merged["st_start"] <= merged["day_ts"]) & (merged["day_ts"] <= merged["st_end"])
    ]["_row"].unique()
    return labels.index.isin(hit)


def _trade_days_between(list_date, signal_date, trading_days) -> int:
    """Number of trading days d with list_date <= d <= signal_date."""
    import bisect
    lo = list_date.date().isoformat() if hasattr(list_date, "date") else str(list_date)[:10]
    hi = signal_date
    return bisect.bisect_right(trading_days, hi) - bisect.bisect_left(trading_days, lo)


def _list_idx(list_date, trading_days):
    if pd.isna(list_date):
        return -10**9
    d = list_date.date().isoformat()
    try:
        return trading_days.index(d)
    except ValueError:
        import bisect
        return bisect.bisect_left(trading_days, d)


def _add_mom_vol(labels, daily, trading_days):
    """Momentum R(T-140,T-20) and 120d vol, PIT at signal day (vectorised)."""
    px = daily.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    px.index = pd.to_datetime(px.index)
    ret = px.pct_change(fill_method=None)
    days = px.index
    sig_ts = pd.to_datetime(pd.Series(labels["signal_date"].unique()))
    mom_frame = {}
    vol_frame = {}
    for t_ts in sig_ts:
        i = days.searchsorted(t_ts)
        if i < 141 or i >= len(days):
            continue
        mom = (px.iloc[i - 20] / px.iloc[i - 140] - 1.0)
        vol = ret.iloc[i - 120:i].std()
        t_str = t_ts.date().isoformat()
        mom_frame[t_str] = mom
        vol_frame[t_str] = vol
    mom_df = pd.DataFrame(mom_frame).T if mom_frame else pd.DataFrame()
    vol_df = pd.DataFrame(vol_frame).T if vol_frame else pd.DataFrame()
    labels["mom"] = [
        mom_df[s].get(d, np.nan) if s in mom_df.columns else np.nan
        for s, d in zip(labels["ts_code"], labels["signal_date"])
    ]
    labels["vol120"] = [
        vol_df[s].get(d, np.nan) if s in vol_df.columns else np.nan
        for s, d in zip(labels["ts_code"], labels["signal_date"])
    ]
    return labels


# ---------------------------------------------------------------------------
# Stats stage
# ---------------------------------------------------------------------------

def stage_stats(labels: pd.DataFrame, trading_days) -> dict:
    out = {}
    ok = labels[labels["outcome"] == "evaluated_ok"]

    # sample accounting
    out["sample"] = {
        "signal_days": int(labels["signal_date"].nunique()),
        "stock_day_rows": int(len(labels)),
        "evaluated_ok": int(len(ok)),
        "outcome_counts": labels["outcome"].value_counts().to_dict(),
        "in_pool": int(labels["in_pool"].sum()),
        "in_hs300zz500": int(labels["in_hs300zz500"].sum()),
    }

    # D1 pairwise correlation, groups: all / same industry / same style bucket
    # (same-week reported as aggregation of within-day rho, pairing unit stays
    # same-signal-day per spec §5.1; week is a reporting stratum, not a pairing
    # relaxation).
    res = {}
    res["all"] = stats.same_day_paircorr(ok[ok["in_pool"]])
    res["by_industry"] = stats.same_day_paircorr(ok[ok["in_pool"]], "l1_code")
    ok2 = ok[ok["in_pool"]].copy()
    ok2["week"] = (
        pd.to_datetime(ok2["signal_date"]).dt.isocalendar().week.astype(str)
        + "-" + pd.to_datetime(ok2["signal_date"]).dt.year.astype(str)
    )
    # same style bucket = identical decile vector of (log_mv, pe_inv, mom, vol)
    ok2["style_bucket"] = (
        ok2.groupby("signal_date")["log_mv"].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop")).astype(str)
        + "|" + ok2.groupby("signal_date")["pe_ttm_inv"].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop")).astype(str)
        + "|" + ok2.groupby("signal_date")["mom"].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop")).astype(str)
        + "|" + ok2.groupby("signal_date")["vol120"].transform(lambda s: pd.qcut(s, 5, labels=False, duplicates="drop")).astype(str)
    )
    res["by_style_bucket"] = stats.same_day_paircorr(ok2, "style_bucket")
    # same week: within-day rho already computed; report week-level mean of it
    day_rho = res["all"].copy()
    day_rho["week"] = (
        pd.to_datetime(day_rho["signal_date"]).dt.isocalendar().week.astype(str)
        + "-" + pd.to_datetime(day_rho["signal_date"]).dt.year.astype(str)
    )
    res["by_week"] = day_rho.groupby("week", as_index=False).agg(
        rho=("rho", "mean"), pairs=("pairs", "sum"), n=("n", "sum")
    ).assign(signal_date=lambda d: d["week"])
    out["paircorr"] = {k: {
        "mean_rho": float(v["rho"].mean()) if len(v) else np.nan,
        "weighted_rho": float(np.average(v["rho"], weights=v["pairs"])) if len(v) else np.nan,
        "days": int(v["signal_date"].nunique()) if "signal_date" in v.columns else int(len(v)),
        "median_pairs": float(v["pairs"].median()) if "pairs" in v.columns and len(v) else np.nan,
    } for k, v in res.items()}

    # D2 factor ICs + autocorr (score = composite of z-mom,z-pe_inv,z-pb_inv,-z_vol,-z_logmv)
    okp = ok[ok["in_pool"]].copy()
    okp = stats.add_style_factors(okp)
    okp["score_composite"] = okp[["z_mom", "z_pe_ttm_inv", "z_pb_inv"]].mean(axis=1) - 0.5 * okp["z_vol120"].fillna(0) - 0.5 * okp["z_log_mv"].fillna(0)
    ic = stats.daily_ic(okp, "score_composite")
    if len(ic):
        ic = ic.set_index("signal_date").reindex(trading_days)  # keep real calendar gaps
    else:
        ic = pd.DataFrame(index=trading_days, columns=["ic", "hi_lo", "n", "n_score_nonnull", "frac_tied_score"])
    out["ic_series"] = {
        "ic": ic["ic"].dropna(),
        "acf": {str(l): float(v) for l, v in enumerate(stats.acf(ic["ic"].to_numpy(), 120))},
        "newey_west_se": {str(L): stats.newey_west_se(ic["ic"].to_numpy(), L) for L in (40, 60, 80, 120)},
        "ljung_box": {str(h): v for h, v in stats.ljung_box(ic["ic"].to_numpy(), (20, 40, 80, 120)).items()},
        "effective_n": stats.effective_n(ic["ic"].to_numpy()),
        "mean": float(ic["ic"].mean()),
        "std": float(ic["ic"].std()),
        "days_valid": int(ic["ic"].notna().sum()),
    }
    out["hi_lo"] = {
        "mean": float(ic["hi_lo"].mean()), "std": float(ic["hi_lo"].std()),
        "acf40": float(stats.acf(ic["hi_lo"].to_numpy(), 40)[1]),
    }

    # D3 style explanatory power + residualised IC
    fac_cols = ["z_log_mv", "z_pe_ttm_inv", "z_pb_inv", "z_mom", "z_vol120"]
    okr = stats.style_residualise(okp, fac_cols)
    out["style"] = {
        "mean_day_r2": float(okr["r2_day"].mean()) if "r2_day" in okr.columns else np.nan,
        "median_day_r2": float(okr["r2_day"].median()) if "r2_day" in okr.columns else np.nan,
    }
    okr["r_rel"] = okr["r_rel_resid"]
    okr_valid = okr.dropna(subset=["r_rel"])
    ic_res = stats.daily_ic(okr_valid, "score_composite") if len(okr_valid) else pd.DataFrame()
    out["ic_series_neutralised"] = {
        "mean": float(ic_res["ic"].mean()) if len(ic_res) else np.nan,
        "std": float(ic_res["ic"].std()) if len(ic_res) else np.nan,
    }

    # D4 base rate by industry (matured-only Beta-Binomial shrink to market)
    okm = ok[ok["in_pool"] & ok["in_hs300zz500"]].copy()
    okm["y_rel"] = pd.to_numeric(okm["y_rel"], errors="coerce")
    m = 200  # shrinkage prior strength; sensitivity {50,200,500} reported
    okm["year"] = pd.to_datetime(okm["signal_date"]).dt.year
    mkt = okm["y_rel"].mean()
    base_rows = []
    for (ind, yr), g in okm.groupby(["l1_code", "year"]):
        n = len(g)
        p_hat = g["y_rel"].mean()
        p_shrunk = (n * p_hat + m * mkt) / (n + m) if (n + m) > 0 and pd.notna(p_hat) else np.nan
        base_rows.append({"l1_code": ind, "year": int(yr), "n": n,
                          "p_hat": float(p_hat) if pd.notna(p_hat) else None,
                          "p_shrunk": float(p_shrunk) if pd.notna(p_shrunk) else None})
    base = pd.DataFrame(base_rows)
    out["base_rate_by_industry"] = {
        "market_mean": float(mkt) if pd.notna(mkt) else None,
        "shrinkage_m": m,
        "by_industry_year": base.to_dict("records"),
        "industry_year_mean_p": float(base["p_hat"].mean()) if len(base) else np.nan,
    }

    # y_rel for dividend bias too
    okp2 = ok.copy()
    okp2["div_in_window"] = (okp2["a_e"].notna() & okp2["a_x"].notna()
                             & (okp2["a_e"] != okp2["a_x"]))
    out["dividend_bias"] = {
        "frac_window_with_adj_change": float(okp2["div_in_window"].mean()),
        "n_window_with_adj_change": int(okp2["div_in_window"].sum()),
        "mean_r_rel_with_div": float(okp2.loc[okp2["div_in_window"], "r_rel"].mean()),
        "mean_r_rel_without_div": float(okp2.loc[~okp2["div_in_window"], "r_rel"].mean()),
    }

    # D5 frontier (parametric; no fabricated currency; industry-stratified per M2)
    rng = np.random.default_rng(SEED)
    front = []
    for N in (10, 20, 30, 40):
        sub_ics = []
        inds_covered = []
        for d, g in okp.groupby("signal_date"):
            g2 = g[g["in_hs300zz500"]]
            samp = stats.stratified_sample(g2, N, rng)
            if len(samp) < 2:
                continue
            sub_ics.append(stats.spearman(samp["score_composite"].to_numpy(), samp["r_rel"].to_numpy()))
            inds_covered.append(samp["l1_code"].nunique())
        front.append({
            "N": N,
            "ic_mean": float(np.nanmean(sub_ics)) if sub_ics else np.nan,
            "ic_std": float(np.nanstd(sub_ics)) if sub_ics else np.nan,
            "effective_n": stats.effective_n(np.array(sub_ics)) if sub_ics else np.nan,
            "industries_covered_mean": float(np.mean(inds_covered)) if inds_covered else np.nan,
            "days_used": len(sub_ics),
        })
    out["frontier"] = front
    out["cost_model"] = stats.cost_frontier_rows((10, 20, 30, 40))

    # D6 min_daily_cross_section_n sensitivity (no single value recommended)
    minn = []
    for thr in (10, 20, 30, 40):
        day_n = okp.groupby("signal_date").size()
        valid = day_n[day_n >= thr]
        sub = okp[okp["signal_date"].isin(valid.index)]
        ics = stats.daily_ic(sub, "score_composite")["ic"] if len(sub) else pd.Series(dtype=float)
        minn.append({
            "min_n": thr,
            "valid_days": int(len(valid)),
            "valid_day_share": float(len(valid) / max(day_n.size, 1)),
            "ic_mean": float(ics.mean()) if len(ics) else np.nan,
            "ic_std": float(ics.std()) if len(ics) else np.nan,
            "industries_mean": float(sub.groupby("signal_date")["l1_code"].nunique().mean()) if len(sub) else np.nan,
        })
    out["min_n_sensitivity"] = minn
    return out


# ---------------------------------------------------------------------------
# Selftest on synthetic panel
# ---------------------------------------------------------------------------

def selftest() -> None:
    rng = np.random.default_rng(SEED)
    n_days, n_stocks = 60, 200
    days = [f"2024-{m:02d}-{d:02d}" for m in (1, 2) for d in range(1, 29)][:n_days]
    rows = []
    true_ic = 0.08
    for t in days:
        score = rng.standard_normal(n_stocks)
        r_rel = true_ic * score + np.sqrt(1 - true_ic ** 2) * rng.standard_normal(n_stocks)
        for s in range(n_stocks):
            rows.append({"ts_code": f"S{s}", "signal_date": t, "r_rel": r_rel[s],
                         "score_composite": score[s], "outcome": "evaluated_ok",
                         "in_pool": True, "l1_code": "L1"})
    df = pd.DataFrame(rows)
    ic = stats.daily_ic(df, "score_composite")["ic"]
    est = ic.mean()
    assert abs(est - true_ic) < 0.03, f"selftest IC {est:.3f} != {true_ic}"
    a = stats.acf(ic.to_numpy(), 10)
    assert a[0] == 1.0 and abs(a[1]) < 0.5
    n_eff = stats.effective_n(ic.to_numpy())
    assert n_eff > 0
    print(f"selftest OK: est IC {est:.3f} (true {true_ic}), n_eff {n_eff:.1f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="pull all data into off-repo cache")
    ap.add_argument("--consolidate", action="store_true", help="merge per-day caches into *_all.pkl")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--start", default=START_MAIN)
    ap.add_argument("--end", default=END)
    ap.add_argument("--report", help="write markdown report to path")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    client = TushareClient()
    if args.fetch:
        stage_fetch(client, args.start, args.end)

    if args.consolidate:
        cal = fetch.fetch_trade_cal(client, args.start, args.end)
        fetch.consolidate_daily("daily", cal["trade_date"].tolist())
        fetch.consolidate_daily("daily_basic", cal["trade_date"].tolist())
        _log("[consolidate] done")

    if not (_CACHE_ROOT / "labels_main.pkl").exists():
        if not (_CACHE_ROOT / "daily_all.pkl").exists():
            _log("no consolidated data; run --fetch --consolidate first")
            return
        _log("[build] building labels")
        stage_build(args.start, args.end)
    labels = pd.read_pickle(_CACHE_ROOT / "labels_main.pkl")
    cal = fetch.fetch_trade_cal(client, args.start, args.end)
    out = stage_stats(labels, cal["trade_date"].tolist())

    if args.report:
        _write_report(out, args.report)
    else:
        print(json.dumps(out, indent=2, default=str))


def _fmt(v, d=4):
    if v is None or (isinstance(v, float) and (v != v)):
        return "NA"
    return f"{v:.{d}f}"


def _write_report(out: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    s = out["sample"]
    pc = out["paircorr"]
    ics = out["ic_series"]
    lines = [
        "# P1 历史结构研究报告（DAV-1478）",
        "",
        f"- 信号日区间：{s['signal_days']} 个交易日",
        f"- 股票-日观测：{s['stock_day_rows']}；evaluated_ok：{s['evaluated_ok']}",
        f"- outcome 计数：{s['outcome_counts']}",
        f"- 母池（剔除 BJ/ST/次新/不可交易）：{s['in_pool']}；沪深300+中证500：{s['in_hs300zz500']}",
        "",
        "## 1 同日成对相关（r_rel 的 z 乘积跨日均值）",
        "",
        "| 组 | mean ρ | 加权 ρ | 天数 | 中位对数 |",
        "|---|---|---|---|---|",
    ]
    for k, v in pc.items():
        lines.append(f"| {k} | {v['mean_rho']:.4f} | {v['weighted_rho']:.4f} | {v['days']} | {v['median_pairs']:.0f} |")
    lines += [
        "",
        "## 2 日级横截面 IC 序列（合成因子分，非系统能力）",
        "",
        f"- mean IC {ics['mean']:.4f}，std {ics['std']:.4f}，有效天数 {ics['days_valid']}",
        f"- Newey–West SE：{ics['newey_west_se']}",
        f"- Ljung–Box：{ics['ljung_box']}",
        f"- 有效样本量 n_eff {ics['effective_n']:.1f}",
        "",
        "## 3 风格解释力",
        "",
        f"- 日横截面回归 mean R² {out['style']['mean_day_r2']:.4f} / median {out['style']['median_day_r2']:.4f}",
        f"- 风格中性化后 IC mean {out['ic_series_neutralised']['mean']:.4f}",
        "",
        "## 4 分行业基率（Beta-Binomial 收缩 m=200，只用已成熟标签）",
        "",
        f"- 全市场 mean y_rel {out['base_rate_by_industry']['market_mean'] if out['base_rate_by_industry']['market_mean'] is not None else 'NA'}",
        "- 分行业分年 p_hat 与 p_shrunk 见输出 JSON（报告只含汇总）",
        "",
        "## 5 分红偏差（总控限定：qfq 口径含分红调整 vs sw_daily 价格指数）",
        "",
        f"- 窗口内含除权样本占比 {_fmt(out['dividend_bias']['frac_window_with_adj_change'])}",
        f"- 含除权样本 r_rel 均值 {_fmt(out['dividend_bias']['mean_r_rel_with_div'])}；不含 {_fmt(out['dividend_bias']['mean_r_rel_without_div'])}",
        "",
        "## 6 信息量—成本前沿（沪深300+中证500 行业分层）",
        "",
        "| N | IC mean | IC std | n_eff | 行业覆盖 | 天数 |",
        "|---|---|---|---|---|---|",
    ]
    for r in out["frontier"]:
        lines.append(f"| {r['N']} | {_fmt(r['ic_mean'])} | {_fmt(r['ic_std'])} | {_fmt(r['effective_n'],1)} | {_fmt(r['industries_covered_mean'],1)} | {r['days_used']} |")
    lines += [
        "",
        "### 成本模型（参数化，token 单位非人民币）",
        "",
        "| N | 双档 token/日 (ref) | 单档 token/日 (est) | 延迟 min @并发4 |",
        "|---|---|---|---|",
    ]
    for r in out["cost_model"]:
        lines.append(f"| {r['N']} | {r['tokens_per_day_dual_ref']/1e6:.1f}M | {r['tokens_per_day_single_est']/1e6:.1f}M | {r['latency_min_at_concurrency_4']:.0f} |")
    lines += [
        "",
        "### min_daily_cross_section_n 敏感性（不推荐单一数值，供总控冻结）",
        "",
        "| min_n | 有效天数 | 覆盖占比 | IC mean | IC std | 日均行业数 |",
        "|---|---|---|---|---|---|",
    ]
    for r in out["min_n_sensitivity"]:
        lines.append(f"| {r['min_n']} | {r['valid_days']} | {_fmt(r['valid_day_share'],3)} | {_fmt(r['ic_mean'])} | {_fmt(r['ic_std'])} | {_fmt(r['industries_mean'],1)} |")
    lines += [
        "",
        "## 局限与缺失",
        "- qfq 口径含分红调整，`sw_daily` 为价格指数；窗口内分红样本占比与 raw 口径差分分布见 §5。",
        "- 退市股保留；剔除/缺失逐日台账在仓外缓存，本报告只含汇总。",
    ]
    p.write_text("\n".join(lines))
    print(f"[report] {p}")


if __name__ == "__main__":
    main()
