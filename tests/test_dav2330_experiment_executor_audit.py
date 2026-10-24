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
                 result_account=None, plan=None, poll_code=200,
                 report_raises=False, report_body=None):
        super().__init__(f"http://stub-{name}:8021", plan or {}, name=name)
        self.trace = trace
        self.report_code = report_code
        self.fallback_account = report_account
        self.report_account = result_account
        self.poll_code = poll_code
        self.report_raises = report_raises
        self.report_body = report_body
        self.timestamps = {
            "created_at": "2026-10-24T10:00:00+08:00",
            "started_at": "2026-10-24T10:00:01+08:00",
            "finished_at": "2026-10-24T10:00:05+08:00",
        }

    def handle(self, instance, method, path, body):
        if path.startswith("/v1/reports/"):
            self.trace.append(f"REPORT:{instance}")
            if self.report_raises:
                raise RuntimeError("stub /v1/reports 不可达")
            if self.report_body is not None:
                return self.report_code, self.report_body
            return self.report_code, {"user_id": self.fallback_account}
        action = "POST" if method == "POST" else (
            "RESULT" if path.endswith("/result") else "POLL"
        )
        self.trace.append(f"{action}:{instance}")
        if action == "POLL" and self.poll_code != 200:
            return self.poll_code, {"detail": "poll unavailable"}
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
    """取第一条**尝试**记录：``ledger.jsonl`` 里还有 ``type=event`` 的留痕行。"""
    for line in ex.ledger.ledger_path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if payload.get("type") != "event":
            return payload
    raise AssertionError("ledger.jsonl 里没有尝试记录")


def test_override_flush_is_immediate_without_run(tmp_path, monkeypatch):
    """🟡#3：放行必须「立即」落盘，不依赖 run() 收尾时那一次 flush。

    变异「删掉 wait_for_daily_gate 里的 self.ledger.flush()」后本用例必须失败：
    Ledger 初始化不写 state.json，该文件能出现只可能来自放行分支。
    """
    monkeypatch.setattr(mod, "is_trading_day", lambda _date: False)
    ex = _executor(
        tmp_path, _two_servers(),
        now=lambda: datetime(2026, 10, 31, 10, 0, tzinfo=mod.CN_TZ),
    )
    ex.allow_nontrading_day = True
    ex.nontrading_day_reason = "总控 10-31 书面授权"
    state_path = tmp_path / "state" / "state.json"
    assert not state_path.exists()
    assert ex.wait_for_daily_gate() is False
    override = json.loads(state_path.read_text(encoding="utf-8"))["nontrading_day_override"]
    assert override == {
        "date": "2026-10-31",
        "flag": "--allow-nontrading-day",
        "reason": "总控 10-31 书面授权",
    }


def test_override_reason_follows_window_instead_of_being_hardcoded(tmp_path, monkeypatch):
    """🟡#1：理由随窗口传入，换窗口不能沿用上一次的文案。"""
    monkeypatch.setattr(mod, "is_trading_day", lambda _date: False)
    first = _executor(
        tmp_path, _two_servers(),
        now=lambda: datetime(2026, 10, 24, 10, 0, tzinfo=mod.CN_TZ),
    )
    first.allow_nontrading_day = True
    first.nontrading_day_reason = "  总控 10-24 授权周六窗口  "
    assert first.wait_for_daily_gate() is False
    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["nontrading_day_override"] == {
        "date": "2026-10-24",
        "flag": "--allow-nontrading-day",
        "reason": "总控 10-24 授权周六窗口",
    }
    assert "DAV-2325" not in json.dumps(state, ensure_ascii=False)

    second = _executor(
        tmp_path, _two_servers(),
        now=lambda: datetime(2026, 10, 31, 10, 0, tzinfo=mod.CN_TZ),
    )
    second.allow_nontrading_day = True
    second.nontrading_day_reason = "   "          # 空白 = 未记录理由
    assert second.wait_for_daily_gate() is False
    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["nontrading_day_override"] == {
        "date": "2026-10-31",
        "flag": "--allow-nontrading-day",
        "reason": None,
    }


def test_two_authorized_windows_leave_two_findable_traces(tmp_path, monkeypatch):
    """🟡#5：同一 state-dir 先后两个授权窗口，两条留痕都必须能查到。

    对应实测缺陷：`state.json` 单槽被下一个窗口覆盖，第一条丢。现在单槽只留
    最近一次，历史窗口靠 `ledger.jsonl` 的追加事件行保留，两条各自可查。
    """
    monkeypatch.setattr(mod, "is_trading_day", lambda _date: False)
    windows = (
        (datetime(2026, 10, 24, 10, 0, tzinfo=mod.CN_TZ), "总控 10-24 授权周六窗口"),
        (datetime(2026, 10, 31, 10, 0, tzinfo=mod.CN_TZ), None),
    )
    for when, reason in windows:
        ex = _executor(tmp_path, _two_servers(), now=lambda w=when: w)
        ex.allow_nontrading_day = True
        ex.nontrading_day_reason = reason
        assert ex.wait_for_daily_gate() is False

    records = [
        json.loads(line) for line in
        (tmp_path / "state" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    events = [rec for rec in records if rec.get("type") == "event"]
    assert [(e["date"], e["reason"]) for e in events] == [
        ("2026-10-24", "总控 10-24 授权周六窗口"),
        ("2026-10-31", None),
    ]
    assert all(e["event"] == "nontrading_day_override" for e in events)
    assert all(e["flag"] == "--allow-nontrading-day" for e in events)
    assert all(e["recorded_at"] for e in events)

    # 单槽仍是最近一次（这是设计，历史已在事件行里）
    slot = json.loads(
        (tmp_path / "state" / "state.json").read_text(encoding="utf-8")
    )["nontrading_day_override"]
    assert slot == {"date": "2026-10-31", "flag": "--allow-nontrading-day", "reason": None}

    # 残留单槽不得被当成本次授权：交易日不带开关的跑，RunReport 里必须是 None
    monkeypatch.setattr(mod, "is_trading_day", lambda _date: True)
    later = _executor(
        tmp_path, _two_servers(),
        now=lambda: datetime(2026, 11, 2, 20, 0, tzinfo=mod.CN_TZ),
    )
    report = later.run(_manifest([_pair("p1")]))
    assert report.nontrading_day_override is None
    assert report.pairs_ok == 1


def test_main_writes_override_into_run_report_and_ledger(tmp_path, monkeypatch):
    """🟡#5 端到端：真 main() 写出的 `run_report.json` 带授权留痕，ledger 有事件行。"""
    from urllib.parse import urlparse

    servers = _two_servers()
    original = mod.HttpTransport

    class _Dispatch(original):
        def __init__(self, *args, **kwargs):
            super().__init__(session=None)

        def request(self, method, url, *, headers=None, json_body=None):
            server = servers["O"] if ":8021" in url else servers["N"]
            return server.handle(server.name, method, urlparse(url).path, json_body or {})

    monkeypatch.setattr(mod, "HttpTransport", _Dispatch)
    monkeypatch.setattr(mod, "is_trading_day", lambda _date: False)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "name": "stub",
        "frozen_models": [EXPECTED_MODEL],
        "pairs": [_pair("p1")],
        "batches": [],
    }), encoding="utf-8")
    state_dir = tmp_path / "state"
    code = mod.main([
        "run", "--manifest", str(manifest_path), "--state-dir", str(state_dir),
        "--expected-model", EXPECTED_MODEL,
        "--instance", "O=http://stub:8021", "--instance", "N=http://stub:8022",
        "--token", "O=stub-token", "--token", "N=stub-token",
        "--allow-nontrading-day", "--nontrading-day-reason", "总控 10-31 授权",
        "--poll-interval", "0",
    ])
    assert code == 0
    today = datetime.now(mod.CN_TZ).date().isoformat()
    payload = json.loads((state_dir / "run_report.json").read_text(encoding="utf-8"))
    assert payload["nontrading_day_override"] == {
        "date": today,
        "flag": "--allow-nontrading-day",
        "reason": "总控 10-31 授权",
    }
    assert payload["stop_reason"] is None and payload["stop_detail"] is None
    assert payload["pairs_ok"] == 1

    events = [
        json.loads(line) for line in
        (state_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    events = [rec for rec in events if rec.get("type") == "event"]
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "nontrading_day_override"
    assert {key: event[key] for key in ("date", "flag", "reason")} == {
        "date": today,
        "flag": "--allow-nontrading-day",
        "reason": "总控 10-31 授权",
    }
    assert isinstance(event["recorded_at"], str) and event["recorded_at"]


@pytest.mark.parametrize("raw", ["2026-10-24", 42, ["x"], {"hack": "y"}])
def test_override_readback_drops_non_dict_and_unknown_only_payloads(tmp_path, raw):
    """🟢 state.json 可被外部改：读回只认 dict + 已知键。"""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps({"nontrading_day_override": raw}), encoding="utf-8"
    )
    assert mod.Ledger(state_dir).nontrading_day_override is None


def test_override_readback_filters_unknown_keys(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(json.dumps({
        "nontrading_day_override": {
            "date": "2026-10-24",
            "flag": "--allow-nontrading-day",
            "reason": None,
            "hack": "injected",
            "note": 1,
        }
    }, ensure_ascii=False), encoding="utf-8")
    assert mod.Ledger(state_dir).nontrading_day_override == {
        "date": "2026-10-24",
        "flag": "--allow-nontrading-day",
        "reason": None,
    }


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
        assert report.nontrading_day_override is None
        assert all(not srv.calls for srv in servers.values())
        assert state.get("nontrading_day_override") is None
        return
    assert report.stop_reason is None
    # 🟡#4：放行不得借用停批字段——run_report.json 是随批交付的主产物，
    # 被污染时不能只靠 state.json（窗口类原因本就不落盘）蒙过去。
    assert report.stop_detail is None
    # 🟡#5：授权留痕现在必须进主产物，且只记「本次运行」发生的那一条
    assert report.nontrading_day_override == {
        "date": "2026-10-24",
        "flag": "--allow-nontrading-day",
        "reason": None,
    }
    assert report.to_json()["nontrading_day_override"] == report.nontrading_day_override
    assert report.pairs_ok == 1
    override = state["nontrading_day_override"]
    assert override == {
        "date": "2026-10-24",
        "flag": "--allow-nontrading-day",
        # 🟡#1：未传 --nontrading-day-reason → null，不再写死工单号
        "reason": None,
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


@pytest.mark.parametrize("plan,poll_code,status", [
    ({"default": {"http_status": 500}}, 200, "submit_failed"),
    ({}, 503, "status_failed"),
])
def test_failed_paths_keep_audit_keys_as_null(tmp_path, plan, poll_code, status):
    """🟡#2：提交/轮询失败时时刻与归属字段必须是 null，而不是整个键缺失。"""
    ex, _, _ = audit_executor(tmp_path, plan=plan, poll_code=poll_code)
    ex.run(_manifest([_pair("p1")]))
    for run in read_record(ex)["runs"]:
        assert run["status"] == status
        assert {key: run[key] for key in ("created_at", "started_at", "finished_at")} == {
            "created_at": None, "started_at": None, "finished_at": None,
        }
        assert run["report_account"] is None
        assert run["report_account_source"] is None
        assert run["report_account_http_status"] is None


def test_unknown_instance_run_info_keeps_audit_keys(tmp_path):
    """🟡#2：run_pair 自造的 unknown_instance 明细同样不能缺键。"""
    ex = _executor(tmp_path, _two_servers(), pair_retries=0)
    ex.run(_manifest([_pair("p1", instances=("O", "ZZ"))]))
    run = read_record(ex)["runs"][1]
    assert run["status"] == "unknown_instance"
    expected = {
        "created_at": None, "started_at": None, "finished_at": None,
        "report_account": None, "report_account_source": None,
        "report_account_http_status": None,
    }
    assert {key: run[key] for key in expected} == expected


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


def test_reports_transport_error_records_zero_status_and_fails_closed(tmp_path):
    """🟢 补测：补读抛传输异常 → 状态记 0、仍 fail-closed、不污染分析请求预算。"""
    ex, _, trace = audit_executor(tmp_path, report_raises=True)
    report = ex.run(_manifest([_pair("p1")]))
    rec = read_record(ex)
    assert (rec["analysis_requests"], rec["http_requests"]) == (2, 8)
    assert report.stop_reason == mod.STOP_RUN_GUARD
    assert trace.count("REPORT:O") == trace.count("REPORT:N") == 1
    for run in rec["runs"]:
        assert run["report_account_http_status"] == 0
        assert run["report_account"] is None
        assert run["status"] == "account_unverified"
        assert run["gate_reason"].startswith(mod.PREFLIGHT_ACCOUNT_UNAVAILABLE)


def test_reports_200_non_dict_body_stays_fail_closed(tmp_path):
    """🟢 补测：补读 200 但响应体不是 dict → 仍 fail-closed。"""
    ex, _, trace = audit_executor(tmp_path, report_body=["not-a-dict"])
    report = ex.run(_manifest([_pair("p1")]))
    rec = read_record(ex)
    assert (rec["analysis_requests"], rec["http_requests"]) == (2, 8)
    assert report.pairs_failed == 1
    assert trace.count("REPORT:O") == trace.count("REPORT:N") == 1
    for run in rec["runs"]:
        assert run["report_account_http_status"] == 200
        assert run["report_account"] is None
        assert run["status"] == "account_unverified"


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
    assert args.nontrading_day_reason is None
    args = mod.build_parser().parse_args(argv + ["--concurrent-arms", "--allow-nontrading-day"])
    assert args.concurrent_arms is True and args.allow_nontrading_day is True
    args = mod.build_parser().parse_args(argv + ["--nontrading-day-reason", "总控 10-24 授权"])
    assert args.nontrading_day_reason == "总控 10-24 授权"


def test_cli_passes_nontrading_reason_through_to_executor(tmp_path, monkeypatch):
    """🟡#1 联动：`--nontrading-day-reason` 必须真的传到执行器，不能只解析不落地。"""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "name": "stub",
        "frozen_models": [EXPECTED_MODEL],
        "pairs": [_pair("p1")],
        "batches": [],
    }), encoding="utf-8")
    captured = {}

    class FakeReport:
        def to_json(self):
            return {"stop_reason": None}

    class FakeExecutor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self, manifest):
            return FakeReport()

    monkeypatch.setattr(mod, "ExperimentExecutor", FakeExecutor)
    code = mod.main([
        "run", "--manifest", str(manifest_path),
        "--state-dir", str(tmp_path / "state"),
        "--expected-model", EXPECTED_MODEL,
        "--instance", "O=http://stub:8021",
        "--token", "O=stub-token",
        "--allow-nontrading-day",
        "--nontrading-day-reason", "总控 10-24 授权",
    ])
    assert code == 0
    assert captured["allow_nontrading_day"] is True
    assert captured["nontrading_day_reason"] == "总控 10-24 授权"
