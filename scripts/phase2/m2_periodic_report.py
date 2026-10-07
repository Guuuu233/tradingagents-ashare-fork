#!/usr/bin/env python3
"""DAV-1680 — M2 deterministic daily / monthly reports over the P4 ledger.

计划 v1.0 §5 M2「确定性日报、月报：成绩全部由脚本从账本计算」的实现。
报告是 (账本, 标签文件, 基准行情文件, 交易日历, 代码版本) 的纯函数：
同一输入两次运行逐字节相同（日历经 --calendar 显式注入，不触网）。

输入（全部为文件，纯读取，不写生产库、不触网）：
  --ledger      P4 前瞻账本 forward_ledger.jsonl
                （``{"record": {...}, "prev_hash": ..., "chain_hash": ...}``
                包装行或裸记录行都接受）。
  --labels      可选 T+40 标签 JSONL（每行一个对象；键 (symbol,
                signal_date)，字段名由 --label-field 单认，缺失记缺失、
                不回落其他字段）。文件不存在按空标签集处理。
  --benchmarks  可选基准 OHLC JSONL {symbol,date,open,close}。T+1 开盘缺
                失记缺失，绝不以收盘顶替（D-072 窗口口径）。
  --calendar    交易日历文件（一行一个日期，YYYYMMDD 或 YYYY-MM-DD）。
                必须给出：没有日历就无法判定「已到期」「日/月锚点」，
                此时 fail-close（绝不退化成自然日口径）。

输出：``<out-dir>/m2_daily_YYYY-MM-DD.md`` 与 ``m2_monthly_YYYY-MM.md``
（后者也可用 --monthly-out 分开指定）。

周期锚点（交易日历锚定，不用自然日）：
  * 日报覆盖**信号日落入当日锚点前一个交易日**的记录（10-12 收盘后跑
    日报，覆盖 T=10-09 的信号；该批 T+1=10-12 入场）。
  * 日报文件名锚定为**覆盖窗口内最后一个交易日**。
  * 月报覆盖信号日落入该自然月（交易日历口径）的全部记录，文件名
    ``YYYY-MM`` 锚定为该月最后一个交易日。
  * ``--as-of`` 是给重放/补跑用的锚点；不补跑同名的日报/月报历史。

「已到期」= 信号日起第 40 个交易日（T+40）在日历内存在且 < as_of —
按日历重算，不盲信账本 timing_class（回填的 H 行也如实计数）。
「正式栏」= 已到期 + timing_class F0 + input_pit_status VERIFIED +
非回填（计划 §6：正式成绩只收 F0+VERIFIED；其余只作研究/诊断）。

账本口径说明：当前账本中线档大多没有数值概率（b2.v1 `p_rel_t40`
是 M1 契约内容，尚未上线），因此「同信号日横截面 IC」只在
``n >= --min-cross-n``（默认 10，DAV-1573 签收口径）且概率/收益双
方秩方差非零时有定义，其余日期单列 ``no_prob`` / ``insufficient`` /
``rank_degenerate`` / ``no_labels``，绝不补零。

示例：
  python scripts/phase2/m2_periodic_report.py run \
      --ledger work/phase2-ledger/forward_ledger.jsonl \
      --calendar work/m2_report/cn_trade_days.txt \
      --as-of 2026-10-09 --out-dir work/m2_report
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
# Sibling import keeps record/record_sha256/canonical-JSON helpers
# single-sourced with the ledger writer (DAV-1480).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_snapshot_ledger as dsl  # noqa: E402

PIPELINE_VERSION = "m2_periodic_report.v1"

DEFAULT_LEDGER = REPO_ROOT / "work" / "phase2-ledger" / "forward_ledger.jsonl"
DEFAULT_LABELS = REPO_ROOT / "work" / "phase2-labels" / "labels_t40.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "work" / "m2_report"
DEFAULT_BENCH_FILE = REPO_ROOT / "work" / "phase2-weekly" / "index_closes.jsonl"
DEFAULT_BENCH_SYMBOLS = ("000300.SH",)
DEFAULT_LABEL_FIELD = "hs300_excess_pct"
DEFAULT_MIN_CROSS_N = 10          # DAV-1573 冻结口径②：单日最少 10 只
DEFAULT_MIN_TOTAL_N = 40          # 临时充分性门槛（P1 冻结前）
MAX_REASON_ROWS = 40              # 排除原因截断，防超长样本表撑爆报告

HORIZONS = ("short", "medium")
OUTCOME_OFFSET = dsl.OUTCOME_OFFSET_TRADING_DAYS   # T+40
FORMAL_KEEP = (("timing_class", "F0"), ("input_pit_status", "VERIFIED"))

_BUCKET_ORDER = ("看多", "看空", "中性", "无结论")


# ---------------------------------------------------------------------------
# 小工具


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
        pass
    t = "".join(ch for ch in str(s).strip() if ch.isdigit())[:8]
    if len(t) == 8:
        try:
            return date(int(t[:4]), int(t[4:6]), int(t[6:8]))
        except ValueError:
            return None
    return None


def _fmt(x: Optional[float], digits: int = 4) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def _mean(xs: Sequence[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 4) if xs else None


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman rank correlation (average ranks on ties); None if undefined."""
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
# 输入加载（fail-close）


def load_calendar(path: Path) -> list[date]:
    """交易日历文件：一行一个 YYYYMMDD / YYYY-MM-DD；缺失或全不可解析 → 异常。"""
    if not path.exists():
        raise FileNotFoundError(f"calendar not found: {path}")
    dates: list[date] = []
    for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        d = _parse_d(s)
        if d is None:
            raise ValueError(f"calendar line {lineno} unparseable: {s!r}")
        dates.append(d)
    if not dates:
        raise ValueError(f"calendar empty: {path}")
    return sorted(set(dates))


def load_ledger(path: Path) -> list[dict[str, Any]]:
    """Fail-close JSONL 读账本；兼容包装行 {record,prev_hash,chain_hash}
    与裸记录行。记录按 (signal_date, sealed_at, report_id) 排序——
    输入顺序不影响任何输出。"""
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
            rec = obj.get("record") if isinstance(obj, dict) else None
            if not isinstance(rec, dict):
                if isinstance(obj, dict) and "report_id" in obj:
                    rec = obj
                else:
                    raise ValueError(f"ledger line {lineno}: record missing")
            records.append(rec)
    if not records:
        raise ValueError(f"ledger empty: {path}")
    records.sort(key=lambda r: (str(r.get("signal_date") or ""),
                                str(r.get("sealed_at") or ""),
                                str(r.get("report_id") or "")))
    return records


def load_labels(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """(symbol, signal_date YYYY-MM-DD) -> label dict。文件不存在 → {}。"""
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
            d = _parse_d(sig)
            if sym and d:
                out[(str(sym), d.isoformat())] = obj
    return out


def _label_return(obj: Mapping[str, Any], field: str) -> Optional[float]:
    """单字段单认：只读 `field`，缺失/非数值 → None，不回落。"""
    v = obj.get(field)
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def load_benchmark_bars(path: Path) -> dict[str, dict[str, dict[str, float]]]:
    """symbol -> {date -> {open,close}}；文件不存在 → {}。"""
    if not path.exists():
        return {}
    out: dict[str, dict[str, dict[str, float]]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            obj = json.loads(line)
            sym, d = obj.get("symbol"), _parse_d(obj.get("date"))
            if not sym or not d:
                continue
            bar: dict[str, float] = {}
            for k in ("open", "close"):
                v = obj.get(k)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)) and v > 0:
                    bar[k] = float(v)
            out.setdefault(str(sym), {})[d.isoformat()] = bar
    return out


def benchmark_return(bars: Mapping[str, Mapping[str, float]],
                     trade_dates: list[date], signal: date,
                     offset: int = OUTCOME_OFFSET) -> Optional[float]:
    """D-072 窗口基准收益：T+1 开盘 → 信号日起第 offset 个交易日收盘。
    信号日本身必须是交易日（非交易日 snap-down 会静默移窗，记缺失）。"""
    idx = bisect.bisect_right(trade_dates, signal) - 1
    if idx < 0 or trade_dates[idx] != signal:
        return None
    e_i, t_i = idx + 1, idx + offset
    if e_i >= len(trade_dates) or t_i >= len(trade_dates):
        return None
    e_bar = bars.get(trade_dates[e_i].isoformat()) or {}
    t_bar = bars.get(trade_dates[t_i].isoformat()) or {}
    e_o, t_c = e_bar.get("open"), t_bar.get("close")
    if e_o is None or t_c is None or e_o <= 0:
        return None
    return round((t_c - e_o) / e_o * 100.0, 4)


# ---------------------------------------------------------------------------
# 分类口径（与 weekly_report / 计划 §6 一致）


def _direction_bucket(raw: Any) -> str:
    """偏多并入看多、偏空并入看空（DAV-1498 签收口径）。"""
    if raw is None:
        return "无结论"
    s = str(raw).strip().upper()
    if not s:
        return "无结论"
    for label, keys in (
            ("看多", ("看多", "偏多", "BUY", "BULL", "增持", "买入")),
            ("看空", ("看空", "偏空", "SELL", "BEAR", "减持", "卖出")),
            ("中性", ("中性", "NEUTRAL", "HOLD", "持有"))):
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


def _outcome_day(rec: Mapping[str, Any],
                 trade_dates: list[date]) -> Optional[date]:
    """T+40 交易日（signal_date 须本身为交易日，否则 None）。"""
    sig = _parse_d(rec.get("signal_date"))
    if sig is None:
        return None
    idx = bisect.bisect_right(trade_dates, sig) - 1
    if idx < 0 or trade_dates[idx] != sig:
        return None
    t_i = idx + OUTCOME_OFFSET
    return trade_dates[t_i] if t_i < len(trade_dates) else None


def _enrich(records: list[dict[str, Any]],
            trade_dates: list[date],
            as_of: date) -> list[dict[str, Any]]:
    """挂截面字段；不改动传入对象（浅拷贝）。"""
    out = []
    for r in records:
        e = dict(r)
        sig = _parse_d(r.get("signal_date"))
        e["_sig"] = sig
        e["_sig_s"] = sig.isoformat() if sig else None
        e["_outcome_day"] = _outcome_day(r, trade_dates)
        e["_matured"] = (e["_outcome_day"] is not None
                         and e["_outcome_day"] < as_of)
        e["_formal"] = bool(
            e["_matured"]
            and r.get("timing_class") == "F0"
            and r.get("input_pit_status") == "VERIFIED"
            and not r.get("backfilled"))
        e["_ver"] = _version_key(r)
        e["_risk"] = _risk_bucket(r.get("risk_status_top"))
        hz = r.get("horizons") or {}
        for h in HORIZONS:
            j = hz.get(h) or {}
            e[f"_dir_{h}"] = _direction_bucket(j.get("direction"))
        out.append(e)
    return out


def _label_for(e: Mapping[str, Any],
               labels: Mapping[tuple[str, str], dict[str, Any]],
               field: str) -> Optional[float]:
    sig = e.get("_sig_s")
    if sig is None:
        return None
    lab = labels.get((str(e.get("symbol")), sig))
    return _label_return(lab, field) if lab is not None else None


def _risk_groups(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    g: dict[str, dict[str, int]] = {}
    for r in records:
        b = r["_risk"]
        slot = g.setdefault(b, {"total": 0, "matured": 0, "pending": 0})
        slot["total"] += 1
        slot["matured" if r["_matured"] else "pending"] += 1
    return g


def _input_digest(records: list[dict[str, Any]],
                  labels: Mapping[Any, Any],
                  bench: Mapping[str, Any]) -> str:
    h = hashlib.sha256()
    for r in records:
        h.update(_canon(r))
        h.update(b"\n")
    for k in sorted(labels):
        h.update(_canon({"k": k, "v": labels[k]}))
    h.update(_canon(bench))
    h.update(PIPELINE_VERSION.encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 报告体


def _header(period: str, anchor: str, as_of: date,
            label_field: str, digest: str,
            win: tuple[str, str]) -> list[str]:
    return [
        f"<!-- pipeline={PIPELINE_VERSION} period={period} anchor={anchor} "
        f"as_of={as_of.isoformat()} label_field={label_field} "
        f"inputs_sha256={digest} -->",
        "",
        f"> 口径：P4 封存账本确定性{period}报；窗口=信号日 ∈ "
        f"[{win[0]}, {win[1]}]（交易日历口径）；「已到期」= T+40 < "
        f"{as_of.isoformat()}；「正式栏」= 已到期 + F0 + VERIFIED + 非回填"
        f"（计划 §6）；标签收益单认字段 `{label_field}`，缺失记缺失不回落；"
        "沪深300 同窗 = T+1 开盘 → T+40 收盘（D-072）。",
        "",
    ]


def build_daily(records: list[dict[str, Any]], *,
                period_dates: set[date], prev_day: Optional[date],
                anchor: date, as_of: date,
                trade_dates: list[date],
                labels: Mapping[tuple[str, str], dict[str, Any]],
                label_field: str,
                bench_bars: Mapping[str, Mapping[str, Mapping[str, float]]],
                bench_symbols: Sequence[str],
                min_cross_n: int, min_total_n: int,
                digest: str) -> str:
    """日报：封存/到期于上一交易日窗口的信号。"""
    env = _enrich(records, trade_dates, as_of)
    cohort = [r for r in env
              if r["_sig"] is not None and r["_sig"] in period_dates]
    anchor_s = anchor.isoformat()
    win = (min(period_dates).isoformat() if period_dates else "—", anchor_s)

    out = _header("daily", anchor_s, as_of, label_field, digest, win)
    prev_s = prev_day.isoformat() if prev_day else "（窗口起点，无上一交易日）"
    out.append(f"## D1. 窗口与封存概览（T = {prev_s} 的信号）")
    out.append("")
    c_status = Counter(str(r.get("status") or "?") for r in cohort)
    out.append(f"- 窗口内封存信号：**{len(cohort)}** 条"
               f"（status: {dict(sorted(c_status.items()))}）")
    out.append(f"- 其中已到期（T+40 < {as_of.isoformat()}）："
               f"**{sum(1 for r in cohort if r['_matured'])}**；"
               f"正式栏：**{sum(1 for r in cohort if r['_formal'])}**")
    out.append(f"- 账本总量：{len(records)}；已到期 "
               f"{sum(1 for r in env if r['_matured'])}；正式栏 "
               f"{sum(1 for r in env if r['_formal'])}（判定阈值 "
               f"{min_total_n}，P1 冻结前临时口径）")
    out.append("")

    out.append("## D2. 方向分布（本窗口信号，按档位 × 版本队列）")
    out.append("")
    ver_rows = []
    queues: dict[str, list[dict]] = {}
    for r in cohort:
        queues.setdefault(r["_ver"], []).append(r)
    for q in sorted(queues):
        rs = queues[q]
        row = [q, len(rs)]
        for h in HORIZONS:
            c = Counter(r[f"_dir_{h}"] for r in rs)
            row += [c.get(k, 0) for k in _BUCKET_ORDER]
        ver_rows.append(tuple(row))
    if ver_rows:
        out.append(_md_table(
            ("版本队列", "n",
             "短·看多", "短·看空", "短·中性", "短·无结论",
             "中·看多", "中·看空", "中·中性", "中·无结论"), ver_rows))
    else:
        out.append("（本窗口无封存信号）")
    out.append("")

    out.append("## D3. 本窗口横截面（当日信号批）")
    out.append("")
    if not cohort:
        out.append("（无样本）")
        out.append("")
    else:
        by_day: dict[date, list[dict]] = {}
        for r in cohort:
            by_day.setdefault(r["_sig"], []).append(r)
        rows = []
        for d in sorted(by_day):
            rs = by_day[d]
            probs = [((r.get("horizons") or {}).get("medium") or {})
                     .get("probability") for r in rs]
            probs = [float(p) for p in probs
                     if isinstance(p, (int, float)) and not isinstance(p, bool)]
            n_prob = len(probs)
            n_lab = sum(1 for r in rs
                        if _label_for(r, labels, label_field) is not None)
            ic = None
            note = "ok"
            if n_prob == 0:
                note = "no_prob"
            elif n_prob < min_cross_n:
                note = "insufficient"
            else:
                pairs = [(p, _label_for(r, labels, label_field))
                         for r, p in
                         zip([r for r in rs
                              if isinstance(
                                  ((r.get("horizons") or {}).get("medium") or {})
                                  .get("probability"), (int, float))
                              and not isinstance(
                                  ((r.get("horizons") or {}).get("medium") or {})
                                  .get("probability"), bool)],
                             probs)]
                pairs = [(p, v) for p, v in pairs if v is not None]
                if len(pairs) < min_cross_n:
                    note = "insufficient_labels"
                else:
                    ic = _spearman([p for p, _ in pairs],
                                   [v for _, v in pairs])
                    if ic is None:
                        note = "rank_degenerate"
            rows.append((d.isoformat(), len(rs), n_prob, n_lab,
                         _fmt(ic), note))
        out.append(f"> IC 需 n≥{min_cross_n}（冻结口径②）且双方秩方差非零；"
                   "no_prob=中线无数值概率（b2.v1 未上线前属预期）。")
        out.append("")
        out.append(_md_table(
            ("signal_date", "封存数", "有中线概率", "有标签收益",
             "Spearman IC", "状态"), rows))
        out.append("")

    out.append("## D4. 风险层（本窗口信号）")
    out.append("")
    g = _risk_groups(cohort)
    if g:
        out.append(_md_table(("风险档位", "总数", "已到期", "未到期"),
                             [(b, v["total"], v["matured"], v["pending"])
                              for b, v in sorted(g.items())]))
    else:
        out.append("（无样本）")
    out.append("")
    out.append("## D5. 说明")
    out.append("")
    out.append("- 只读账本与文件输入；同输入重跑逐字节一致（含日历注入）。")
    out.append("- 本报告不含组合净值（M2-B 影子组合为独立层）；"
                   "不做推断（NW-HAC 归 M1 评估脚本/月报区）。")
    out.append("")
    return "\n".join(out)


def build_monthly(records: list[dict[str, Any]], *,
                  period_dates: set[date], month: str, anchor: date,
                  as_of: date, trade_dates: list[date],
                  labels: Mapping[tuple[str, str], dict[str, Any]],
                  label_field: str,
                  bench_bars: Mapping[str, Mapping[str, Mapping[str, float]]],
                  bench_symbols: Sequence[str],
                  min_cross_n: int, min_total_n: int,
                  digest: str) -> str:
    """月报：信号日落入该自然月（交易日历口径）的全部封存记录。"""
    env = _enrich(records, trade_dates, as_of)
    cohort = [r for r in env
              if r["_sig"] is not None and r["_sig"] in period_dates]
    win = (min(period_dates).isoformat() if period_dates else "—",
           anchor.isoformat())

    out = _header("monthly", anchor.isoformat(), as_of, label_field, digest, win)

    matured = [r for r in cohort if r["_matured"]]
    formal = [r for r in cohort if r["_formal"]]
    immature = [r for r in cohort if not r["_matured"]]
    sufficient = len(formal) >= min_total_n

    out.append("## M1. 样本量与结论判定")
    out.append("")
    out.append(f"- 本月封存信号：**{len(cohort)}**")
    out.append(f"- 已到期（T+40 < {as_of.isoformat()}）：**{len(matured)}**；"
               f"未到期：**{len(immature)}**")
    out.append(f"- 正式栏（F0+VERIFIED+非回填）：**{len(formal)}**；"
               f"判定阈值 min_total_n={min_total_n}（P1 冻结前临时口径）")
    out.append("")
    if sufficient:
        out.append(f"> ✅ 正式样本 {len(formal)} ≥ {min_total_n}，"
                   "可开始解读指标。")
    else:
        out.append(f"> ⚠️ **样本不足，不下结论**（正式 {len(formal)} < "
                   f"{min_total_n}）；以下仅作管线验证。")
    out.append("")

    out.append("## M2. 方向分布（本月信号，按版本队列）")
    out.append("")
    queues: dict[str, list[dict]] = {}
    for r in cohort:
        queues.setdefault(r["_ver"], []).append(r)
    rows = []
    for q in sorted(queues):
        rs = queues[q]
        row = [q, len(rs)]
        for h in HORIZONS:
            c = Counter(r[f"_dir_{h}"] for r in rs)
            row += [c.get(k, 0) for k in _BUCKET_ORDER]
        rows.append(tuple(row))
    if rows:
        out.append(_md_table(
            ("版本队列", "n",
             "短·看多", "短·看空", "短·中性", "短·无结论",
             "中·看多", "中·看空", "中·中性", "中·无结论"), rows))
    else:
        out.append("（本月无封存信号）")
    out.append("")

    out.append("## M3. 已到期样本 timing_class × input_pit_status")
    out.append("")
    pit: dict[str, Counter] = {}
    for r in matured:
        tc = str(r.get("timing_class") or "UNCLASSIFIED")
        pit.setdefault(tc, Counter())[
            str(r.get("input_pit_status") or "MISSING")] += 1
    pr = []
    for tc in sorted(pit):
        c = pit[tc]
        formal_n = c.get("VERIFIED", 0) if tc == "F0" else 0
        pr.append((tc, c.get("VERIFIED", 0), c.get("UNVERIFIED", 0),
                   c.get("FAILED", 0), c.get("MISSING", 0),
                   formal_n, sum(c.values()) - formal_n))
    if pr:
        out.append(_md_table(
            ("timing_class", "VERIFIED", "UNVERIFIED", "FAILED", "MISSING",
             "正式栏", "研究/诊断栏"), pr))
    else:
        out.append("（本月无已到期样本）")
    out.append("")

    out.append("## M4. 日级横截面统计（未冻结口径，双列展示）")
    out.append("")
    daily: dict[date, list[tuple[float, float]]] = {}
    missing_lab = miss_prob = 0
    for r in matured:
        j = (r.get("horizons") or {}).get("medium") or {}
        prob = j.get("probability")
        if not isinstance(prob, (int, float)) or isinstance(prob, bool):
            miss_prob += 1
            continue
        v = _label_for(r, labels, label_field)
        if v is None:
            missing_lab += 1
            continue
        daily.setdefault(r["_sig"], []).append((float(prob), v))
    drows = []
    ic_ser: list[float] = []
    sp_ser: list[float] = []
    insuf = 0
    for d in sorted(daily):
        pairs = daily[d]
        n = len(pairs)
        if n < min_cross_n:
            insuf += 1
            drows.append((d.isoformat(), n, "insufficient", "—", "—"))
            continue
        ic = _spearman([p for p, _ in pairs], [v for _, v in pairs])
        if ic is not None:
            ic_ser.append(ic)
        ordered = sorted(pairs, key=lambda p: p[0])
        k = max(1, n // 3)
        spread = (round(sum(v for _, v in ordered[-k:]) / k
                        - sum(v for _, v in ordered[:k]) / k, 4)
                  if k else None)
        if spread is not None:
            sp_ser.append(spread)
        drows.append((d.isoformat(), n, "ok" if ic is not None
                      else "rank_degenerate", _fmt(ic), _fmt(spread)))
    out.append(f"> 只计有中线概率且标签字段 `{label_field}` 非缺的成熟样本；"
               f"缺标签 {missing_lab} 条、缺概率 {miss_prob} 条不计入。")
    out.append("")
    if drows:
        out.append(_md_table(
            ("signal_date", "n", "status", "Spearman IC（未冻结）",
             "高低分组差（未冻结）"), drows))
        out.append("")
        out.append(f"- IC 序列均值 **{_fmt(_mean(ic_ser))}**"
                   f"（{len(ic_ser)} 天有效）；组差均值 "
                   f"**{_fmt(_mean(sp_ser))}**（{len(sp_ser)} 天）；"
                   f"insufficient 天数（n<{min_cross_n}）：{insuf}。")
    else:
        out.append("（本月无可入列日序列：成熟样本普遍缺中线概率或标签 — "
                   "b2.v1 上线前属预期）")
    out.append("")

    out.append(f"## M5. 收益口径（标签字段 `{label_field}` 单认；"
               "沪深300 同窗 T+1 开盘→T+40 收盘）")
    out.append("")
    lab_rets: list[float] = []
    lab_missing = 0
    for r in matured:
        v = _label_for(r, labels, label_field)
        if v is None:
            lab_missing += 1
        else:
            lab_rets.append(v)
    bench_rets: list[float] = []
    bench_missing = 0
    first = (bench_bars.get(bench_symbols[0])
             if bench_bars and bench_symbols else None)
    for r in matured:
        v = (benchmark_return(first, trade_dates, r["_sig"])
             if first and r["_sig"] else None)
        if v is None:
            bench_missing += 1
        else:
            bench_rets.append(v)
    out.append(_md_table(
        ("集合", "成熟样本", "标签收益 n", "标签均值", "标签缺失",
         "沪深300 同窗 n", "沪深300 均值", "基准缺失"),
        [("本月已到期", len(matured), len(lab_rets), _fmt(_mean(lab_rets)),
          lab_missing, len(bench_rets), _fmt(_mean(bench_rets)),
          bench_missing)]))
    out.append("")

    out.append("## M6. 风险层（本月信号，描述性）")
    out.append("")
    g = _risk_groups(cohort)
    if g:
        out.append(_md_table(("风险档位", "总数", "已到期", "未到期"),
                             [(b, v["total"], v["matured"], v["pending"])
                              for b, v in sorted(g.items())]))
    else:
        out.append("（无样本）")
    out.append("")
    out.append("## M7. 说明")
    out.append("")
    out.append("- 推断（NW-HAC 60/120、A–D 分档）归 M1 评估脚本，本报告只出"
                   "账本确定性描述统计，不越权下结论。")
    out.append("- 同输入两次运行逐字节相同；输入 digest 见文件头注释。")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI


def _prev_day_window(trade_dates: list[date],
                     as_of: date) -> tuple[set[date], Optional[date], Optional[date]]:
    """日报窗口：anchor = ≤as_of 的最后一个交易日；覆盖信号日=前一交易日。
    返回 (period_dates, prev_day, anchor)。日历不覆盖 as_of 周边 → anchor None。"""
    idx = bisect.bisect_right(trade_dates, as_of) - 1
    if idx < 0:
        return set(), None, None
    anchor = trade_dates[idx]
    prev = trade_dates[idx - 1] if idx >= 1 else None
    return ({prev} if prev else set()), prev, anchor


def _month_window(trade_dates: list[date],
                  as_of: date) -> tuple[set[date], str, Optional[date]]:
    """月报窗口：as_of 所在自然月内的全部交易日；锚=当月最后交易日。"""
    days = [d for d in trade_dates
            if d.year == as_of.year and d.month == as_of.month]
    if not days:
        return set(), f"{as_of.year:04d}-{as_of.month:02d}", None
    return set(days), f"{as_of.year:04d}-{as_of.month:02d}", days[-1]


def cmd_run(args: argparse.Namespace) -> int:
    try:
        trade_dates = load_calendar(Path(args.calendar))
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 3
    try:
        records = load_ledger(Path(args.ledger))
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 4

    as_of = _parse_d(args.as_of) or date.today()
    labels = load_labels(Path(args.labels)) if args.labels else {}
    bench_bars = (load_benchmark_bars(Path(args.benchmarks))
                  if args.benchmarks else {})
    digest = _input_digest(records, labels, bench_bars)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    if args.period in ("daily", "both"):
        pd_, prev, anchor = _prev_day_window(trade_dates, as_of)
        if anchor is None or prev is None:
            print("FAILED: calendar does not cover a daily window for "
                  f"as_of={as_of}", file=sys.stderr)
            return 3
        body = build_daily(
            records, period_dates=pd_, prev_day=prev, anchor=anchor,
            as_of=as_of, trade_dates=trade_dates, labels=labels,
            label_field=args.label_field, bench_bars=bench_bars,
            bench_symbols=args.benchmark_symbols,
            min_cross_n=args.min_cross_n, min_total_n=args.min_total_n,
            digest=digest)
        p = Path(args.daily_out) if args.daily_out else (
            out_dir / f"m2_daily_{anchor.isoformat()}.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(body, "utf-8")
        tmp.replace(p)
        written.append(p)
    if args.period in ("monthly", "both"):
        md_, mon, anchor = _month_window(trade_dates, as_of)
        if anchor is None:
            print("FAILED: calendar has no trading day in month of "
                  f"as_of={as_of}", file=sys.stderr)
            return 3
        body = build_monthly(
            records, period_dates=md_, month=mon, anchor=anchor,
            as_of=as_of, trade_dates=trade_dates, labels=labels,
            label_field=args.label_field, bench_bars=bench_bars,
            bench_symbols=args.benchmark_symbols,
            min_cross_n=args.min_cross_n, min_total_n=args.min_total_n,
            digest=digest)
        p = Path(args.monthly_out) if args.monthly_out else (
            out_dir / f"m2_monthly_{mon}.md")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(body, "utf-8")
        tmp.replace(p)
        written.append(p)
    for p in written:
        print(f"OK: wrote {p}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--period", choices=("daily", "monthly", "both"),
                   default="both")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p.add_argument("--labels", default=str(DEFAULT_LABELS),
                   help="T+40 label JSONL；文件不存在按空集处理")
    p.add_argument("--label-field", default=DEFAULT_LABEL_FIELD,
                   help="标签收益单认字段（默认 hs300_excess_pct）")
    p.add_argument("--benchmarks", default=str(DEFAULT_BENCH_FILE),
                   help="指数 OHLC JSONL {symbol,date,open,close}；"
                        "不存在则基准列记缺失")
    p.add_argument("--benchmark-symbols", nargs="+",
                   default=list(DEFAULT_BENCH_SYMBOLS))
    p.add_argument("--calendar", required=True,
                   help="交易日历文件（一行一个 YYYYMMDD/YYYY-MM-DD）；"
                        "必选——无日历则到期/锚点无从判定，fail-close")
    p.add_argument("--as-of", default=None,
                   help="锚定日 YYYY-MM-DD（默认今天）；日报取 ≤as-of 的"
                        "最后交易日为锚、覆盖其前一交易日信号")
    p.add_argument("--min-cross-n", type=int, default=DEFAULT_MIN_CROSS_N,
                   help="日横截面最少样本（默认 10，DAV-1573 冻结口径②）")
    p.add_argument("--min-total-n", type=int, default=DEFAULT_MIN_TOTAL_N)
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--daily-out", default=None)
    p.add_argument("--monthly-out", default=None)
    args = ap.parse_args()
    if args.cmd == "run":
        return cmd_run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
