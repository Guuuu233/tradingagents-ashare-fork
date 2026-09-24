"""DAV-1259 上线后价格返修监控脚本（只读，零 LLM）。

D-044 要求逐份审计价格返修上线（f075124，2026-09-25 01:05 CST）后的前 20 份
真实报告；D-036 要求结论来自已提交、可复跑的脚本。本脚本只读生产库
（``file:…?mode=ro``），不写任何库，不调用模型。

用法（仓库根目录，锁定解释器）：
    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
        work/dav1258-monitor/audit.py [--db PATH] [--since TS] [--limit N] [--user UID]

默认口径：
- --db     <repo>/data/tradingagents.db（按脚本所在仓库根解析）
- --since  2026-09-25 01:05:19（返修上线时刻，CST；库内 created_at 为本地朴素时间）
- --limit  20
- --user   429163f7-50b6-4982-8bdf-96ae99506843

口径说明：
- gate 前动作严格取 result_data.investment_debate_state.manager_verdict.trade_action
  （不是 decision_status.trade_action，后者已被降级覆盖）。
- 合格 clean（D-043）：analysis_status=VALID ∧ gate 前动作 ∈ {BUY,SELL,HOLD}
  ∧ price_basis_gate.status == "pass"。
- 模型调用次数只覆盖 llm_call_logs 表（分析师阶段口径，DAV-1249 已知缺口，
  非全量 HTTP 口径），输出中如实标注，不伪造全量数。
- 耗时 = updated_at - created_at（报告端到端墙钟）。

输出只写终端与 work/dav1258-monitor/out/（out/ 由本目录 .gitignore 排除）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT_DIR = os.path.join(HERE, "out")

DEFAULT_DB = os.path.join(REPO, "data", "tradingagents.db")
DEFAULT_SINCE = "2026-09-25 01:05:19"
DEFAULT_LIMIT = 20
DEFAULT_USER = "429163f7-50b6-4982-8bdf-96ae99506843"

SMOKE_REPORT_ID = "89584ca1fb534575a0611acf2ca612fc"
# 与总控签收评论逐项对齐的期望事实（smoke 89584ca1）
SMOKE_EXPECTED = {
    "triggered_roles": 5,
    "adopted_revised": 4,
    "discard_revised_check_failed": 1,
    "all_signatures_same": True,
    "analysis_status": "ABSTAIN",
    "qualified_clean": False,
}

EXECUTABLE_ACTIONS = {"BUY", "SELL", "HOLD"}
REVISION_VERSION = "price_ref_revision.v1"

_DT_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
               "%Y-%m-%dT%H:%M:%S")


def parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def load_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, (dict,)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def open_ro(db_path: str) -> sqlite3.Connection:
    """只读打开 SQLite（mode=ro），绝不写库。"""
    abspath = os.path.abspath(db_path)
    return sqlite3.connect(f"file:{abspath}?mode=ro", uri=True)


def _signature_same(rec: Mapping[str, Any]) -> Optional[bool]:
    orig = rec.get("orig_signature")
    revised = rec.get("revised_signature")
    if orig is None or revised is None:
        return None
    return orig == revised


def analyze_report_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """从 reports 行（含 result_data JSON）抽取审计字段。纯函数，便于夹具测试。"""
    rd = load_json(row.get("result_data"))
    ids = rd.get("investment_debate_state")
    mv = ids.get("manager_verdict") if isinstance(ids, Mapping) else None
    pre_gate_action = mv.get("trade_action") if isinstance(mv, Mapping) else None

    gate = rd.get("price_basis_gate")
    gate_status = gate.get("status") if isinstance(gate, Mapping) else None
    violations = gate.get("violations") if isinstance(gate, Mapping) else None
    violation_by_kind: Dict[str, int] = {}
    for v in violations or []:
        kind = v.get("kind") if isinstance(v, Mapping) else str(v)
        violation_by_kind[kind] = violation_by_kind.get(kind, 0) + 1

    revision = rd.get("price_ref_revision")
    roles: Dict[str, Dict[str, Any]] = {}
    if isinstance(revision, Mapping):
        for role_key, rec in revision.items():
            if not isinstance(rec, Mapping):
                continue
            roles[role_key] = {
                "triggered": bool(rec.get("triggered")),
                "revision_attempted": bool(rec.get("revision_attempted")),
                "problem_count": rec.get("problem_count"),
                "adopted": rec.get("adopted"),
                "discard_reason": rec.get("discard_reason"),
                "signature_same": _signature_same(rec),
                "post_revision_problem_count": rec.get("post_revision_problem_count"),
            }

    analysis_status = row.get("analysis_status") or rd.get("analysis_status")
    qualified_clean = (
        analysis_status == "VALID"
        and pre_gate_action in EXECUTABLE_ACTIONS
        and gate_status == "pass"
    )

    created = parse_dt(row.get("created_at"))
    updated = parse_dt(row.get("updated_at"))
    elapsed = (updated - created).total_seconds() if created and updated else None

    return {
        "report_id": row.get("id"),
        "symbol": row.get("symbol"),
        "trade_date": row.get("trade_date"),
        "created_at": row.get("created_at"),
        "status": row.get("status"),
        "error": row.get("error"),
        "analysis_status": analysis_status,
        "pre_gate_action": pre_gate_action,
        "gate_status": gate_status,
        "violation_count": sum(violation_by_kind.values()),
        "violations_by_kind": violation_by_kind,
        "qualified_clean": qualified_clean,
        "revision_version": rd.get("price_ref_revision_version"),
        "roles": roles,
        "elapsed_seconds": elapsed,
    }


def fetch_reports(conn: sqlite3.Connection, user: str, since: str,
                  limit: int) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT id, symbol, trade_date, status, error, analysis_status,"
        " result_data, created_at, updated_at"
        " FROM reports WHERE user_id = ? AND created_at >= ?"
        " ORDER BY created_at ASC, id ASC LIMIT ?",
        (user, since, limit),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_llm_counts(conn: sqlite3.Connection,
                     report_ids: List[str]) -> Dict[str, int]:
    """llm_call_logs 口径：仅分析师阶段（DAV-1249 已知缺口）。"""
    counts: Dict[str, int] = {}
    if not report_ids:
        return counts
    try:
        for rid in report_ids:
            n = conn.execute(
                "SELECT COUNT(*) FROM llm_call_logs WHERE report_id = ?",
                (rid,),
            ).fetchone()[0]
            counts[rid] = int(n)
    except sqlite3.Error:
        # 表不存在等情形：按不可得处理
        return {}
    return counts


def fetch_baseline_valid_ratio(conn: sqlite3.Connection, user: str,
                               since: str) -> Dict[str, Any]:
    """上线前 30 天同账户 completed 报告的 VALID 比例（按 created_at）。"""
    since_dt = parse_dt(since)
    window_start = (since_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S") \
        if since_dt else None
    row = conn.execute(
        "SELECT COUNT(*),"
        " SUM(CASE WHEN analysis_status = 'VALID' THEN 1 ELSE 0 END)"
        " FROM reports WHERE user_id = ? AND status = 'completed'"
        " AND created_at >= ? AND created_at < ?",
        (user, window_start, since),
    ).fetchone()
    total, valid = int(row[0] or 0), int(row[1] or 0)
    return {
        "window_start": window_start,
        "window_end": since,
        "completed_total": total,
        "valid": valid,
        "valid_ratio": (valid / total) if total else None,
    }


def summarize(records: List[Dict[str, Any]],
              baseline: Dict[str, Any]) -> Dict[str, Any]:
    completed = [r for r in records if r["status"] == "completed"]
    valid = [r for r in completed if r["analysis_status"] == "VALID"]
    clean = [r for r in records if r["qualified_clean"]]
    failed = [r for r in records if r["status"] == "failed"]
    failed_prr = [r for r in failed
                  if r.get("error") and "price_ref_revision" in str(r["error"])]

    roles_total = triggered = attempted = adopted_revised = 0
    sig_diff_adopted = 0
    reports_with_trigger = 0
    for r in records:
        any_trig = False
        for role in r["roles"].values():
            roles_total += 1
            if role["triggered"]:
                triggered += 1
                any_trig = True
            if role["revision_attempted"]:
                attempted += 1
            if role["adopted"] == "revised":
                adopted_revised += 1
                if role["signature_same"] is False:
                    sig_diff_adopted += 1
        if any_trig:
            reports_with_trigger += 1

    return {
        "reports": len(records),
        "completed": len(completed),
        "valid": len(valid),
        "valid_ratio": (len(valid) / len(completed)) if completed else None,
        "qualified_clean": len(clean),
        "baseline": baseline,
        "roles_total": roles_total,
        "roles_triggered": triggered,
        "roles_attempted": attempted,
        "roles_adopted_revised": adopted_revised,
        "reports_with_trigger": reports_with_trigger,
        "trigger_rate_reports": (reports_with_trigger / len(records)) if records else None,
        "trigger_rate_roles": (triggered / roles_total) if roles_total else None,
        "adopt_rate": (adopted_revised / attempted) if attempted else None,
        "sig_diff_adopted": sig_diff_adopted,
        "failed": len(failed),
        "failed_with_prr_error": len(failed_prr),
    }


def _fmt_ratio(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def format_report(records: List[Dict[str, Any]],
                  llm_counts: Dict[str, int],
                  summary: Dict[str, Any],
                  args: Mapping[str, Any]) -> str:
    lines: List[str] = []
    a = lines.append
    a("=" * 100)
    a(f"DAV-1259 上线后价格返修审计 | db={args['db']} | user={args['user']}"
      f" | since={args['since']} | limit={args['limit']}")
    a("口径：gate 前动作=investment_debate_state.manager_verdict.trade_action；"
      "合格 clean(D-043)=VALID ∧ 动作∈{BUY,SELL,HOLD} ∧ gate pass；"
      "LLM 计数=llm_call_logs（仅分析师阶段，DAV-1249 口径缺口，非全量）")
    a("=" * 100)
    for i, r in enumerate(records, 1):
        a(f"[{i:02d}] {r['report_id']} | {r['symbol']} | trade_date={r['trade_date']}"
          f" | created={r['created_at']} | status={r['status']}")
        a(f"     analysis_status={r['analysis_status']} | pre_gate_action={r['pre_gate_action']}"
          f" | gate={r['gate_status']} | violations={r['violation_count']}"
          f" {r['violations_by_kind'] or ''}")
        a(f"     qualified_clean={r['qualified_clean']}"
          f" | llm_calls={llm_counts.get(r['report_id'], 'n/a')}(analyst-phase)"
          f" | elapsed={('%.0fs' % r['elapsed_seconds']) if r['elapsed_seconds'] is not None else 'n/a'}")
        if r["status"] == "failed" and r.get("error"):
            a(f"     error={str(r['error'])[:200]}")
        if not r["roles"]:
            a("     price_ref_revision: (无记录)")
        for role_key, role in r["roles"].items():
            sig = role["signature_same"]
            sig_s = "same" if sig is True else ("DIFF" if sig is False else "n/a")
            adopted = role["adopted"] or "-"
            discard = f" discard={role['discard_reason']}" if role["discard_reason"] else ""
            a(f"     role={role_key:<16} triggered={role['triggered']}"
              f" problems={role['problem_count']} adopted={adopted}{discard}"
              f" sig={sig_s} post_problems={role['post_revision_problem_count']}")
    a("-" * 100)
    a("汇总")
    a(f"  审计报告数={summary['reports']} (completed={summary['completed']},"
      f" VALID={summary['valid']}, VALID 比例={_fmt_ratio(summary['valid_ratio'])})")
    bl = summary["baseline"]
    a(f"  对照：上线前 30 天同账户 completed VALID 比例"
      f" = {_fmt_ratio(bl['valid_ratio'])} ({bl['valid']}/{bl['completed_total']},"
      f" 窗口 {bl['window_start']} ~ {bl['window_end']})")
    a(f"  合格 clean 数={summary['qualified_clean']}")
    a(f"  返修触发率：报告级 {_fmt_ratio(summary['trigger_rate_reports'])}"
      f" ({summary['reports_with_trigger']}/{summary['reports']})；"
      f"角色级 {_fmt_ratio(summary['trigger_rate_roles'])}"
      f" ({summary['roles_triggered']}/{summary['roles_total']})")
    a(f"  返修采用率：{_fmt_ratio(summary['adopt_rate'])}"
      f" (adopted=revised {summary['roles_adopted_revised']}/{summary['roles_attempted']} attempted)")
    if summary["sig_diff_adopted"]:
        a(f"  !!! 报警：签名不同却被采用 = {summary['sig_diff_adopted']}（必须为 0）!!!")
    else:
        a(f"  签名不同却被采用 = {summary['sig_diff_adopted']}（必须为 0，当前正常）")
    a(f"  上线后 failed={summary['failed']}，其中 error 含 price_ref_revision"
      f" ={summary['failed_with_prr_error']}")
    return "\n".join(lines)


def smoke_self_check(conn: sqlite3.Connection) -> str:
    """对 smoke 89584ca1 逐项自检，与总控签收评论数据对齐。"""
    lines = ["-" * 100, f"smoke 自检：{SMOKE_REPORT_ID}"]
    row = conn.execute(
        "SELECT id, symbol, trade_date, status, error, analysis_status,"
        " result_data, created_at, updated_at FROM reports WHERE id = ?",
        (SMOKE_REPORT_ID,),
    ).fetchone()
    if row is None:
        lines.append("  未找到 smoke 报告（该库中不存在，跳过自检）")
        return "\n".join(lines)
    cols = ["id", "symbol", "trade_date", "status", "error", "analysis_status",
            "result_data", "created_at", "updated_at"]
    rec = analyze_report_row(dict(zip(cols, row)))
    roles = rec["roles"]
    actual = {
        "triggered_roles": sum(1 for r in roles.values() if r["triggered"]),
        "adopted_revised": sum(1 for r in roles.values() if r["adopted"] == "revised"),
        "discard_revised_check_failed": sum(
            1 for r in roles.values() if r["discard_reason"] == "revised_check_failed"),
        "all_signatures_same": all(
            r["signature_same"] is not False for r in roles.values()),
        "analysis_status": rec["analysis_status"],
        "qualified_clean": rec["qualified_clean"],
    }
    ok_all = True
    for k, exp in SMOKE_EXPECTED.items():
        act = actual[k]
        ok = act == exp
        ok_all &= ok
        lines.append(f"  [{'OK' if ok else 'MISMATCH'}] {k}: 期望 {exp} / 实际 {act}")
    lines.append(f"  smoke 自检总体：{'PASS' if ok_all else 'FAIL'}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--user", default=DEFAULT_USER)
    args = ap.parse_args(argv)

    conn = open_ro(args.db)
    try:
        rows = fetch_reports(conn, args.user, args.since, args.limit)
        records = [analyze_report_row(r) for r in rows]
        llm_counts = fetch_llm_counts(conn, [r["report_id"] for r in records])
        baseline = fetch_baseline_valid_ratio(conn, args.user, args.since)
        summary = summarize(records, baseline)
        out = format_report(records, llm_counts, summary, vars(args))
        out += "\n" + smoke_self_check(conn) + "\n"
    finally:
        conn.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(OUT_DIR, f"audit-{stamp}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(out)
    print(f"[written] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
