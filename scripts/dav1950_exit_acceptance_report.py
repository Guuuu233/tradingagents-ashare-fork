#!/usr/bin/env python3
"""DAV-1950 — 第一阶段验收报告生成器（只读、零模型、零 provider）。

总控 10-12 令：汇总出口标准 A1–A4 / B1–B3 的读数，每项写明
**数据来源、起算日、样本量、门槛、是否达标**；A1 标注「需总控人工审计」。

口径纪律（不重写口径）：

* 全部读数来自现有脚本 ``scripts/dav1507_exit_progress.py`` 的同一套
  扫描与指标函数——本生成器以 ``--json`` 模式**在进程内**调用其
  ``main()``，只取回结构化 summary 做汇总与格式化；不改任何既有
  脚本的口径、不复制其谓词实现。
* 确定性：报告是 ``(db 文件内容, --start, --end, --as-of,
  --calendar-json, --ids, --all-history)`` 的纯函数。不读墙钟——
  ``--as-of`` 未显式给出时从 ``current.db`` 同目录最新
  ``tradingagents-YYYY-MM-DD.db.zst`` 快照名解析（共享快照目录
  约定，见 docs/ta-snapshot-usage.md），解析不到再退回 db 文件
  mtime（同一输入文件该值同样不变）。正文不嵌入生成时刻。
* 输入溯源行内记录 db 路径 + 字节数 + SHA-256，便于两次运行
  确认输入同一。

Exit codes: 0 ok；2 输入缺失（db/calendar/args，与上游同口径上抛）。

示例：
  python scripts/dav1950_exit_acceptance_report.py \
      --db ~/ta-snapshot/current.db --as-of 2026-10-12 \
      --out work/dav1950/acceptance-2026-10-12.md
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GENERATOR_VERSION = "dav1950_exit_acceptance_report.v1"
# DAV-2033: shared snapshot moved out of /tmp to ~/ta-snapshot (2026-10-13),
# see docs/ta-snapshot-usage.md. Expanded at import so the value recorded in
# the report and forwarded to upstream ``open_ro`` is a real absolute path
# (``Path.resolve()`` upstream does not expand ``~``).
DEFAULT_DB = str(Path("~/ta-snapshot/current.db").expanduser())
_SNAPSHOT_RE = re.compile(r"^tradingagents-(\d{4}-\d{2}-\d{2})\.db\.zst$")


# --------------------------------------------------------------------------- #
# Upstream invocation (in-process; single tracemalloc/footprint envelope)     #
# --------------------------------------------------------------------------- #

def run_upstream_summary(db, start, end, as_of, calendar_json, ids_path,
                         all_history):
    """Call dav1507_exit_progress.main() with --json; return parsed summary.

    Runs in-process so a single ``/usr/bin/time -l`` measurement covers the
    whole pipeline (scan + metrics + render). ``SystemExit`` (upstream
    fail-close, e.g. missing calendar -> 2) is re-raised as-is.
    """
    import dav1507_exit_progress as up

    argv = [
        "dav1507_exit_progress.py", "--db", str(db), "--json",
        "--start", start, "--as-of", as_of,
    ]
    if end:
        argv += ["--end", end]
    if calendar_json:
        argv += ["--calendar-json", str(calendar_json)]
    if ids_path:
        argv += ["--ids", str(ids_path)]
    if all_history:
        argv += ["--all-history"]
    old_argv = sys.argv
    buf = io.StringIO()
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(buf):
            rc = up.main()
    finally:
        sys.argv = old_argv
    if rc:
        raise SystemExit(rc)
    return json.loads(buf.getvalue())


# --------------------------------------------------------------------------- #
# Deterministic provenance                                                    #
# --------------------------------------------------------------------------- #

def db_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_as_of(db_path):
    """Derive --as-of default from the shared-snapshot directory convention.

    Newest sibling ``tradingagents-YYYY-MM-DD.db.zst`` names the snapshot
    day; fall back to the db file's own mtime (UTC). Deterministic for an
    unchanged input file/directory.
    """
    db = Path(db_path)
    try:
        days = sorted(
            m.group(1) for f in db.parent.iterdir()
            if (m := _SNAPSHOT_RE.match(f.name))
        )
    except OSError:
        days = []
    if days:
        return days[-1]
    from datetime import datetime, timezone
    return datetime.fromtimestamp(db.stat().st_mtime,
                                  tz=timezone.utc).date().isoformat()


# --------------------------------------------------------------------------- #
# Acceptance report                                                           #
# --------------------------------------------------------------------------- #

def _v(ok):
    return {True: "✅ 达标", False: "❌ 未达标", None: "N/A"}[ok]


def _a3_verdict(c):
    """Per-cohort A3 three-part verdict (each side >=25 sessions, window
    ratio in [40,60], each side >=100 verified claims)."""
    es, vc, w = c["effective_sessions"], c["verified_claims"], c["last_50_window"]
    parts = []
    parts.append(es["bull"] >= 25 and es["bear"] >= 25)
    parts.append(vc["bull"] >= 100 and vc["bear"] >= 100)
    parts.append(bool(w.get("ratio_in_[40,60]")) if w.get("ratio_in_[40,60]") is not None else None)
    if all(p is True for p in parts):
        return "✅ 达标"
    if any(p is False for p in parts):
        return "❌ 未达标"
    return "N/A（窗口不足等）"


def build_report(summary, db_path, db_bytes, db_sha, as_of, all_history):
    meta, a1, a2, a3, a4 = (summary[k] for k in ("meta", "A1", "A2", "A3", "A4"))
    b1, b2, b3 = (summary[k] for k in ("B1", "B2", "B3"))
    L = []
    L.append("<!-- generator=" + GENERATOR_VERSION
             + f" upstream=dav1507_exit_progress db_sha256={db_sha} -->")
    L.append("# 第一阶段验收报告（D-066 出口标准 A1–A4 / B1–B3）")
    L.append("")
    L.append("## 输入与口径")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 数据库（只读快照） | `{meta['db']}`（{db_bytes} B，sha256 `{db_sha}`） |")
    L.append(f"| 样本起算日 | {meta['start']} |")
    L.append(f"| 样本截止 | {meta['end'] or '—（不设上限）'} |")
    L.append(f"| as_of（A4 成熟档基准） | {meta['as_of']} |")
    L.append(f"| 账户 | `{meta['account']}` |")
    L.append(f"| 范围 | {meta['reports']} 份报告 / {meta['units']} 个档位单元 / {meta['clean_units']} 个 clean 单元 |")
    L.append(f"| 读数来源 | `scripts/dav1507_exit_progress.py`（口径冻结于 `work/dav1507-exit-progress/spec_v4.md` + D-074 修订） |")
    L.append(f"| 上游扫描内存 | tracemalloc 峰值 {meta['peak_mb']} MB（整管线含生成器 `/usr/bin/time -l` 实测见交付评论） |")
    L.append("")
    L.append("## 出口标准逐项读数")
    L.append("")

    # ── A1 ────────────────────────────────────────────────────────────
    L.append("### A1 — 假 clean = 0")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 数据来源 | reports 表 `result_data` → `filter_v2_completed_reports` clean 管线输出（VALID + BUY/SELL 可执行档） |")
    L.append(f"| 起算日 | {meta['start']} |")
    L.append(f"| 样本量 | 可执行档 {a1['executable_units']} 条（范围内 {meta['units']} 个档位单元） |")
    L.append(f"| 门槛 | {a1['threshold']} |")
    L.append("| 是否达标 | **需总控人工审计**（脚本不判定假 clean；逐档审计清单见文末，出现 1 例假 clean 即回退并清零计数窗口） |")
    L.append("")

    # ── A2 ────────────────────────────────────────────────────────────
    rw = a2["rolling_window"]
    asc = a2["all_scope"]
    L.append("### A2 — 滚动 10 个交易日合格 clean 率 ≥ 4.2%")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 数据来源 | clean 管线 qualifying 集合 / 范围内全部 completed 档位单元 |")
    L.append(f"| 起算日 | {meta['start']} |")
    L.append(f"| 样本量 | 滚动窗口 {rw['days']} 个交易日（{('、').join(rw['dates']) if rw['dates'] else '—'}）：{rw['completed_units']} 个 completed 单元；全期 {asc['completed_units']} 个 |")
    L.append(f"| 门槛 | {a2['threshold']} |")
    if rw["rate_pct"] is not None:
        L.append(f"| 当前读数 | 滚动 {rw['rate_pct']}%（{rw['qualified_clean']}/{rw['completed_units']}，gap {rw['gap_to_4.2']:+.3f}pp）；全期 {asc['rate_pct']}%（{asc['qualified_clean']}/{asc['completed_units']}） |")
    else:
        L.append("| 当前读数 | 无样本 |")
    L.append(f"| 是否达标 | {_v(rw['meets'])} |")
    L.append("")

    # ── A3 ────────────────────────────────────────────────────────────
    L.append("### A3 — 多空分侧有效场次 / 最近 50 场比例 / 已核实论点")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 数据来源 | clean 池逐单元 `a3_session_sides`/`is_verified_claim`（冻结谓词，spec_v4 §2–§4 + D-074 双协议规则），按 DAV-1322 四元 cohort 分桶 |")
    L.append(f"| 起算日 | {meta['start']} |")
    L.append(f"| 样本量 | clean 单元 {meta['clean_units']} 个 |")
    L.append(f"| 门槛 | {a3['threshold']} |")
    L.append("| 是否达标 | 见下表 per-cohort |")
    L.append("")
    if a3["per_cohort"]:
        L.append("| cohort | clean 单元 | 有效场次 多/空（各 ≥25） | 窗口比 ∈[40,60] | 已核实 多/空（各 ≥100） | 判定 |")
        L.append("|---|---|---|---|---|---|")
        for cohort, c in sorted(a3["per_cohort"].items()):
            w = c["last_50_window"]
            share = f"{w['bull_share_pct']}%（{w['n']} 场，多{w['bull_sessions']}/空{w['bear_sessions']}）" \
                if w.get("bull_share_pct") is not None else "—"
            flag = "（partial window）" if w.get("partial_window") else ""
            L.append(
                f"| `{cohort}` | {c['clean_units']} | "
                f"{c['effective_sessions']['bull']} / {c['effective_sessions']['bear']} | "
                f"{share}{flag} | "
                f"{c['verified_claims']['bull']} / {c['verified_claims']['bear']} | "
                f"{_a3_verdict(c)} |")
        L.append("")
        dh = sum(c["data_health"]["clean_units_with_missing_bf"] for c in a3["per_cohort"].values())
        L.append(f"> 观察列（不判达标）：多空累计差 |多−空| 见上游 `observed_abs_diff_cumulative`；`_ref_loose` 宽口径参考列同；数据健康——clean 档内 Opening 论点缺/非法战场 {dh}/{meta['clean_units']} 档。")
    else:
        L.append("（范围内无 clean 单元）")
    L.append("")

    # ── A4 ────────────────────────────────────────────────────────────
    L.append("### A4 — completed ≥ 95%；T+5 完整率 ≥ 95%")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 数据来源 | reports.status（completed/failed）+ result_data `t_plus_5_status` 探针；成熟档 = trade_date 距 as_of ≥ 5 交易日（交易日历，无回退） |")
    L.append(f"| 起算日 | {meta['start']} |")
    L.append(f"| 样本量 | submitted {a4['submitted']}（completed {a4['completed']} / failed {a4['failed']}）；成熟档 {a4['mature_units']} 个单元 / {a4['mature_reports']} 份报告（成熟截止 {a4['mature_cutoff']}） |")
    L.append(f"| 门槛 | {a4['threshold']} |")
    if a4["completion_rate_pct"] is not None:
        t5r = f"{a4['t5_rate_pct']}%（{a4['t5_present']}/{a4['mature_units']}）" if a4["t5_rate_pct"] is not None else "N/A（无成熟档）"
        L.append(f"| 当前读数 | completed {a4['completion_rate_pct']}%；T+5 {t5r} |")
        comp_ok = a4["completion_rate_pct"] >= 95.0
        t5_ok = (a4["t5_rate_pct"] >= 95.0) if a4["t5_rate_pct"] is not None else None
        both = comp_ok and (t5_ok is not False)
        verdict = "✅ 达标" if (comp_ok and t5_ok) else ("❌ 未达标" if (not comp_ok or t5_ok is False) else "部分达标（completed ✅；T+5 样本不足 N/A）")
        L.append(f"| 是否达标 | {verdict} |")
    else:
        L.append("| 当前读数 | 无样本 |")
        L.append("| 是否达标 | N/A |")
    L.append("")

    # ── B1 ────────────────────────────────────────────────────────────
    L.append("### B1 — 概率与价位输出契约上线（b1.v1）")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 数据来源 | 档位单元 `forecast.contract` 字段 |")
    L.append(f"| 起算日 | {meta['start']} |")
    L.append(f"| 样本量 | {b1['units_b1_v1']}/{b1['units_total']} 个档位单元带 b1.v1 forecast |")
    L.append(f"| 门槛 | {b1['threshold']} |")
    L.append(f"| 是否达标 | {_v(b1['live'])}（覆盖 {b1['coverage_pct']}%；{b1['note']}） |")
    L.append("")

    # ── B2 ────────────────────────────────────────────────────────────
    L.append("### B2 — 新契约下连续 ≥100 次运行无效率 ≤ 5%")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 数据来源 | b1.v1 完成单元 `forecast.status` + `price_plan_check.status`（violation/missing_required/unverifiable 即无效） |")
    L.append(f"| 起算日 | {meta['start']} |")
    L.append(f"| 样本量 | {b2['b1_v1_units_completed']} 次 b1.v1 运行（无效 {b2['invalid']}） |")
    L.append(f"| 门槛 | {b2['threshold']} |")
    if b2["invalid_pct"] is not None:
        L.append(f"| 当前读数 | 无效率 {b2['invalid_pct']}%；样本差 {b2['sample_gap_to_100']} |")
    else:
        L.append("| 当前读数 | 无 b1.v1 样本 |")
    L.append(f"| 是否达标 | {_v(b2['meets'])} |")
    L.append("")

    # ── B3 ────────────────────────────────────────────────────────────
    L.append("### B3 — 每次运行可追溯（input_snapshot.hash + served_models）")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 数据来源 | reports.result_data `input_snapshot.hash` + `served_models`（cf7bda3 契约） |")
    L.append(f"| 起算日 | {meta['start']} |")
    L.append(f"| 样本量 | {b3['reports_total']} 份报告 |")
    L.append(f"| 门槛 | {b3['threshold']} |")
    L.append(f"| 当前读数 | {b3['traceable']}/{b3['reports_total']} 可追溯（{b3['coverage_pct']}%），缺 {b3['gap']} |")
    L.append(f"| 是否达标 | {_v(b3['gap'] == 0 and bool(b3['reports_total']))} |")
    L.append("")

    # ── A1 audit manifest ─────────────────────────────────────────────
    L.append("## 附：A1 待总控人工审计可执行档清单")
    L.append("")
    if a1["audit_manifest"]:
        L.append("| report_id | symbol | trade_date | cohort | 动作 | 方向 | created_at |")
        L.append("|---|---|---|---|---|---|---|")
        for r in a1["audit_manifest"]:
            L.append(f"| `{r['report_id']}` | {r['symbol']} | {r['trade_date']} | `{r['cohort']}` | {r['decision']} | {r['direction']} | {r['created_at']} |")
    else:
        L.append("（范围内无可执行档）")
    L.append("")
    if all_history and summary.get("all_history_side_reading"):
        ah = summary["all_history_side_reading"]
        a2h, a4h = ah.get("a2") or {}, ah.get("a4") or {}
        L.append("## 附：全历史侧读（对照，不计入门栏）")
        L.append("")
        L.append(f"- 全账户历史 {ah['reports']} 份报告 / {ah['units']} 档；"
                 f"A2 全期 {(a2h.get('all_scope') or {}).get('rate_pct')}%；"
                 f"completed {a4h.get('completion_rate_pct')}%")
        L.append("")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# Self test (in-memory; no DB, no network)                                     #
# --------------------------------------------------------------------------- #

def _self_test():
    """Determinism + field presence on a fixed fake summary."""
    summary = {
        "meta": {"db": "x.db", "account": "acct", "start": "2026-10-08",
                 "end": None, "as_of": "2026-10-12", "reports": 2, "units": 4,
                 "clean_units": 2, "peak_mb": 1.0},
        "A1": {"threshold": "假 clean = 0", "executable_units": 1,
               "audit_manifest": [{"report_id": "r1", "symbol": "X",
                                    "trade_date": "2026-10-09", "cohort": "c",
                                    "decision": "BUY", "direction": "BULL",
                                    "created_at": "2026-10-09T01:00"}]},
        "A2": {"threshold": "≥4.2%", "window_days": 10,
               "all_scope": {"completed_units": 4, "qualified_clean": 1,
                             "rate_pct": 25.0, "gap_to_4.2": 20.8},
               "rolling_window": {"dates": ["2026-10-09"], "days": 1,
                                  "completed_units": 4, "qualified_clean": 1,
                                  "rate_pct": 25.0, "gap_to_4.2": 20.8,
                                  "meets": True},
               "direction_watch": {"buy": 1, "sell": 0}},
        "A3": {"threshold": "t3",
               "per_cohort": {"dmv:ecv:pbv:short": {
                   "clean_units": 2,
                   "effective_sessions": {"bull": 30, "bear": 28,
                                          "gap_to_25": {"bull": 0, "bear": 0}},
                   "verified_claims": {"bull": 120, "bear": 110,
                                       "gap_to_100": {"bull": 0, "bear": 0},
                                       "ref_loose_not_judged": {"bull": 0, "bear": 0}},
                   "last_50_window": {"n": 2, "partial_window": True,
                                      "bull_sessions": 1, "bear_sessions": 1,
                                      "bull_share_pct": 50.0,
                                      "ratio_in_[40,60]": True},
                   "observed_abs_diff_cumulative": 2,
                   "observed_opening_cluster_type_diversity": {"bull": {}, "bear": {}},
                   "observed_opening_cluster_type_mean_per_unit": {"bull": 1, "bear": 1},
                   "protocol_mix": {"v2": 2},
                   "data_health": {"opening_claims": 4,
                                   "opening_missing_or_invalid_battlefield": 0,
                                   "clean_units_with_missing_bf": 0}}}},
        "A4": {"threshold": "t4", "as_of": "2026-10-12", "mature_cutoff": "2026-10-01",
               "submitted": 2, "completed": 2, "failed": 0,
               "completion_rate_pct": 100.0, "completion_gap_to_95": 5.0,
               "mature_units": 1, "mature_reports": 1, "t5_present": 1,
               "t5_reports_present": 1, "t5_rate_pct": 100.0,
               "t5_report_rate_pct": 100.0, "t5_gap_to_95": 5.0},
        "B1": {"threshold": "b1", "units_total": 4, "units_b1_v1": 4,
               "coverage_pct": 100.0, "live": True, "note": "n"},
        "B2": {"threshold": "b2", "b1_v1_units_completed": 4, "invalid": 0,
               "invalid_pct": 0.0, "sample_gap_to_100": 96, "meets": None},
        "B3": {"threshold": "b3", "reports_total": 2, "traceable": 2,
               "coverage_pct": 100.0, "gap": 0,
               "missing_report_ids_sample": []},
        "all_history_side_reading": None,
    }
    r1 = build_report(summary, "x.db", 123, "ab" * 32, "2026-10-12", False)
    r2 = build_report(summary, "x.db", 123, "ab" * 32, "2026-10-12", False)
    assert r1 == r2
    for needle in ("需总控人工审计", "样本起算日", "样本量", "门槛", "是否达标",
                   "✅ 达标", "db_sha256"):
        assert needle in r1, needle
    assert "生成时间" not in r1
    # snapshot_as_of derivation
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        dbp = Path(td) / "current.db"
        dbp.write_bytes(b"")
        (Path(td) / "tradingagents-2026-10-09.db.zst").write_bytes(b"")
        (Path(td) / "tradingagents-2026-10-12.db.zst").write_bytes(b"")
        assert snapshot_as_of(dbp) == "2026-10-12"
        for f in Path(td).glob("*.zst"):
            f.unlink()
        os.utime(dbp, (1760000000, 1760000000))
        assert snapshot_as_of(dbp) == "2025-10-09", snapshot_as_of(dbp)
    print(json.dumps({"self_test": "passed", "model_calls": 0,
                      "provider_calls": 0}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB,
                    help=f"read-only snapshot db (default {DEFAULT_DB})")
    ap.add_argument("--start", default="2026-10-08",
                    help="sample start trade_date (default 2026-10-08, first daily-batch day)")
    ap.add_argument("--end", help="sample end trade_date")
    ap.add_argument("--as-of", default=None,
                    help="A4 mature-cutoff anchor (default: newest sibling "
                         "tradingagents-YYYY-MM-DD.db.zst name, else db mtime)")
    ap.add_argument("--calendar-json")
    ap.add_argument("--ids", help="JSON array of report ids (upstream passthrough)")
    ap.add_argument("--all-history", action="store_true",
                    help="include upstream --all-history side reading")
    ap.add_argument("--out", help="write markdown to this file (default stdout)")
    ap.add_argument("--json", action="store_true",
                    help="emit acceptance JSON (report + provenance) instead of markdown")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    db = Path(args.db).resolve()
    if not db.is_file():
        sys.stderr.write(f"[exit 2] database does not exist: {db}\n")
        return 2
    as_of = args.as_of or snapshot_as_of(db)

    sha = db_sha256(db)
    nbytes = db.stat().st_size
    summary = run_upstream_summary(db, args.start, args.end, as_of,
                                   args.calendar_json, args.ids,
                                   args.all_history)
    report_md = build_report(summary, str(db), nbytes, sha, as_of,
                             args.all_history)

    if args.json:
        out = json.dumps({"generator": GENERATOR_VERSION,
                          "db": str(db), "db_bytes": nbytes, "db_sha256": sha,
                          "as_of": as_of, "report_markdown": report_md,
                          "summary": summary}, ensure_ascii=False, indent=2,
                         default=str)
    else:
        out = report_md
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(out, encoding="utf-8")
        tmp.replace(p)
        print(f"OK: wrote {p}")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
