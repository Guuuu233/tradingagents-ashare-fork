"""Read-only acceptance audit of the phase2 data cache (DAV-1546).

Verifies, without re-fetching or modifying anything:

1. manifest.json — every entry's file exists in ``api_cache/``, is non-zero
   in size, has a plausible ``fetched_at``, and (for a deterministic sample)
   has a matching sha256 and row count.
2. Coverage — trade calendar span; per-day completeness of ``daily_by_day/``
   and ``daily_basic_by_day/`` against open trading days; per-symbol
   ``adj_factor/`` vs ``adj_factor_all.pkl``; ``index_member_all/`` vs
   ``index_member_all_all.pkl``; ``sw_daily`` industry coverage; index-weight
   span; stock_basic list-status split.
3. Writes a per-check details JSON (checkpoint) so a re-run skips work
   already done.

The cache directory is opened strictly read-only; nothing under it is
written, overwritten or deleted. All intermediate results go to
``--out`` (a file inside the repo, or a caller-chosen path).

Run:
    .venv310/bin/python scripts/phase2/cache_acceptance.py \
        --cache ~/Documents/TradingAgents-AShare-cache/phase2 \
        --details work/dav-1546-cache-acceptance.json \
        --report docs/research/phase2-midterm/cache-acceptance.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

WINDOW_START = "20151001"
WINDOW_END = "20251231"
STUDY_WINDOW_START = "2015-10-01"
STUDY_WINDOW_END = "2025-12-31"
# Big top-level pkls are hashed in chunks so a 700 MB file never lives in RAM.
_CHUNK = 8 * 1024 * 1024
# sha256 sampling: all small files plus at least this many large ones.
_SHA_SAMPLE_MIN_BIG = 5
_SHA_SAMPLE_MIN_SMALL = 5
# Row-count re-check sample per api (on top of any that fail size checks).
_ROWS_SAMPLE_PER_API = 25


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blk in iter(lambda: f.read(_CHUNK), b""):
            h.update(blk)
    return h.hexdigest()


def _peak_rss_gb() -> float:
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 3)
    except Exception:
        return -1.0


def _load_details(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"checks": {}, "runtime": {}}


def _save_details(path: Path, d: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _entry_fname(key: str, entry: Dict[str, Any]) -> str:
    return key if key.endswith(".pkl") else key + ".pkl"


def check_manifest(
    cache: Path,
    details: Dict[str, Any],
    seed: int,
) -> Dict[str, Any]:
    """Validate manifest entries; resume-aware, entry-by-entry."""
    done = details["checks"].setdefault("manifest_entries", {})
    manifest = json.loads((cache / "manifest.json").read_text())
    files = manifest.get("files", {})
    api_dir = cache / "api_cache"

    missing_file: List[str] = []
    zero_size: List[str] = []
    bad_fields: List[str] = []
    per_api_entries: Dict[str, List[str]] = {}
    fetched_min: Dict[str, str] = {}
    fetched_max: Dict[str, str] = {}
    row_total: Dict[str, int] = {}

    for fname, meta in files.items():
        if fname in done:
            continue
        rec: Dict[str, Any] = {}
        fp = api_dir / fname
        if not fp.exists():
            rec["exists"] = False
            missing_file.append(fname)
        else:
            sz = fp.stat().st_size
            rec["exists"] = True
            rec["size"] = sz
            if sz == 0:
                zero_size.append(fname)
        for k in ("api", "params", "fields", "rows", "sha256", "fetched_at"):
            if k not in meta:
                bad_fields.append(f"{fname}:missing:{k}")
        rec["api"] = meta.get("api")
        rec["rows"] = meta.get("rows")
        rec["fetched_at"] = meta.get("fetched_at")
        done[fname] = rec

    # Aggregates rebuilt deterministically from `done` (idempotent on resume).
    per_api_entries = {}
    fetched_min, fetched_max, row_total = {}, {}, {}
    missing_file, zero_size = [], []
    for fname, rec in done.items():
        api = rec.get("api", "?")
        per_api_entries.setdefault(api, []).append(fname)
        row_total[api] = row_total.get(api, 0) + int(rec.get("rows") or 0)
        fa = rec.get("fetched_at") or ""
        if fa:
            fetched_min[api] = min(fetched_min.get(api, fa), fa)
            fetched_max[api] = max(fetched_max.get(api, fa), fa)
        if rec.get("exists") is False:
            missing_file.append(fname)
        elif rec.get("size") == 0:
            zero_size.append(fname)

    # ---- sha256 spot check -------------------------------------------------
    rng = random.Random(seed)
    verified = details["checks"].setdefault("manifest_sha256", {})
    big = [f for f, m in files.items() if (api_dir / f).exists() and (api_dir / f).stat().st_size > 50 * 1024 * 1024]
    small = [f for f in files if (api_dir / f).exists() and f not in big]
    sample = sorted(big)
    if len(sample) > _SHA_SAMPLE_MIN_BIG:
        sample = sorted(rng.sample(big, _SHA_SAMPLE_MIN_BIG))
    sample += sorted(rng.sample(small, min(_SHA_SAMPLE_MIN_SMALL, len(small))))
    sha_bad: List[str] = []
    for fname in sample:
        if fname in verified:
            if not verified[fname].get("ok"):
                sha_bad.append(fname)
            continue
        actual = _sha256_file(api_dir / fname)
        ok = actual == files[fname]["sha256"]
        verified[fname] = {"ok": ok, "sha256_actual": actual}
        if not ok:
            sha_bad.append(fname)

    # ---- row-count spot check ----------------------------------------------
    rows_done = details["checks"].setdefault("manifest_rows", {})
    rows_bad: List[str] = []
    for api, fnames in per_api_entries.items():
        cand = [f for f in fnames if (api_dir / f).exists() and (api_dir / f).stat().st_size <= 400 * 1024 * 1024]
        if not cand:
            continue
        pick = sorted(rng.sample(cand, min(_ROWS_SAMPLE_PER_API, len(cand))))
        for fname in pick:
            if fname in rows_done:
                if not rows_done[fname].get("ok"):
                    rows_bad.append(fname)
                continue
            try:
                df = pd.read_pickle(api_dir / fname)
                n = int(len(df))
            except Exception as e:  # unreadable -> count as mismatch, note error
                rows_done[fname] = {"ok": False, "error": str(e)[:120]}
                rows_bad.append(fname)
                continue
            ok = n == int(files[fname]["rows"])
            rows_done[fname] = {"ok": ok, "rows_actual": n, "rows_manifest": int(files[fname]["rows"])}
            if not ok:
                rows_bad.append(fname)

    return {
        "entries_total": len(files),
        "entries_checked": len(done),
        "missing_file": missing_file,
        "zero_size": zero_size,
        "bad_fields": bad_fields,
        "per_api_count": {a: len(v) for a, v in sorted(per_api_entries.items())},
        "per_api_rows": row_total,
        "fetched_at_min": fetched_min,
        "fetched_at_max": fetched_max,
        "sha256_sampled": len(verified),
        "sha256_bad": sorted(set(sha_bad)),
        "rows_sampled": len(rows_done),
        "rows_bad": sorted(set(rows_bad)),
    }


def check_calendar(cache: Path) -> Dict[str, Any]:
    """Open-day list from the three trade_cal files; return canonical list."""
    api_dir = cache / "api_cache"
    manifest = json.loads((cache / "manifest.json").read_text())
    cals = [k for k, v in manifest["files"].items() if v["api"] == "trade_cal"]
    out = {}
    best_days: List[str] = []
    for k in sorted(cals):
        df = pd.read_pickle(api_dir / k)
        df = df[df["is_open"] == 1].copy()
        df["d"] = pd.to_datetime(df["cal_date"]).dt.strftime("%Y-%m-%d")
        days = sorted(df["d"].tolist())
        out[k] = {
            "rows": int(len(df)),
            "first": days[0] if days else None,
            "last": days[-1] if days else None,
            "n_open": len(days),
        }
        if len(days) > len(best_days):
            best_days = days
    # canonical calendar = the widest one; must cover the study window
    in_window = [d for d in best_days if STUDY_WINDOW_START <= d <= STUDY_WINDOW_END]
    return {"files": out, "trade_days_in_window": in_window, "n_in_window": len(in_window)}


def _list_days(dirpath: Path) -> List[str]:
    return sorted(p.stem for p in dirpath.glob("*.pkl"))


def check_by_day(cache: Path, api: str, trade_days: List[str]) -> Dict[str, Any]:
    """Check daily_by_day / daily_basic_by_day completeness vs open days."""
    dirpath = cache / f"{api}_by_day"
    have = set(_list_days(dirpath))
    want = set(trade_days)
    missing = sorted(want - have)
    extra = sorted(have - want)
    return {
        "files_on_disk": len(have),
        "expected_days": len(want),
        "missing_days": missing,
        "extra_days": extra,
        "first": min(have) if have else None,
        "last": max(have) if have else None,
    }


def check_by_day_rows(cache: Path, api: str, manifest: Dict[str, Any],
                      trade_days: List[str], details: Dict[str, Any], seed: int,
                      sample_n: int = 30) -> Dict[str, Any]:
    """Compare by-day file row counts to the corresponding manifest entries."""
    done = details["checks"].setdefault(f"{api}_by_day_rows", {})
    api_dir = cache / "api_cache"
    # map trade_date -> manifest file name
    m: Dict[str, str] = {}
    for fname, meta in manifest["files"].items():
        if meta["api"] == api:
            td = meta["params"].get("trade_date", "")
            if td:
                m[f"{td[:4]}-{td[4:6]}-{td[6:]}"] = fname
    rng = random.Random(seed)
    days = sorted(trade_days)
    pick = sorted(rng.sample(days, min(sample_n, len(days))))
    bad = []
    checked = 0
    for d in pick:
        if d in done:
            if not done[d].get("ok"):
                bad.append(d)
            checked += 1
            continue
        fp = cache / f"{api}_by_day" / f"{d}.pkl"
        mf = m.get(d)
        if not fp.exists() or mf is None or not (api_dir / mf).exists():
            done[d] = {"ok": False, "reason": "missing file or manifest entry"}
            bad.append(d)
            continue
        n_disk = int(len(pd.read_pickle(fp)))
        n_api = int(len(pd.read_pickle(api_dir / mf)))
        ok = n_disk == n_api == int(manifest["files"][mf]["rows"])
        done[d] = {"ok": ok, "disk": n_disk, "api_cache": n_api,
                   "manifest": int(manifest["files"][mf]["rows"])}
        if not ok:
            bad.append(d)
        checked += 1
    return {"sampled": checked, "bad": sorted(set(bad)), "detail": {d: done[d] for d in pick}}


def check_symbol_dir_vs_all(
    cache: Path,
    dirname: str,
    all_pkl: str,
    key_cols: List[str],
    manifest: Dict[str, Any],
    api_name: Optional[str] = None,
    sample: int = 40,
    seed: int = 0,
) -> Dict[str, Any]:
    """Per-symbol dir vs consolidated *_all.pkl; row parity + content sample."""
    dirpath = cache / dirname
    files = sorted(dirpath.glob("*.pkl"))
    symbols = [p.stem for p in files]
    out: Dict[str, Any] = {"dir_files": len(files), "dir_symbols": len(set(symbols))}

    allpath = cache / all_pkl
    if allpath.exists():
        # stream the big concat file in pieces by symbol to keep RAM low
        df_all = pd.read_pickle(allpath)
        out["all_rows"] = int(len(df_all))
        out["all_symbols"] = int(df_all["ts_code"].nunique())
        counts = df_all.groupby("ts_code").size().to_dict()
        del df_all
    else:
        out["all_rows"] = None
        out["all_symbols"] = None
        counts = {}

    # row parity per symbol (cheap: read each small file once)
    mismatch = []
    total_dir_rows = 0
    rng = random.Random(seed)
    for sym in symbols:
        try:
            n = int(len(pd.read_pickle(dirpath / f"{sym}.pkl")))
        except Exception as e:
            mismatch.append({"symbol": sym, "error": str(e)[:100]})
            continue
        total_dir_rows += n
        if counts and counts.get(sym) != n:
            mismatch.append({"symbol": sym, "dir": n, "all": counts.get(sym)})
    out["total_dir_rows"] = total_dir_rows
    out["row_mismatch"] = mismatch[:50]
    out["n_row_mismatch"] = len(mismatch)

    # manifest cross-check for the underlying api
    if api_name:
        expected_syms = {
            meta["params"]["ts_code"]
            for meta in manifest["files"].values()
            if meta["api"] == api_name
        }
        out["manifest_symbols"] = len(expected_syms)
        out["dir_minus_manifest"] = sorted(set(symbols) - expected_syms)[:50]
        out["manifest_minus_dir"] = sorted(expected_syms - set(symbols))[:50]
    return out


def check_index_member(cache: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """index_member_all/ dir vs index_member_all_all.pkl; con_code gap check."""
    dirpath = cache / "index_member_all"
    symbols = sorted(p.stem for p in dirpath.glob("*.pkl"))
    res: Dict[str, Any] = {"dir_symbols": len(symbols)}

    allp = cache / "index_member_all_all.pkl"
    if allp.exists():
        df = pd.read_pickle(allp)
        res["all_rows"] = int(len(df))
        res["all_symbols"] = int(df["ts_code"].nunique())
        res["is_new_dist"] = df["is_new"].value_counts().to_dict() if "is_new" in df else {}
        res["l1_null"] = int(df["l1_code"].isna().sum()) if "l1_code" in df else None
        res["has_con_code_col"] = "con_code" in df.columns
        # per-symbol frame equality on a sample
        rng = random.Random(3)
        bad = []
        for sym in rng.sample(symbols, min(30, len(symbols))):
            d = pd.read_pickle(dirpath / f"{sym}.pkl")
            a = df[df["ts_code"] == sym]
            if len(d) != len(a):
                bad.append({"symbol": sym, "dir": len(d), "all": len(a)})
        res["symbol_row_mismatch_sample30"] = bad

    # manifest says every symbol got both is_new Y and N calls
    ima = manifest["files"]
    y_syms = {v["params"]["ts_code"] for v in ima.values() if v["api"] == "index_member_all" and v["params"].get("is_new") == "Y"}
    n_syms = {v["params"]["ts_code"] for v in ima.values() if v["api"] == "index_member_all" and v["params"].get("is_new") == "N"}
    res["manifest_Y"] = len(y_syms)
    res["manifest_N"] = len(n_syms)
    res["missing_Y"] = sorted(set(symbols) - y_syms)[:50]
    res["missing_N"] = sorted(set(symbols) - n_syms)[:50]
    return res


def check_sw_daily(cache: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    cls = pd.read_pickle(cache / "index_classify.pkl")
    want_codes = set(cls["index_code"])
    sw = pd.read_pickle(cache / "sw_daily.pkl")
    have_codes = set(sw["ts_code"])
    per = sw.groupby("ts_code").size().to_dict()
    # manifest rows per code
    m_rows = {}
    api_dir = cache / "api_cache"
    for fname, meta in manifest["files"].items():
        if meta["api"] == "sw_daily":
            m_rows[meta["params"]["ts_code"]] = (fname, int(meta["rows"]))
    bad = {}
    for code, (fname, mrows) in m_rows.items():
        actual = per.get(code, 0)
        # cross-check the api_cache file itself
        n_api = int(len(pd.read_pickle(api_dir / fname))) if (api_dir / fname).exists() else -1
        if actual != mrows or n_api != mrows:
            bad[code] = {"manifest": mrows, "consolidated": actual, "api_cache": n_api}
    return {
        "index_classify_codes": len(want_codes),
        "sw_daily_codes": len(have_codes),
        "missing_codes": sorted(want_codes - have_codes),
        "extra_codes": sorted(have_codes - want_codes),
        "rows_per_code_min": min(per.values()) if per else 0,
        "rows_per_code_max": max(per.values()) if per else 0,
        "manifest_vs_data_bad": bad,
        "total_rows": int(len(sw)),
    }


def check_index_weight(cache: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    api_dir = cache / "api_cache"
    for code, fname in (("000300.SH", "index_weight_000300.SH.pkl"),
                        ("000905.SH", "index_weight_000905.SH.pkl")):
        p = cache / fname
        rec: Dict[str, Any] = {}
        if p.exists():
            df = pd.read_pickle(p)
            rec["rows"] = int(len(df))
            rec["dates_min"] = str(df["trade_date"].min())
            rec["dates_max"] = str(df["trade_date"].max())
            rec["n_dates"] = int(df["trade_date"].nunique())
            rec["n_con"] = int(df["con_code"].nunique())
            rec["weight_null"] = int(df["weight"].isna().sum())
        # manifest counterpart
        mrow = next((v for v in manifest["files"].values()
                     if v["api"] == "index_weight" and v["params"].get("index_code") == code), None)
        rec["manifest_rows"] = int(mrow["rows"]) if mrow else None
        rec["manifest_file"] = next((k for k, v in manifest["files"].items()
                                     if v["api"] == "index_weight" and v["params"].get("index_code") == code), None)
        out[code] = rec
    return out


def check_stock_basic(cache: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    df = pd.read_pickle(cache / "stock_basic.pkl")
    dist = df["list_status"].value_counts().to_dict()
    m = {}
    for f, v in manifest["files"].items():
        if v["api"] == "stock_basic":
            m[v["params"]["list_status"]] = int(v["rows"])
    return {
        "rows": int(len(df)),
        "list_status": dist,
        "manifest": m,
        "exchange_dist": df["exchange"].value_counts().to_dict(),
        "bj_count": int(df["ts_code"].str.endswith(".BJ").sum()),
        "list_date_min": str(df["list_date"].min()),
        "list_date_max": str(df["list_date"].max()),
    }


def check_daily_all(cache: Path, trade_days: List[str]) -> Dict[str, Any]:
    """daily_all.pkl / daily_basic_all.pkl vs by-day counts."""
    out = {}
    for api in ("daily", "daily_basic"):
        p = cache / f"{api}_all.pkl"
        rec: Dict[str, Any] = {}
        if p.exists():
            df = pd.read_pickle(p)
            rec["rows"] = int(len(df))
            rec["n_dates"] = int(df["trade_date"].nunique())
            rec["n_symbols"] = int(df["ts_code"].nunique())
            have = set(pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"))
            want = set(trade_days)
            rec["missing_days_vs_cal"] = sorted(want - have)
            rec["extra_days_vs_cal"] = sorted(have - want)
            del df
        # by-day total
        dirpath = cache / f"{api}_by_day"
        tot = 0
        for fp in dirpath.glob("*.pkl"):
            tot += int(len(pd.read_pickle(fp)))
        rec["dir_rows"] = tot
        out[api] = rec
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.expanduser("~/Documents/TradingAgents-AShare-cache/phase2"))
    ap.add_argument("--details", required=True, help="JSON checkpoint output (in workdir)")
    ap.add_argument("--report", default="", help="optional markdown summary path")
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--skip-big-concat", action="store_true",
                    help="skip *_all.pkl full-file loads (still checks dir-level counts)")
    args = ap.parse_args()

    cache = Path(args.cache).expanduser()
    if not cache.is_dir():
        print(f"cache dir not found: {cache}", file=sys.stderr)
        return 2

    details_path = Path(args.details).expanduser()
    details = _load_details(details_path)
    t0 = time.time()

    manifest = json.loads((cache / "manifest.json").read_text())

    section_times: Dict[str, float] = {}

    def timed(name, fn, *a, **kw):
        s = time.time()
        r = fn(*a, **kw)
        section_times[name] = round(time.time() - s, 1)
        return r

    cal = timed("calendar", check_calendar, cache)
    trade_days = cal["trade_days_in_window"]

    manifest_res = timed("manifest", check_manifest, cache, details, args.seed)

    res = {
        "cache_dir": str(cache),
        "window": [STUDY_WINDOW_START, STUDY_WINDOW_END],
        "trade_days_in_window": len(trade_days),
        "calendar": cal,
        "manifest": manifest_res,
        "daily_by_day": timed("daily_by_day", check_by_day, cache, "daily", trade_days),
        "daily_basic_by_day": timed("daily_basic_by_day", check_by_day, cache, "daily_basic", trade_days),
        "daily_by_day_rows": timed("daily_by_day_rows", check_by_day_rows, cache, "daily", manifest, trade_days, details, args.seed),
        "daily_basic_by_day_rows": timed("daily_basic_by_day_rows", check_by_day_rows, cache, "daily_basic", manifest, trade_days, details, args.seed),
        "adj_factor": timed("adj_factor", check_symbol_dir_vs_all, cache, "adj_factor", "adj_factor_all.pkl",
                            ["ts_code", "trade_date", "adj_factor"], manifest, "adj_factor"),
        "index_member_all": timed("index_member_all", check_index_member, cache, manifest),
        "sw_daily": timed("sw_daily", check_sw_daily, cache, manifest),
        "index_weight": timed("index_weight", check_index_weight, cache, manifest),
        "stock_basic": timed("stock_basic", check_stock_basic, cache, manifest),
    }
    if not args.skip_big_concat:
        res["consolidated_all"] = timed("consolidated_all", check_daily_all, cache, trade_days)

    runtime = {
        "wall_seconds": round(time.time() - t0, 1),
        "section_seconds": section_times,
        "peak_rss_gb": round(_peak_rss_gb(), 3),
        "python": sys.version.split()[0],
        "seed": args.seed,
    }
    details["result"] = res
    details["runtime"] = runtime
    _save_details(details_path, details)
    print(json.dumps({"ok": True, "details": str(details_path), "runtime": runtime}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
