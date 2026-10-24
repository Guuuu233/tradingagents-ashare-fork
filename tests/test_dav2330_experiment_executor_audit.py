"""DAV-2330: execution order, audit persistence and owner-scoped fallback.

All responses are in-memory stubs; no provider or API service is contacted.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from scripts.phase2 import experiment_executor as mod
from tests.test_dav1882_experiment_executor import (
    EXPECTED_MODEL,
    FIXED_ACCOUNT,
    StubServer,
    _executor,
    _manifest,
    _pair,
    _two_servers,
)


class AuditServer(StubServer):
    def __init__(self, name, trace, *, report_code=200, report_account=FIXED_ACCOUNT,
                 result_account=None, plan=None):
        super().__init__(f"http://stub-{name}:8021", plan or {}, name=name)
        self.trace = trace
        self.report_code = report_code
        self.fallback_account = report_account
        self.report_account = result_account
        self.timestamps = {
            "created_at": "2026-10-24T10:00:00+08:00",
            "started_at": "2026-10-24T10:00:01+08:00",
            "finished_at": "2026-10-24T10:00:05+08:00",
        }

    def handle(self, instance, method, path, body):
        if path.startswith("/v1/reports/"):
            self.trace.append(f"REPORT:{instance}")
            return self.report_code, {"user_id": self.fallback_account}
        action = "POST" if method == "POST" else (
            "RESULT" if path.endswith("/result") else "POLL"
        )
        self.trace.append(f"{action}:{instance}")
        code, payload = super().handle(instance, method, path, body)
        if action == "POLL":
            payload.update(self.timestamps)
        return code, payload


def audit_executor(tmp_path, **server_options):
    StubServer._store = {}
    StubServer._store_meta = {}
    trace = []
    servers = {
        name: AuditServer(name, trace, **server_options) for name in ("O", "N")
    }
    ex = _executor(tmp_path, servers, pair_retries=0)
    return ex, servers, trace


def read_record(ex):
    return json.loads(ex.ledger.ledger_path.read_text(encoding="utf-8").splitlines()[0])


@pytest.mark.parametrize("concurrent", [False, True])
def test_submission_order_and_per_arm_accounting(tmp_path, concurrent):
    ex, _, trace = audit_executor(tmp_path, result_account=FIXED_ACCOUNT)
    assert ex.concurrent_arms is False
    assert ex.allow_nontrading_day is False
    ex.concurrent_arms = concurrent
    report = ex.run(_manifest([_pair("p1")]))
    expected = (
        ["POST:O", "POST:N", "POLL:O", "RESULT:O", "POLL:N", "RESULT:N"]
        if concurrent else
        ["POST:O", "POLL:O", "RESULT:O", "POST:N", "POLL:N", "RESULT:N"]
    )
    assert trace == expected
    assert report.pairs_ok == 1
    rec = read_record(ex)
    assert (rec["analysis_requests"], rec["http_requests"]) == (2, 6)
    assert (rec["prompt_tokens"], rec["completion_tokens"], rec["total_tokens"]) == (2000, 400, 2400)
    assert [r["instance"] for r in rec["runs"]] == ["O", "N"]
    assert [r["job_id"] for r in rec["runs"]] == ["O-job-1", "N-job-1"]
    assert len({r["run_identity"]["run_uuid"] for r in rec["runs"]}) == 2
    assert all(r["run_identity_confirmed"] for r in rec["runs"])


@pytest.mark.parametrize("allowed", [False, True])
def test_nontrading_override_persists_without_becoming_stop(tmp_path, monkeypatch, allowed):
    monkeypatch.setattr(mod, "is_trading_day", lambda _date: False)
    now = lambda: datetime(2026, 10, 24, 10, 0, tzinfo=mod.CN_TZ)
    servers = _two_servers()
    ex = _executor(tmp_path, servers, now=now)
    ex.allow_nontrading_day = allowed
    report = ex.run(_manifest([_pair("p1")]))
    state = json.loads(ex.ledger.state_path.read_text(encoding="utf-8"))
    if not allowed:
        assert report.stop_reason == mod.STOP_WEEKEND
        assert report.pairs_run == 0
        assert all(not srv.calls for srv in servers.values())
        assert state.get("nontrading_day_override") is None
        return
    assert report.stop_reason is None
    assert report.pairs_ok == 1
    override = state["nontrading_day_override"]
    assert override == {
        "date": "2026-10-24",
        "flag": "--allow-nontrading-day",
        "reason": "DAV-2325 总控 10-24 定案授权周六执行",
    }
    assert state["stop_reason"] is None and state["stop_detail"] is None
    reloaded = mod.Ledger(ex.ledger.state_dir, now=now)
    assert reloaded.nontrading_day_override == override
    assert reloaded.stop_reason is None
    reloaded.flush()
    assert json.loads(reloaded.state_path.read_text())["nontrading_day_override"] == override
    resumed = _executor(tmp_path, _two_servers(), now=now)
    resumed.allow_nontrading_day = True
    assert resumed.run(_manifest([_pair("p1")])).pairs_skipped_done == 1


@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("report_code,account,outcome,reason,source", [
    (200, FIXED_ACCOUNT, "ok", None, "reports_endpoint"),
    (200, "another-user", "failed", mod.PREFLIGHT_ACCOUNT_MISMATCH, "reports_endpoint"),
    (404, None, "failed", mod.PREFLIGHT_ACCOUNT_UNAVAILABLE, None),
    (200, None, "failed", mod.PREFLIGHT_ACCOUNT_UNAVAILABLE, None),
    (503, None, "failed", mod.PREFLIGHT_ACCOUNT_UNAVAILABLE, None),
])
def test_report_fallback_outcomes(tmp_path, concurrent, report_code, account, outcome, reason, source):
    ex, _, trace = audit_executor(tmp_path, report_code=report_code, report_account=account)
    ex.concurrent_arms = concurrent
    report = ex.run(_manifest([_pair("p1"), _pair("p2")]))
    rec = read_record(ex)
    assert rec["outcome"] == outcome
    assert (rec["analysis_requests"], rec["http_requests"]) == (2, 8)
    for run in rec["runs"]:
        assert run["report_account"] == account
        assert run["report_account_source"] == source
        assert run["report_account_http_status"] == report_code
        if reason:
            assert run["status"] == "account_unverified"
            assert run["gate_reason"].startswith(reason)
    assert trace.count("REPORT:O") >= 1 and trace.count("REPORT:N") >= 1
    if reason:
        assert report.stop_reason == mod.STOP_RUN_GUARD
        assert report.pairs_failed == 1 and report.pairs_rerun == 0
        assert ex.ledger.pair_state("p2").status == "pending"
    else:
        assert report.pairs_ok == 2


def test_wrong_result_account_is_never_overridden_by_fallback(tmp_path):
    ex, _, trace = audit_executor(tmp_path, result_account="another-user")
    report = ex.run(_manifest([_pair("p1")]))
    assert report.stop_reason == mod.STOP_RUN_GUARD
    assert not any(action.startswith("REPORT:") for action in trace)
    assert all(r["report_account_source"] == "result_endpoint" for r in read_record(ex)["runs"])


def test_disabled_account_check_still_records_fallback_requests(tmp_path):
    ex, _, trace = audit_executor(tmp_path, report_code=404)
    ex.expect_account = None
    assert ex.run(_manifest([_pair("p1")])).pairs_ok == 1
    rec = read_record(ex)
    assert (rec["analysis_requests"], rec["http_requests"]) == (2, 8)
    assert trace.count("REPORT:O") == trace.count("REPORT:N") == 1
    assert all(r["report_account_source"] is None for r in rec["runs"])


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_server_timestamps_reach_ledger(tmp_path, status):
    ex, servers, _ = audit_executor(
        tmp_path, plan={"default": {"status": status, "model": EXPECTED_MODEL, "error": "boom"}}
    )
    ex.run(_manifest([_pair("p1")]))
    for run in read_record(ex)["runs"]:
        assert {key: run[key] for key in servers[run["instance"]].timestamps} == servers[run["instance"]].timestamps


def test_missing_server_timestamps_are_null_without_client_substitution(tmp_path):
    ex = _executor(tmp_path, _two_servers())
    ex.run(_manifest([_pair("p1")]))
    for run in read_record(ex)["runs"]:
        assert {key: run[key] for key in ("created_at", "started_at", "finished_at")} == {
            "created_at": None, "started_at": None, "finished_at": None,
        }


def test_client_poll_elapsed_excludes_wait_for_previous_arm(tmp_path, monkeypatch):
    ex, servers, _ = audit_executor(tmp_path, result_account=FIXED_ACCOUNT)
    ex.concurrent_arms = True
    clock = {"seconds": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["seconds"])
    original = servers["O"].handle

    def slow_poll(instance, method, path, body):
        if path.startswith("/v1/jobs/") and not path.endswith("/result"):
            clock["seconds"] += 7.0
        return original(instance, method, path, body)

    monkeypatch.setattr(servers["O"], "handle", slow_poll)
    ex.run(_manifest([_pair("p1")]))
    rec = read_record(ex)
    assert rec["elapsed_seconds"] == 7.0
    assert [r["elapsed_seconds"] for r in rec["runs"]] == [7.0, 0.0]
    assert all(r["started_at"] == "2026-10-24T10:00:01+08:00" for r in rec["runs"])


@pytest.mark.parametrize("failure", ["unknown_instance", "server_error"])
def test_concurrent_arm_failure_still_collects_other_arm(tmp_path, failure):
    ex, servers, trace = audit_executor(tmp_path, result_account=FIXED_ACCOUNT)
    ex.concurrent_arms = True
    if failure == "unknown_instance":
        instances = ("O", "ZZ")
    else:
        instances = ("O", "N")
        servers["N"].plan = {"default": {"http_status": 500}}
    report = ex.run(_manifest([_pair("p1", instances=instances)]))
    rec = read_record(ex)
    assert report.pairs_failed == 1
    assert rec["runs"][0]["status"] == "completed"
    assert rec["runs"][1]["status"] == ("unknown_instance" if failure == "unknown_instance" else "submit_failed")
    assert rec["total_tokens"] == 1200
    assert "RESULT:O" in trace
    assert rec["analysis_requests"] == (1 if failure == "unknown_instance" else 2)


def test_cli_override_switches_default_off_and_opt_in():
    argv = ["run", "--manifest", "stub.json", "--state-dir", "state", "--expected-model", EXPECTED_MODEL]
    args = mod.build_parser().parse_args(argv)
    assert args.concurrent_arms is False and args.allow_nontrading_day is False
    args = mod.build_parser().parse_args(argv + ["--concurrent-arms", "--allow-nontrading-day"])
    assert args.concurrent_arms is True and args.allow_nontrading_day is True
