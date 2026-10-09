#!/usr/bin/env python3
"""DAV-1705 管线试跑，非成绩 — 影子组合串联驱动（M2-A 成交规则 + M2-B 三套策略）.

输入: 账本 `forward_ledger.jsonl`（--ledger，P4 账本全量封存行）,
      Phase-2 缓存 daily_by_day（--cache-dir，只读）,
      交易日历（--calendar 显式文件 / Tushare 运行时 / 本机缓存，见
      resolve_calendar）.
输出: <out-dir>/shadow_trial-<tag>.json（canonical JSON, sort_keys, 确定性）.
      默认 out-dir 为 `work/analysis_runs/dav1705/`（gitignored，见 README）.

产出清单（output_inventory）: primary（任务窗口） + synthetic_smoke +
            synthetic_edge（内存合成对照）. signal_date ∈ [2026-03, 2026-09)
            的非任务窗口记录不进入任何产出项，仅计数备查
           （non_task_window_excluded_n），M-03 已闭环。
另含合成行情冒烟项 synthetic_smoke（内存构造 3 根 bar, 证明成交/记账代码
路径本身可 FILLED, 与数据覆盖问题隔离）.
不调模型, 不写生产库, 不碰活库（行情只读 phase2 pkl 缓存）.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tradingagents.eval.shadow_portfolio import (
    STRATEGIES,
    compare_strategies,
    risk_rules_v2,
    run_shadow_portfolio,
)
from tradingagents.eval.trade_execution import (
    DailyBar,
    ExecutionEngine,
    OrderSide,
)

HERE = Path(__file__).resolve().parent
TAG = "管线试跑，非成绩"

import os

DEFAULT_CACHE_DIR = Path(os.environ.get(
    "PHASE2_CACHE_DIR",
    "~/Documents/TradingAgents-AShare-cache/phase2")).expanduser()
DEFAULT_OUT_DIR = REPO_ROOT / "work" / "analysis_runs" / "dav1705"
# Fixed P4 account (controller order 2026-10-09): only this user_id enters
# the trial; other accounts are counted as excluded_user_n.
DEFAULT_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"

CUTOFF = "2026-09-01"


def _norm_day(raw: object) -> str:
    """日历日期归一为 ISO YYYY-MM-DD（兼容 YYYYMMDD / ISO 输入）."""
    s = str(raw).strip()[:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


def load_calendar_file(path: Path) -> list[str]:
    """显式日历文件：一行一个 YYYYMMDD / YYYY-MM-DD."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(_norm_day(s))
    return sorted(set(out))


def _calendar_from_tushare() -> list[str] | None:
    """运行时从 Tushare trade_cal 取 SSE 日历；无 token/失败返回 None.

    复用生产侧同一请求函数 `_query_tushare_api`（token 绝不打印）。"""
    from tradingagents.dataflows.providers.industry_linkage_provider import (
        _query_tushare_api,
    )
    from tradingagents.dataflows.trade_calendar import cn_today_str

    end = cn_today_str().replace("-", "")
    df, _, _ = _query_tushare_api(
        "trade_cal", fields="cal_date,is_open",
        params={"exchange": "SSE", "start_date": "19900101",
                "end_date": end})
    if df is None or df.empty or "cal_date" not in df.columns:
        return None
    days = df.loc[df["is_open"].astype(str) == "1", "cal_date"]
    out = sorted({_norm_day(d) for d in days.tolist()})
    return out or None


def _calendar_from_local_cache(cache_dir: Path) -> list[str]:
    """读本机缓存：<cache>/api_cache/trade_cal_*.pkl 取覆盖最广的一份."""
    cals = sorted((cache_dir / "api_cache").glob("trade_cal_*.pkl"))
    if not cals:
        raise FileNotFoundError(
            f"trade_cal pkl missing in {cache_dir}/api_cache")
    import pandas as pd

    best: list[str] = []
    for p in cals:
        df = pd.read_pickle(p)
        days = sorted({_norm_day(d) for d in
                       df.loc[df["is_open"].astype(str) == "1",
                                "cal_date"].tolist()})
        if len(days) > len(best):
            best = days
    if not best:
        raise ValueError("trade_cal cache has no open days")
    return best


def resolve_calendar(calendar_file: Path | None = None,
                     cache_dir: Path | None = None
                     ) -> tuple[list[str], str]:
    """运行时交易日历三级来源：显式文件 > Tushare 在线 > 本机缓存.

    返回 (ISO 日期序列, 来源说明)。各级失败自动降级；全部失败抛异常
    （fail-close，绝不退化成自然日口径）。"""
    if calendar_file is not None:
        return load_calendar_file(calendar_file), f"file:{calendar_file}"
    ts = _calendar_from_tushare()
    if ts:
        return ts, "tushare:trade_cal/SSE"
    cache = cache_dir or DEFAULT_CACHE_DIR
    return _calendar_from_local_cache(cache), f"cache:{cache}/api_cache"


def load_records(ledger: Path) -> list[dict]:
    recs = []
    with open(ledger, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rec = obj.get("record") if isinstance(obj, dict) else None
            recs.append(rec if isinstance(rec, dict) else obj)
    return recs


def filter_by_user(records: list[dict],
                   user_id: str = DEFAULT_USER_ID
                   ) -> tuple[list[dict], int]:
    """Keep only `user_id` records (DAV-1740 account hygiene).

    Returns (kept, excluded_n). Missing/mismatched user_id never enters."""
    if not user_id:
        return records, 0
    kept = [r for r in records
            if str(r.get("user_id") or "") == str(user_id)]
    return kept, len(records) - len(kept)


class PklBars:
    """Phase-2 daily_by_day 只读行情源（缺文件/缺 symbol 即 None, 不抛异常）."""

    def __init__(self, daily_dir: Path):
        self.daily_dir = daily_dir
        self._cache: dict[str, dict[str, DailyBar]] = {}
        self.lookups = 0
        self.misses = 0

    def _load_day(self, iso: str) -> dict[str, DailyBar]:
        hit = self._cache.get(iso)
        if hit is not None:
            return hit
        out: dict[str, DailyBar] = {}
        for name in (iso + ".pkl", iso.replace("-", "") + ".pkl"):
            p = self.daily_dir / name
            if p.exists():
                import pandas as pd

                df = pd.read_pickle(p)
                for row in df.itertuples(index=False):
                    d = str(row.trade_date)
                    iso_d = (f"{d[:4]}-{d[4:6]}-{d[6:]}"
                             if len(d) == 8 and d.isdigit() else d)
                    try:
                        o = float(row.open)
                    except (TypeError, ValueError):
                        o = None
                    out[str(row.ts_code)] = DailyBar(
                        trade_date=iso_d, open=o,
                        high=_f(row.high), low=_f(row.low),
                        close=_f(row.close),
                        pre_close=_f(row.pre_close), vol=_f(row.vol))
                break
        self._cache[iso] = out
        return out

    def get_bar(self, symbol: str, iso_date: str):
        self.lookups += 1
        bar = self._load_day(iso_date).get(symbol)
        if bar is None:
            self.misses += 1
        return bar


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def run_slice(records: list[dict], engine: ExecutionEngine,
              cal: list[str], name: str) -> dict:
    rules = risk_rules_v2()
    results = []
    for sname, sfn in STRATEGIES.items():
        res = run_shadow_portfolio(
            records, sfn, engine, rules, initial_cash=1_000_000.0,
            horizon="medium", strategy_name=sname)
        results.append(res)
    table = compare_strategies(results)
    entry_sides = Counter()
    for r in results:
        for e in r.entries:
            entry_sides[f"{r.strategy}/{e.side}"] += 1
    return {
        "pipeline_trial_non_result": TAG,
        "slice": name,
        "n_records": len(records),
        "risk_rules": rules.name,
        "initial_cash": 1_000_000.0,
        "calendar_days": len(cal),
        "calendar_first": cal[0] if cal else None,
        "calendar_last": cal[-1] if cal else None,
        "strategies": table,
        "filled_entry_histogram": dict(sorted(entry_sides.items())),
        "nav_heads": {r.strategy: r.nav_series[:3] for r in results},
        "nav_tails": {r.strategy: r.nav_series[-3:] for r in results},
    }


def synthetic_smoke() -> dict:
    """内存合成行情冒烟: 1 条看多记录应 T+1 开盘 FILLED, 卖出走收盘."""
    cal = ["2026-09-28", "2026-09-29", "2026-09-30",
           "2026-10-09", "2026-10-12", "2026-10-13"]
    bars = {
        "2026-10-09": DailyBar(trade_date="2026-10-09", open=10.0, high=10.2,
                               low=9.9, close=10.1, pre_close=10.0, vol=1e6),
        "2026-10-12": DailyBar(trade_date="2026-10-12", open=10.1, high=10.4,
                               low=10.0, close=10.3, pre_close=10.1, vol=1e6),
        "2026-10-13": DailyBar(trade_date="2026-10-13", open=10.3, high=10.5,
                               low=10.2, close=10.4, pre_close=10.3, vol=1e6),
    }
    eng = ExecutionEngine(trading_calendar=cal,
                          get_bar=lambda s, d: bars.get(d))
    buy = eng.execute_buy("000001.SZ", "2026-09-30", cash=100_000.0)
    sell = (eng.execute_sell("000001.SZ", "2026-10-12", buy.shares)
            if buy.shares else None)
    return {
        "pipeline_trial_non_result": TAG,
        "buy_status": str(buy.status),
        "buy_executed_date": buy.executed_date,
        "buy_requested_date": buy.requested_date,
        "buy_shares": buy.shares,
        "buy_price": buy.price,
        "sell_status": str(sell.status) if sell else None,
        "sell_executed_date": sell.executed_date if sell else None,
        "sell_price": sell.price if sell else None,
    }


def synthetic_edge() -> dict:
    """R2 锁板全覆盖 + R3 归因: 连续 6 个一字涨停日耗尽顺延窗口.

    窗口前/后各放一根正常 bar 提供双边证据, 预期 UNEXECUTABLE/suspension.
    6 个锁板日全部被 R2 判定为 locked 是 suspension 成立的前提——窗口内任
    一日可成交都会直接 FILLED, 不会走到归因分支. 另直探首日分类作旁证.
    """
    lock_days = ["2026-10-%02d" % d for d in (9, 12, 13, 14, 15, 16)]
    cal = ["2026-09-28", "2026-09-29", "2026-09-30",
           "2026-10-08"] + lock_days + ["2026-10-19", "2026-10-20"]
    # 一字涨停（主板 10%）: open==high==low==11.0, pre_close=10.0
    lock = DailyBar(trade_date="", open=11.0, high=11.0,
                    low=11.0, close=11.0, pre_close=10.0, vol=1e6)
    normal = DailyBar(trade_date="", open=10.0, high=10.2,
                      low=9.9, close=10.1, pre_close=10.0, vol=1e6)
    table = {d: lock for d in lock_days}
    table["2026-10-08"] = normal
    table["2026-10-19"] = normal
    table["2026-10-20"] = normal
    eng = ExecutionEngine(trading_calendar=cal,
                          get_bar=lambda s, d: table.get(d))
    day_kind, _ = eng._classify_day("000001.SZ", lock_days[0],
                                     OrderSide.BUY)
    r1 = eng.execute_buy("000001.SZ", "2026-10-08", cash=100_000.0)
    eng2 = ExecutionEngine(trading_calendar=cal,
                           get_bar=lambda s, d: None)
    r2 = eng2.execute_buy("000001.SZ", "2026-10-08", cash=100_000.0)
    return {
        "pipeline_trial_non_result": TAG,
        "first_lock_day_kind": day_kind,
        "expected_first_lock_day_kind": "locked",
        "all_lock_window_buy_status": str(r1.status),
        "all_lock_window_buy_reason": r1.reason,
        "expected_reason": "suspension",
        "all_missing_buy_status": str(r2.status),
        "all_missing_buy_reason": r2.reason,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tag", nargs="?", default="a")
    ap.add_argument("--ledger", required=True,
                    help="P4 封存账本 forward_ledger.jsonl")
    ap.add_argument("--calendar", default=None,
                    help="显式日历文件（缺省走运行时三级来源）")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                    help="Phase-2 缓存目录（只读）")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                    help="产物目录（默认 gitignored 的 work/analysis_runs/dav1705）")
    ap.add_argument("--write-calendar", default=None,
                    help="将本次解析的日历另存为文件（供 m2 报告 --calendar 复用）")
    ap.add_argument("--user-id", default=DEFAULT_USER_ID,
                    help="仅统计该 user_id 的账本记录 "
                         "(default: fixed P4 account)")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cal_file = Path(args.calendar) if args.calendar else None
    cal, cal_source = resolve_calendar(cal_file, cache_dir)
    if args.write_calendar:
        Path(args.write_calendar).write_text(
            "\n".join(cal) + "\n", encoding="utf-8")
    records = load_records(Path(args.ledger))
    ledger_n = len(records)
    user_id = getattr(args, "user_id", None) or DEFAULT_USER_ID
    records, excluded_user_n = filter_by_user(records, user_id)
    if excluded_user_n:
        print(f"SKIP: {excluded_user_n}/{ledger_n} ledger record(s) "
              f"filtered by user_id (kept user_id={user_id})")
    primary = [r for r in records
               if str(r.get("signal_date") or "") >= CUTOFF]
    src = PklBars(cache_dir / "daily_by_day")
    engine = ExecutionEngine(trading_calendar=cal, get_bar=src.get_bar)
    out_primary = run_slice(primary, engine, cal, "primary_ge_2026-09-01")
    out_primary["bar_lookups"] = src.lookups
    out_primary["bar_misses"] = src.misses
    non_task_n = sum(
        1 for r in records
        if "2026-03-01" <= str(r.get("signal_date") or "") < CUTOFF)
    payload = {
        "pipeline_trial_non_result": TAG,
        "ledger_lines": ledger_n,
        "user_id": user_id,
        "excluded_user_n": excluded_user_n,
        "filtered_ledger_lines": len(records),
        "calendar_source": cal_source,
        "primary": out_primary,
        "output_inventory": ["primary", "synthetic_smoke", "synthetic_edge"],
        "non_task_window_excluded_n": non_task_n,
        "non_task_window_note": ("signal_date ∈ [2026-03, 2026-09) 的非任务窗口记录 "
                                  "不进入任何产出项，仅计数备查（M-03）."),
        "synthetic_smoke": synthetic_smoke(),
        "synthetic_edge": synthetic_edge(),
        "notes": [
            "primary 窗口行情缺失时成交顺延耗尽归因 suspension/data_missing, "
            "属 M2-A R3 口径, 非代码异常.",
            "synthetic_smoke/synthetic_edge 为内存合成行情对照, "
            "仅用于隔离“代码路径 vs 数据覆盖”, 非任务窗口, 不计成绩.",
        ],
    }
    path = out_dir / f"shadow_trial-{args.tag}.json"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"OK: wrote {path} "
          f"(primary={len(primary)}, non_task_excluded={non_task_n}, "
          f"excluded_user={excluded_user_n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
