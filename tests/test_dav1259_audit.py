"""DAV-1259 work/dav1258-monitor/audit.py 单元测试（全合成夹具，不依赖生产库）。

夹具复用 DAV-1250 做法：tmp_path 建 SQLite，执行内联合成 INSERT；
smoke 自检用一条复刻 89584ca1 关键字段的合成记录。
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "work" / "dav1258-monitor"))
import audit  # noqa: E402

USER = audit.DEFAULT_USER
SINCE = audit.DEFAULT_SINCE


def _revision(triggered=False, adopted=None, discard=None, sig_same=True,
              problems=0):
    sig = {"verdict": {"direction": "中性"}, "decision": "HOLD"}
    rec = {
        "version": "price_ref_revision.v1",
        "triggered": triggered,
        "revision_attempted": triggered,
        "problems": [{"ref_id": f"pr-{i:03d}"} for i in range(problems)],
        "problem_count": problems,
        "adopted": adopted,
        "discard_reason": discard,
        "post_revision_problem_count": 0 if adopted == "revised" else None,
        "orig_signature": dict(sig),
        "revised_signature": dict(sig),
    }
    if not sig_same:
        rec["revised_signature"] = {"verdict": {"direction": "偏多"},
                                    "decision": "BUY"}
    if adopted is None and not triggered:
        rec["revision_attempted"] = False
        rec["orig_signature"] = rec["revised_signature"] = None
    return rec


def _result_data(analysis_status="VALID", mv_action="BUY", gate_status="pass",
                 violations=None, revision=None):
    return json.dumps({
        "analysis_status": analysis_status,
        "investment_debate_state": {
            "manager_verdict": {"trade_action": mv_action},
        },
        "decision_status": {"trade_action": "NO_TRADE"},
        "price_basis_gate": {"status": gate_status,
                             "violations": violations or []},
        "price_ref_revision": revision or {},
        "price_ref_revision_version": "price_ref_revision.v1",
    })


def _smoke_result_data():
    """复刻 89584ca1 的关键审计字段（合成）：5 触发、4 采用、1 丢弃
    revised_check_failed、签名全一致、ABSTAIN、非 clean。"""
    roles = {
        "macro": _revision(True, "revised", problems=3),
        "social": _revision(False),
        "fundamentals": _revision(True, "revised", problems=2),
        "news": _revision(True, "revised", problems=1),
        "smart_money": _revision(True, "revised", problems=1),
        "research_manager": _revision(True, "original",
                                      discard="revised_check_failed",
                                      problems=1),
    }
    return _result_data(analysis_status="ABSTAIN", mv_action="NO_TRADE",
                        gate_status="pass", revision=roles)


SCHEMA = """
CREATE TABLE reports (
    id TEXT, user_id TEXT, symbol TEXT, trade_date TEXT, status TEXT,
    error TEXT, analysis_status TEXT, result_data TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE llm_call_logs (
    id TEXT, report_id TEXT, agent_name TEXT, elapsed_seconds REAL,
    created_at TEXT
);
"""


def _insert_report(conn, rid, user=USER, symbol="600519.SH",
                   trade_date="2026-09-25", status="completed", error=None,
                   analysis_status="VALID", result_data=None,
                   created_at="2026-09-25 08:00:00",
                   updated_at="2026-09-25 08:10:00"):
    conn.execute(
        "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rid, user, symbol, trade_date, status, error, analysis_status,
         result_data or _result_data(analysis_status=analysis_status),
         created_at, updated_at),
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "fixture.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    # 上线后 3 份：clean VALID、ABSTAIN 非 clean、failed(含 prr 错误)
    _insert_report(conn, "post-clean", created_at="2026-09-25 08:00:00",
                   result_data=_result_data(
                       "VALID", "BUY", "pass",
                       revision={"macro": _revision(True, "revised", problems=1),
                                 "news": _revision(False)}))
    _insert_report(conn, "post-abstain", created_at="2026-09-25 09:00:00",
                   analysis_status="ABSTAIN",
                   result_data=_result_data(
                       "ABSTAIN", "NO_TRADE", "pass",
                       revision={"macro": _revision(True, "original",
                                                    discard="conclusion_changed",
                                                    problems=2)}))
    _insert_report(conn, "post-fail", status="failed",
                   error="price_ref_revision boom", analysis_status=None,
                   result_data="{}", created_at="2026-09-25 10:00:00",
                   updated_at="2026-09-25 10:01:00")
    # 上线前 30 天对照：3 completed（2 VALID + 1 ABSTAIN）+ 1 failed
    for i, st in enumerate(["VALID", "VALID", "ABSTAIN"]):
        _insert_report(conn, f"pre-{i}", created_at=f"2026-09-0{i+1} 08:00:00",
                       analysis_status=st,
                       result_data=_result_data(st, "BUY", "pass"))
    _insert_report(conn, "pre-fail", status="failed", error="x",
                   analysis_status=None, created_at="2026-09-02 08:00:00")
    # 其他用户 + 窗口外：不应计入
    _insert_report(conn, "other-user", user="other", created_at="2026-09-25 08:00:00")
    _insert_report(conn, "too-old", created_at="2026-08-01 08:00:00")
    # smoke 报告
    _insert_report(conn, audit.SMOKE_REPORT_ID, analysis_status="ABSTAIN",
                   result_data=_smoke_result_data(),
                   created_at="2026-09-24 17:07:35",
                   updated_at="2026-09-24 17:12:51")
    conn.execute("INSERT INTO llm_call_logs VALUES ('l1','post-clean','a',1.0,'2026-09-25 08:01:00')")
    conn.execute("INSERT INTO llm_call_logs VALUES ('l2','post-clean','b',2.0,'2026-09-25 08:02:00')")
    conn.commit()
    conn.close()
    return str(path)


def _ro(path):
    return audit.open_ro(path)


def test_open_ro_is_read_only(db):
    conn = _ro(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO reports VALUES ('x','u','s','d','c',NULL,NULL,NULL,NULL,NULL)")
    conn.close()


def test_fetch_reports_window_and_order(db):
    conn = _ro(db)
    rows = audit.fetch_reports(conn, USER, SINCE, 20)
    ids = [r["id"] for r in rows]
    # since=09-25 01:05:19 → 只含 3 份上线后报告，按 created_at 升序
    assert ids == ["post-clean", "post-abstain", "post-fail"]
    conn.close()


def test_analyze_report_row_clean(db):
    conn = _ro(db)
    rows = audit.fetch_reports(conn, USER, SINCE, 20)
    recs = {r["id"]: audit.analyze_report_row(r) for r in rows}
    c = recs["post-clean"]
    assert c["qualified_clean"] is True
    assert c["pre_gate_action"] == "BUY"  # manager_verdict，非 decision_status
    assert c["gate_status"] == "pass"
    assert c["elapsed_seconds"] == 600
    assert c["roles"]["macro"]["triggered"] is True
    assert c["roles"]["macro"]["signature_same"] is True
    a = recs["post-abstain"]
    assert a["qualified_clean"] is False
    assert a["roles"]["macro"]["discard_reason"] == "conclusion_changed"
    assert recs["post-fail"]["roles"] == {}
    conn.close()


def test_gate_violations_by_kind(db):
    rd = _result_data("VALID", "SELL", "fail", violations=[
        {"kind": "decision_driving_unspecified_basis"},
        {"kind": "decision_driving_unspecified_basis"},
        {"kind": "invalid_conversion"},
    ])
    row = {"id": "x", "symbol": "s", "trade_date": "d", "status": "completed",
           "error": None, "analysis_status": "VALID", "result_data": rd,
           "created_at": "2026-09-25 08:00:00", "updated_at": None}
    rec = audit.analyze_report_row(row)
    assert rec["violation_count"] == 3
    assert rec["violations_by_kind"] == {
        "decision_driving_unspecified_basis": 2, "invalid_conversion": 1}
    assert rec["qualified_clean"] is False  # gate fail → 非 clean


def test_sig_diff_adopted_alarm(db):
    rd = _result_data("VALID", "BUY", "pass", revision={
        "macro": _revision(True, "revised", sig_same=False, problems=1)})
    row = {"id": "x", "symbol": "s", "trade_date": "d", "status": "completed",
           "error": None, "analysis_status": "VALID", "result_data": rd,
           "created_at": "2026-09-25 08:00:00", "updated_at": None}
    rec = audit.analyze_report_row(row)
    summary = audit.summarize([rec], {"valid_ratio": None, "valid": 0,
                                      "completed_total": 0,
                                      "window_start": None, "window_end": None})
    assert summary["sig_diff_adopted"] == 1
    out = audit.format_report([rec], {}, summary,
                              {"db": "x", "user": "u", "since": "s", "limit": 1})
    assert "!!! 报警" in out


def test_summary_and_baseline(db):
    conn = _ro(db)
    rows = audit.fetch_reports(conn, USER, SINCE, 20)
    recs = [audit.analyze_report_row(r) for r in rows]
    bl = audit.fetch_baseline_valid_ratio(conn, USER, SINCE)
    # 窗口 08-26~09-25：pre-0/1/2 三份 completed（VALID=2）+ smoke(ABSTAIN)，
    # pre-fail 不计，too-old(08-01) 出窗
    assert bl["completed_total"] == 4 and bl["valid"] == 2
    s = audit.summarize(recs, bl)
    assert s["reports"] == 3 and s["completed"] == 2 and s["valid"] == 1
    assert s["qualified_clean"] == 1
    assert s["failed"] == 1 and s["failed_with_prr_error"] == 1
    assert s["roles_triggered"] == 2 and s["roles_attempted"] == 2
    assert s["roles_adopted_revised"] == 1
    assert s["sig_diff_adopted"] == 0
    counts = audit.fetch_llm_counts(conn, [r["report_id"] for r in recs])
    assert counts["post-clean"] == 2
    conn.close()


def test_smoke_self_check(db):
    conn = _ro(db)
    out = audit.smoke_self_check(conn)
    assert "smoke 自检总体：PASS" in out
    assert "MISMATCH" not in out
    conn.close()


def test_smoke_self_check_missing(tmp_path):
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.commit(); conn.close()
    conn = _ro(str(path))
    out = audit.smoke_self_check(conn)
    assert "未找到" in out
    conn.close()


def test_main_writes_out(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(audit, "OUT_DIR", str(tmp_path / "out"))
    rc = audit.main(["--db", db])
    assert rc == 0
    files = list((tmp_path / "out").glob("audit-*.txt"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "post-clean" in text and "smoke 自检总体：PASS" in text
