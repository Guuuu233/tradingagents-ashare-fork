#!/usr/bin/env python3
"""DAV-1481 P5: mid-term read-only weekly report pipeline.

Reads the P4 forward ledger (``work/phase2-ledger/forward_ledger.jsonl``,
gitignored — pass ``--ledger`` when it lives elsewhere) plus a real or fixture
SQLite DB (``file:...?mode=ro``, never the write path) and emits one
deterministic Markdown report per run under ``work/phase2-weekly/``.

Weekly file naming is anchored on the CN trading calendar — the last trading
day of the calendar week (Mon-Fri) containing the report date — NOT on the
wall-clock day the command happens to run. A Friday evening run and a Monday
catch-up run for the same week produce the same file. Re-running the same
command on unchanged inputs is byte-identical by contract: the artifact is
fully derived from (ledger, label files, benchmark file), and the header
carries a SHA-256 digest over the analysis-relevant inputs (ledger records,
labels, benchmarks, code version) so "same inputs" is verifiable.

Read-only / no-report-body guarantees:
* production DB access is ``file:...?mode=ro`` only;
* only the columns needed for risk-status bookkeeping are selected — report
  text (markdown/news/psychology/social/risk fields) is never read;
* output contains aggregates, identifiers and hashes only, never report-body
  content or vendor raw rows (D-040).

"Matured" is defined by the CN trading calendar, not by the ledger
timing_class alone: a record counts as matured when its T+40 outcome date
exists in the calendar and is strictly earlier than ``--as-of``. This keeps
the report honest for backfilled H records (whose outcome was long past at
seal time) and for F0/F1 records whose window has since elapsed.

Sections (issue order):
 0. Sample sufficiency banner — pinned first per issue §7.
 1. Direction distribution, per version queue.
 2. Matured sample counts, timing_class x input_pit_status; only
    F0+VERIFIED counts as "formal", everything else "research/diagnostic".
 3. Daily cross-sectional statistic series — BOTH Spearman rank IC and
    top-bottom bucket spread, flagged 未冻结 until P1 freezes
    primary_cross_sectional_metric / min_daily_cross_section_n; days below
    the floor are ``insufficient``.
 4. Label-return side-by-side, 行业相对 vs 沪深300 — provisional caliber
    (hs300) only until P2 lands; sw_industry column is explicit "P2 未合入".
 5. Risk-gate descriptive: BLOCKED vs passed cohorts, matured vs pending.
 6. Style exposure — placeholder until P1 style factors land.
 7. Report body guarantee statement.

Usage:
  python scripts/phase2/weekly_report.py run [options]

Options:
  --ledger PATH       forward_ledger.jsonl (default work/phase2-ledger/…)
  --db PATH           SQLite DB opened mode=ro (default data/tradingagents.db)
  --labels PATH       T+40 label JSONL for the provisional caliber
                      (default work/phase2-labels/labels_t40.jsonl)
  --sw-labels PATH    industry-relative labels once P2 lands (default
                      work/phase2-labels/labels_t40_sw.jsonl)
  --benchmarks PATH   index close JSONL keyed rows {"symbol","date","close"}
                      (default work/phase2-weekly/index_closes.jsonl)
  --benchmark-symbols 000300.SH
                      index symbols to include; if --benchmarks is absent the
                      file can be fetched via akshare (stock_zh_index_daily_tx
                      -> index_zh_a_hist -> stock_zh_index_daily_em) into
                      --benchmarks-dir (default work/phase2-weekly) when
                      --fetch-benchmarks is passed; otherwise sections 3/4
                      degrade without touching the network.
  --min-cross-n N     daily cross-section floor (default 5 — provisional,
                      P1 owns the freeze)
  --min-total-n N     overall sufficiency floor for the banner (default 40
                      formal samples — provisional until P1's minimum
                      effective information requirement lands)
  --as-of YYYY-MM-DD  report as-of date (default: today)
  --out-dir DIR       output dir (default work/phase2-weekly/)
  --out PATH          write to an explicit file instead of
                      <out-dir>/weekly_report_<YYYY-MM-DD>.md (tests)

Fail-close: missing/empty ledger, corrupt JSONL, unparseable record shape
all exit non-zero — the report never silently emits a "0 samples" week.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Import the sibling ledger script so record-shape helpers stay single-sourced.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_snapshot_ledger as dsl  # noqa: E402

PIPELINE_VERSION = "weekly_report.v1"
DEFAULT_DB = REPO_ROOT / "data" / "tradingagents.db"
DEFAULT_LEDGER = REPO_ROOT / "work" / "phase2-ledger" / "forward_ledger.jsonl"
DEFAULT_LABELS = REPO_ROOT / "work" / "phase2-labels" / "labels_t40.jsonl"
DEFAULT_SW_LABELS = REPO_ROOT / "work" / "phase2-labels" / "labels_t40_sw.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "work" / "phase2-weekly"
DEFAULT_BENCH_DIR = REPO_ROOT / "work" / "phase2-weekly"
DEFAULT_BENCH_FILE = "index_closes.jsonl"
DEFAULT_BENCH_SYMBOLS = ("000300.SH",)

# Provisional floors — P1 freezes primary_cross_sectional_metric and
# min_daily_cross_section_n before M2; until then both IC and bucket spread
# are shown side-by-side marked 未冻结 (issue §3 / plan v1.0 §6).
DEFAULT_MIN_CROSS_N = 5
DEFAULT_MIN_TOTAL_N = 40

MEDIUM = "medium"

_CN_OFFSET = timedelta(hours=8)


# ---------------------------------------------------------------------------
# tiny utils


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _parse_d(s: Any) -> Optional[date]:
    if s is None:
        return None
    txt = str(s).strip()[:10]
    try:
        return date.fromisoformat(txt)
    except ValueError:
        return None


def _mean(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    return round(sum(xs) / len(xs), 4)


def _fmt(x: Optional[float], digits: int = 4) -> str:
    if x is None:
        return "—"
    return f"{x:.{digits}f}"


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation; ties -> average rank; None if undefined."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None

    def ranks(vals: Sequence[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return None
    return round(cov / (vx * vy) ** 0.5, 4)


# ---------------------------------------------------------------------------
# ledger IO


def _load_ledger(path: Path) -> list[dict[str, Any]]:
    """Fail-close JSONL read: any unreadable line aborts the run."""
    if not path.exists():
        raise FileNotFoundError(f"ledger not found: {path}")
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"ledger line {lineno} unreadable: {exc}") from exc
            rec = obj.get("record")
            if not isinstance(rec, dict):
                raise ValueError(f"ledger line {lineno}: record missing")
            rec["_line"] = lineno
            records.append(rec)
    if not records:
        raise ValueError(f"ledger empty: {path}")
    return records


# ---------------------------------------------------------------------------
# DB (mode=ro, risk_status only)


def _load_risk_status(db_path: Path,
                      report_ids: Iterable[str]) -> tuple[dict[str, str], str]:
    """report_id -> risk_status, read-only.

    Returns (mapping, note): note is "" on success, else a short reason the
    caller prints in section 5 — distinguishes "DB missing" from "DB read
    but no matching ids".
    """
    if not db_path.exists():
        return {}, f"db not found: {db_path}"
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        out: dict[str, str] = {}
        ids = list(dict.fromkeys(report_ids))
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = con.execute(
                f"SELECT id, risk_status FROM reports "
                f"WHERE id IN ({','.join('?' * len(chunk))})", chunk
            ).fetchall()
            for rid, rs in rows:
                out[str(rid)] = str(rs) if rs is not None else "UNKNOWN"
        return out, ""
    finally:
        con.close()


# ---------------------------------------------------------------------------
# labels (provisional hs300 caliber) + benchmarks


def _load_label_file(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """(symbol, signal_date) -> label dict. Missing file -> {}."""
    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"label file {path} line {lineno} unreadable: {exc}") from exc
            sym = obj.get("symbol")
            sig = obj.get("signal_date") or obj.get("trade_date")
            if sym and sig:
                out[(str(sym), str(sig)[:10])] = obj
    return out


def _label_return(obj: Mapping[str, Any]) -> Optional[float]:
    for k in ("return_pct", "hs300_excess_pct", "excess_return_pct"):
        v = obj.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _fetch_index_closes(symbols: Sequence[str], out_dir: Path) -> Path:
    """Fetch daily index closes via akshare -> JSONL {symbol, date, close}.

    Sources tried in the same order as cn_akshare_provider: tx -> eastmoney
    hist -> em daily. Offline by default — only called when the operator
    passes --fetch-benchmarks.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / DEFAULT_BENCH_FILE
    try:
        import akshare as ak  # noqa: WPS433
    except Exception as exc:
        raise RuntimeError(f"akshare unavailable: {exc}") from exc

    frames = []
    for sym in symbols:
        df = None
        try:
            if hasattr(ak, "stock_zh_index_daily_tx"):
                em = ("sh" if sym.endswith(".SH") else "sz") + sym.split(".")[0]
                df = ak.stock_zh_index_daily_tx(symbol=em)
        except Exception:
            df = None
        if df is None or getattr(df, "empty", True):
            try:
                if hasattr(ak, "index_zh_a_hist"):
                    df = ak.index_zh_a_hist(
                        symbol=sym.split(".")[0], period="daily")
            except Exception:
                df = None
        if df is None or getattr(df, "empty", True):
            try:
                if hasattr(ak, "stock_zh_index_daily_em"):
                    df = ak.stock_zh_index_daily_em(symbol=sym.lower())
            except Exception:
                df = None
        if df is None or getattr(df, "empty", True):
            raise RuntimeError(f"index daily fetch failed for {sym}")
        frames.append((sym, df))

    with open(path, "w", encoding="utf-8") as fh:
        for sym, df in frames:
            date_col = next(
                (c for c in df.columns
                 if str(c).lower() in ("date", "trade_date", "日期")), None)
            close_col = next(
                (c for c in df.columns
                 if str(c).lower() in ("close", "收盘")), None)
            if date_col is None or close_col is None:
                raise RuntimeError(
                    f"index daily schema unexpected for {sym}: "
                    f"{list(df.columns)[:8]}")
            for _, row in df.iterrows():
                d = str(row[date_col])[:10]
                try:
                    c = float(row[close_col])
                except (TypeError, ValueError):
                    continue
                fh.write(json.dumps({"symbol": sym, "date": d, "close": c},
                                    ensure_ascii=False) + "\n")
    return path


def _load_benchmark_closes(path: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            sym, d, c = obj.get("symbol"), obj.get("date"), obj.get("close")
            if sym and d and isinstance(c, (int, float)):
                out.setdefault(str(sym), {})[str(d)[:10]] = float(c)
    return out


def _benchmark_return(closes: Mapping[str, float],
                      trade_dates: list[date],
                      signal: date,
                      offset: int) -> Optional[float]:
    """Index % return over the same T+1 -> T+offset trading-day window.

    Close-to-close on trading-day anchors (signal+1 -> signal+offset);
    documented as the provisional 沪深300 caliber until P2 supplies the
    industry-relative leg.
    """
    idx = bisect.bisect_right(trade_dates, signal) - 1
    e_i, t_i = idx + 1, idx + offset
    if idx < 0 or e_i >= len(trade_dates) or t_i >= len(trade_dates):
        return None
    e_d, t_d = trade_dates[e_i].isoformat(), trade_dates[t_i].isoformat()
    e_c, t_c = closes.get(e_d), closes.get(t_d)
    if not e_c or not t_c or e_c <= 0:
        return None
    return round((t_c - e_c) / e_c * 100.0, 4)


# ---------------------------------------------------------------------------
# classification helpers


def _version_key(rec: Mapping[str, Any]) -> str:
    v = rec.get("versions") or {}
    commit = v.get("generated_by_commit_sha")
    if isinstance(commit, str) and commit:
        return "commit:" + commit[:12]
    ri = v.get("run_identity")
    if isinstance(ri, dict):
        c = ri.get("commit_sha")
        if isinstance(c, str) and c:
            return "commit:" + c[:12]
    if isinstance(ri, str) and ri:
        return "run_identity:" + ri[:12]
    return "unversioned"


_DIRECTION_BUCKETS = (
    ("看多", ("看多", "BUY", "BULL", "增持", "买入")),
    ("看空", ("看空", "SELL", "BEAR", "减持", "卖出")),
    ("中性", ("中性", "NEUTRAL", "HOLD", "持有")),
)


def _direction_bucket(raw: Any) -> str:
    if raw is None:
        return "无结论"
    s = str(raw).strip().upper()
    if not s:
        return "无结论"
    for label, keys in _DIRECTION_BUCKETS:
        if any(k.upper() in s for k in keys):
            return label
    return "无结论"


def _risk_bucket(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if not s:
        return "UNKNOWN"
    if "BLOCK" in s or "REJECT" in s or "拦截" in s or "否决" in s:
        return "BLOCKED"
    return "PASSED_OR_OTHER"


# ---------------------------------------------------------------------------
# markdown


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def _record_matured_outcome_day(rec: Mapping[str, Any],
                                trade_dates: list[date]) -> Optional[date]:
    """T+40 trading day for this record's signal_date, or None if unresolved."""
    sig = _parse_d(rec.get("signal_date"))
    if sig is None:
        return None
    idx = bisect.bisect_right(trade_dates, sig) - 1
    t_i = idx + dsl.OUTCOME_OFFSET_TRADING_DAYS
    if idx < 0 or t_i >= len(trade_dates):
        return None
    return trade_dates[t_i]


def build_report(
    *,
    records: list[dict[str, Any]],
    risk_status: Mapping[str, str],
    risk_note: str = "",
    labels: Mapping[tuple[str, str], dict[str, Any]],
    sw_labels: Mapping[tuple[str, str], dict[str, Any]],
    bench_closes: Mapping[str, Mapping[str, float]],
    bench_symbols: Sequence[str],
    trade_dates: list[date],
    as_of: date,
    min_cross_n: int,
    min_total_n: int,
    input_digest: str,
) -> str:
    """Pure function over the analysis inputs -> deterministic markdown."""
    as_of_s = as_of.isoformat()

    # --- enrich + split ------------------------------------------------------
    medium: list[dict[str, Any]] = []
    for r in records:
        j = (r.get("horizons") or {}).get(MEDIUM)
        if not isinstance(j, dict):
            continue
        r["_m_dir"] = _direction_bucket(j.get("direction"))
        prob = j.get("probability")
        r["_m_prob"] = float(prob) if isinstance(prob, (int, float)) else None
        r["_sig"] = _parse_d(r.get("signal_date"))
        r["_outcome_day"] = _record_matured_outcome_day(r, trade_dates)
        r["_matured"] = (r["_outcome_day"] is not None
                         and r["_outcome_day"] < as_of)
        medium.append(r)

    matured = [r for r in medium if r["_matured"]]
    immature = [r for r in medium if not r["_matured"]]

    # "formal" = matured + sealed F0 (before entry open) + VERIFIED inputs +
    # not a multi-week backfill row.
    formal = [r for r in matured
              if r.get("timing_class") == "F0"
              and r.get("input_pit_status") == "VERIFIED"
              and not r.get("backfilled")]
    research = [r for r in matured if r not in formal]

    # --- section 1: direction distribution per version queue
    dir_rows = []
    queues: dict[str, list[dict]] = {}
    for r in medium:
        queues.setdefault(_version_key(r), []).append(r)
    for q in sorted(queues):
        c = Counter(r["_m_dir"] for r in queues[q])
        tot = sum(c.values())
        dir_rows.append((q, tot, c.get("看多", 0), c.get("看空", 0),
                         c.get("中性", 0), c.get("无结论", 0)))

    # --- section 2: matured timing x pit cross
    pit_map: dict[str, Counter] = {}
    for r in matured:
        tc = r.get("timing_class") or "UNCLASSIFIED"
        pit_map.setdefault(tc, Counter())[
            r.get("input_pit_status") or "MISSING"] += 1
    pit_rows = []
    for tc in sorted(pit_map):
        c = pit_map[tc]
        formal_n = c.get("VERIFIED", 0) if tc == "F0" else 0
        research_n = sum(c.values()) - formal_n
        pit_rows.append((tc, c.get("VERIFIED", 0), c.get("UNVERIFIED", 0),
                         c.get("FAILED", 0), c.get("MISSING", 0),
                         formal_n, research_n))

    # --- section 3: daily cross-sectional series (provisional, 未冻结)
    def _ret_for(r: Mapping[str, Any]) -> Optional[float]:
        sig = r.get("_sig")
        if sig is None:
            return None
        lab = labels.get((r.get("symbol"), sig.isoformat()))
        if lab:
            v = _label_return(lab)
            if v is not None:
                return v
        if bench_closes and bench_symbols:
            first = bench_closes.get(bench_symbols[0], {})
            return _benchmark_return(first, trade_dates, sig,
                                     dsl.OUTCOME_OFFSET_TRADING_DAYS)
        return None

    daily: dict[date, list[tuple[float, float]]] = {}
    for r in matured:
        sig = r.get("_sig")
        if sig is None or r["_m_prob"] is None:
            continue
        ret = _ret_for(r)
        if ret is not None:
            daily.setdefault(sig, []).append((r["_m_prob"], ret))

    day_rows = []
    ic_series: list[float] = []
    spread_series: list[float] = []
    insufficient_days = 0
    for d in sorted(daily):
        pairs = daily[d]
        n = len(pairs)
        if n < min_cross_n:
            insufficient_days += 1
            day_rows.append((d.isoformat(), n, "insufficient", "—", "—"))
            continue
        probs = [p for p, _ in pairs]
        rets = [v for _, v in pairs]
        ic = _spearman(probs, rets)
        if ic is not None:
            ic_series.append(ic)
        ordered = sorted(pairs, key=lambda p: p[0])
        k = max(1, n // 3)
        lo = [v for _, v in ordered[:k]]
        hi = [v for _, v in ordered[-k:]]
        spread = None
        if lo and hi:
            spread = round(sum(hi) / len(hi) - sum(lo) / len(lo), 4)
            spread_series.append(spread)
        day_rows.append((d.isoformat(), n, "ok", _fmt(ic), _fmt(spread)))

    # --- section 4: returns side-by-side
    lab_rets = [
        v for r in matured
        if (v := _label_return(labels.get(
            (r.get("symbol"),
             (r.get("_sig") or date.min).isoformat()), {}))) is not None
    ]
    sw_rets = [
        v for r in matured
        if (v := _label_return(sw_labels.get(
            (r.get("symbol"),
             (r.get("_sig") or date.min).isoformat()), {}))) is not None
    ]
    bench_rets: list[float] = []
    if bench_closes and bench_symbols:
        first = bench_closes.get(bench_symbols[0], {})
        for r in matured:
            sig = r.get("_sig")
            if sig:
                v = _benchmark_return(first, trade_dates, sig,
                                      dsl.OUTCOME_OFFSET_TRADING_DAYS)
                if v is not None:
                    bench_rets.append(v)
    ret_rows = [(
        "成熟样本", len(matured), len(lab_rets), _fmt(_mean(lab_rets)),
        len(sw_rets), _fmt(_mean(sw_rets)), len(bench_rets),
        _fmt(_mean(bench_rets)))]

    # --- section 5: risk gate descriptive
    # Prefer DB risk_status (authoritative); fall back to the ledger's own
    # risk_status_top when the DB lookup produced nothing (e.g. the ledger
    # was copied off-host). Explicitly flagged in the rendered note.
    gate_groups: dict[str, dict[str, int]] = {}
    used_db_risk = False
    for r in medium:
        db_v = risk_status.get(r.get("report_id"))
        if db_v is not None:
            used_db_risk = True
        rs = db_v if db_v is not None else str(
            r.get("risk_status_top") or "UNKNOWN")
        b = _risk_bucket(rs)
        g = gate_groups.setdefault(
            b, {"total": 0, "matured": 0, "pending": 0})
        g["total"] += 1
        g["matured" if r["_matured"] else "pending"] += 1
    gate_rows = [(b, g["total"], g["matured"], g["pending"])
                 for b, g in sorted(gate_groups.items())]
    risk_source = ("db" if used_db_risk
                   else "ledger risk_status_top（DB 未命中，降级）")

    sufficient = len(formal) >= min_total_n

    out: list[str] = []
    out.append(f"<!-- pipeline={PIPELINE_VERSION} as_of={as_of_s} "
               f"inputs_sha256={input_digest} -->")
    out.append("")
    out.append("## 0. 样本量与结论判定")
    out.append("")
    out.append(f"- 中线账本记录总数：**{len(medium)}**")
    out.append(f"- 已到期（T+40 结果可知日 < as_of）样本：**{len(matured)}**")
    out.append(f"- 未到期：**{len(immature)}**")
    out.append(f"- 正式栏样本（到期 + 封存时 F0 + VERIFIED + 非回填）："
               f"**{len(formal)}**")
    out.append(f"- 判定阈值（min_total_n，P1 冻结前临时口径）："
               f"**{min_total_n}**")
    out.append("")
    if sufficient:
        out.append(f"> ✅ 正式样本量 {len(formal)} ≥ {min_total_n}，"
                   "可以开始解读指标。")
    else:
        out.append(f"> ⚠️ **样本不足，不下结论**（正式样本 {len(formal)} < "
                   f"{min_total_n}）。以下所有指标仅作管线跑通验证。")
    out.append("")
    out.append("---")
    out.append("")

    out.append("## 1. 中线方向分布（按版本队列）")
    out.append("")
    out.append(_md_table(
        ("版本队列", "总数", "看多", "看空", "中性", "无结论"), dir_rows))
    out.append("")

    out.append("## 2. 已到期样本数 timing_class × input_pit_status")
    out.append("")
    out.append(_md_table(
        ("timing_class", "VERIFIED", "UNVERIFIED", "FAILED", "MISSING",
         "正式栏(F0+VERIFIED)", "研究/诊断栏"), pit_rows))
    out.append("")
    out.append(f"- 正式栏合计：**{len(formal)}**；研究/诊断栏合计："
               f"**{len(research)}**。")
    out.append("")

    out.append("## 3. 日级横截面统计量（未冻结口径，P1 裁定前双列）")
    out.append("")
    if day_rows:
        out.append(_md_table(
            ("signal_date", "n", "status", "Spearman IC（未冻结）",
             "高低分组差（未冻结）"), day_rows))
        out.append("")
        out.append(f"- IC 序列均值：**{_fmt(_mean(ic_series))}**"
                   f"（{len(ic_series)} 天有效）；"
                   f"高低分组差均值：**{_fmt(_mean(spread_series))}**"
                   f"（{len(spread_series)} 天有效）。")
        out.append(f"- insufficient 天数（n < {min_cross_n}）："
                   f"**{insufficient_days}**。")
    else:
        out.append("无可用日序列（成熟样本无标签或无基准数据）。")
    out.append("")

    out.append("## 4. 收益口径并列（P2 未合入：沪深300 为临时口径，"
               "行业相对列留空待 P2）")
    out.append("")
    out.append(_md_table(
        ("集合", "样本数", "标签收益 n", "标签收益均值",
         "行业相对 n(P2 未合入)", "行业相对均值(P2 未合入)",
         "沪深300 同窗 n", "沪深300 同窗均值"), ret_rows))
    out.append("")
    out.append("> 临时口径说明：标签文件来自 `return_labels.py` 沪深300 口径；"
               "P2 合入后本表切换为行业相对口径并注明切换日期。")
    out.append("")

    out.append("## 5. 风险层拦截档 vs 放行档（描述性）")
    out.append("")
    if risk_note:
        out.append(f"（{risk_note} — 本节降级；来源：{risk_source}）")
    elif gate_rows:
        out.append(f"来源：{risk_source}")
        out.append("")
        out.append(_md_table(
            ("风险档位", "总数", "已到期(T+40<as_of)", "未到期"), gate_rows))
    else:
        out.append("（无可用风险状态行）")
    out.append("")

    out.append("## 6. 风格暴露")
    out.append("")
    out.append("P1 风格因子未提供，本节留空待 P1 落地。")
    out.append("")

    out.append("## 7. 数据来源与只读声明")
    out.append("")
    out.append("- 账本输入：`forward_ledger.jsonl`（P4 哈希链）")
    out.append("- DB：只读 `file:...?mode=ro`，仅取 `id`,`risk_status` 两列；"
                   "不读取/输出报告正文")
    out.append(f"- pipeline：`{PIPELINE_VERSION}`；输入 digest "
               f"`{input_digest[:16]}…`；同输入重跑逐字节一致")
    out.append("")

    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# CLI


def _iso_week_anchor(d: date, trade_dates: list[date]) -> str:
    """YYYY-MM-DD of the last trading day in the Mon-Fri week containing d."""
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    wk = [t for t in trade_dates if monday <= t <= friday]
    return (wk[-1] if wk else friday).isoformat()


def _input_digest(records: list[dict[str, Any]],
                  labels: Mapping[Any, Any],
                  sw_labels: Mapping[Any, Any],
                  bench: Mapping[str, Any]) -> str:
    h = hashlib.sha256()
    for r in records:
        r2 = {k: v for k, v in r.items() if k != "_line"}
        h.update(_canon(r2))
        h.update(b"\n")
    for k in sorted(labels):
        h.update(_canon({"k": k, "v": labels[k]}))
    for k in sorted(sw_labels):
        h.update(_canon({"sw_k": k, "v": sw_labels[k]}))
    h.update(_canon(bench))
    h.update(PIPELINE_VERSION.encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# launchd plist emission (kept inside this file so the diff whitelist
# scripts/phase2/weekly_report*.py stays intact — no separate .plist file).


def _emit_plist() -> str:
    """Print a launchd plist for the weekly Friday run to stdout.

    Paths are resolved at emit time from this checkout; review before
    install: cp / launchctl bootstrap gui/$UID …
    """
    py = Path(sys.executable).resolve()
    script = Path(__file__).resolve()
    workdir = REPO_ROOT
    logdir = workdir / "work" / "phase2-weekly"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- DAV-1481 P5 weekly report. Install after 总控 sign-off:
       python scripts/phase2/weekly_report.py plist > ~/Library/LaunchAgents/com.davidliu.ta-p5-weekly.plist
       launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.davidliu.ta-p5-weekly.plist
     Runs Friday 20:30 (after the 19:45 P4 ledger seal). The report file name
     anchors on the last CN trading day of the week, so a missed Friday run
     can be re-issued the following Monday without changing the artifact. -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.davidliu.ta-p5-weekly</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>{script}</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>NO_PROXY</key>
        <string>127.0.0.1,localhost</string>
        <key>no_proxy</key>
        <string>127.0.0.1,localhost</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>20</integer><key>Minute</key><integer>30</integer></dict>
    <key>StandardOutPath</key>
    <string>{logdir}/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>{logdir}/launchd.err.log</string>
</dict>
</plist>
"""


def cmd_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    as_of = _parse_d(args.as_of) or date.today()

    try:
        trade_dates = dsl._load_trade_dates()
    except Exception as exc:
        print(f"FAILED: trade calendar unavailable: {exc}", file=sys.stderr)
        return 3

    try:
        records = _load_ledger(Path(args.ledger))
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 4

    risk_status: dict[str, str] = {}
    risk_note = ""
    try:
        risk_status, risk_note = _load_risk_status(
            Path(args.db), (r["report_id"] for r in records))
    except sqlite3.Error as exc:
        risk_note = f"risk_status load failed: {exc}"
        print(f"WARNING: {risk_note}; section 5 degraded", file=sys.stderr)

    labels = _load_label_file(Path(args.labels))
    sw_labels = _load_label_file(Path(args.sw_labels))

    bench_path = Path(args.benchmarks) if args.benchmarks else None
    if bench_path is None or not bench_path.exists():
        bench_dir = Path(args.benchmarks_dir)
        candidate = bench_dir / DEFAULT_BENCH_FILE
        if not candidate.exists() and args.fetch_benchmarks:
            try:
                candidate = _fetch_index_closes(
                    args.benchmark_symbols, bench_dir)
            except RuntimeError as exc:
                print(f"WARNING: benchmark fetch failed: {exc}; "
                      "sections 3/4 degraded", file=sys.stderr)
        bench_path = candidate
    bench_closes = (_load_benchmark_closes(bench_path)
                    if bench_path.exists() else {})

    digest = _input_digest(records, labels, sw_labels, bench_closes)
    body = build_report(
        records=records, risk_status=risk_status, risk_note=risk_note,
        labels=labels,
        sw_labels=sw_labels, bench_closes=bench_closes,
        bench_symbols=args.benchmark_symbols, trade_dates=trade_dates,
        as_of=as_of, min_cross_n=args.min_cross_n,
        min_total_n=args.min_total_n, input_digest=digest)

    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out:
        out_path = Path(args.out)
    else:
        anchor = _iso_week_anchor(as_of, trade_dates)
        out_path = out_dir / f"weekly_report_{anchor}.md"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(body, "utf-8")
    tmp.replace(out_path)
    print(f"OK: weekly report written to {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--labels", default=str(DEFAULT_LABELS),
                   help="T+40 label JSONL (provisional hs300 caliber)")
    p.add_argument("--sw-labels", default=str(DEFAULT_SW_LABELS),
                   help="industry-relative label JSONL once P2 lands")
    p.add_argument("--benchmarks", default=None,
                   help="index close JSONL (default work/phase2-weekly/"
                        "index_closes.jsonl)")
    p.add_argument("--benchmarks-dir", default=str(DEFAULT_BENCH_DIR))
    p.add_argument("--benchmark-symbols", nargs="+",
                   default=list(DEFAULT_BENCH_SYMBOLS))
    p.add_argument("--fetch-benchmarks", action="store_true",
                   help="fetch index closes via akshare when the file is "
                        "absent (default: stay offline, degrade)")
    p.add_argument("--min-cross-n", type=int, default=DEFAULT_MIN_CROSS_N)
    p.add_argument("--min-total-n", type=int, default=DEFAULT_MIN_TOTAL_N)
    p.add_argument("--as-of", default=None)
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--out", default=None)
    sub.add_parser("plist", help="emit a launchd plist for the weekly "
                                 "Friday 20:30 run on stdout")
    args = ap.parse_args()
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "plist":
        sys.stdout.write(_emit_plist())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
