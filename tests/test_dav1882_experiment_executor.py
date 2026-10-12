"""DAV-1882 实验执行器桩服务测试：作废 / 续跑 / 停批 三条路径。

桩服务在内存里，不起真进程、不连真模型、不写生产库。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase2.experiment_executor import (  # noqa: E402
    CN_TZ,
    BatchSpec,
    daily_batch_gate_open,
    fetch_batch_pairs,
    load_batch_specs,
    ExperimentExecutor,
    HttpTransport,
    Ledger,
    Manifest,
    StopConfig,
    blackout_active,
    classify_error,
    flatten_served_models,
    verify_pair,
)

EXPECTED_MODEL = "deepseek-v3.2-exp-frozen"
FROZEN = (EXPECTED_MODEL,)


# ---------------------------------------------------------------------------
# 桩服务
# ---------------------------------------------------------------------------


class StubServer:
    """一台假实例。

    ``plan[pair_key]`` 决定这一次 ``/v1/analyze`` 的行为（pair_key = "symbol@trade_date"）：
    ``{"status": "completed"|"failed", "model": ..., "missing": bool,
       "error": str, "http_status": int}``。默认按 ``default``。
    """

    def __init__(self, base_url: str, plan: dict, name: str = None):
        self.base_url = base_url
        self.name = name or base_url.rsplit(":", 1)[-1]
        self.plan = plan
        self.calls = []          # [(instance, method, url)]
        self.parallel = []       # 交错轨迹：每条是 (instance, 动作)
        self._job_seq = 0

    # -- transport 适配 ----------------------------------------------------
    def transport(self) -> HttpTransport:
        return _StubTransport(self)

    def _behaviour(self, instance: str, body: dict) -> dict:
        key = f"{body.get('symbol')}@{body.get('trade_date')}"
        spec = self.plan.get(key)
        if spec is None:
            spec = self.plan.get("default", {"status": "completed", "model": EXPECTED_MODEL})
        return spec

    def _name_of(self, job_id: str) -> str:
        return job_id.rsplit("-job-", 1)[0].rsplit("-", 1)[-1]

    def handle(self, instance: str, method: str, path: str, body: dict):
        if path == "/v1/analyze":
            spec = self._behaviour(instance, body or {})
            self._job_seq += 1
            job_id = f"{instance}-job-{self._job_seq}"
            self._store[job_id] = spec
            self._store_meta[job_id] = body
            self.parallel.append((instance, f"submit:{body.get('symbol')}"))
            return spec.get("http_status", 200), {"job_id": job_id, "status": "pending"}
        if path.startswith("/v1/jobs/") and path.endswith("/result"):
            job_id = path.split("/")[3]
            spec = self._store.get(job_id, {})
            self.parallel.append((self._name_of(job_id), f"result:{job_id}"))
            # 形状对齐真实 /v1/jobs/{id}/result：result_data 藏在 result 下
            return 200, {
                "job_id": job_id,
                "status": "completed",
                "decision": None,
                "result": {
                    "served_models": {
                        "call_count": 3,
                        "by_role": (
                            {} if spec.get("missing")
                            else {"bull_researcher": [spec.get("model")]}
                        ),
                        "served_model_missing": 3 if spec.get("missing") else 0,
                    },
                    "usage_summary": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 200,
                        "total_tokens": 1200,
                    },
                },
                "finished_at": "2026-10-13T09:00:00+08:00",
            }
        if path.startswith("/v1/jobs/"):
            job_id = path.split("/")[3]
            spec = self._store.get(job_id, {})
            self.parallel.append((self._name_of(job_id), f"poll:{job_id}"))
            status = "completed" if spec.get("status") == "completed" else "failed"
            return 200, {
                "job_id": job_id,
                "status": status,
                "error": spec.get("error"),
                "symbol": (self._store_meta.get(job_id) or {}).get("symbol"),
                "trade_date": (self._store_meta.get(job_id) or {}).get("trade_date"),
            }
        raise AssertionError(f"unexpected path {path}")

    _store = {}
    _store_meta = {}


class _StubTransport(HttpTransport):
    def __init__(self, server: StubServer):
        super().__init__(session=None)
        self.server = server
        self.name = server.name

    def request(self, method, url, *, headers=None, json_body=None):
        from urllib.parse import urlparse

        path = urlparse(url).path
        self.server.calls.append((self.name, method, path))
        return self.server.handle(self.name, method, path, json_body or {})


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _manifest(pairs_spec, *, frozen=FROZEN, batches=None) -> Manifest:
    return Manifest.from_dict({
        "name": "stub",
        "frozen_models": list(frozen),
        "pairs": pairs_spec,
        "batches": batches or [],
    })


def _executor(tmp_path, servers: dict, *, now=None, **cfg):
    ledger = Ledger(tmp_path / "state", now=now or (lambda: datetime(2026, 10, 13, 9, 0, tzinfo=CN_TZ)))
    cfg.setdefault("pair_retries", 1)
    return ExperimentExecutor(
        instances={name: srv.base_url for name, srv in servers.items()},
        ledger=ledger,
        transport_factory=lambda name, _url: servers[name].transport(),
        config=StopConfig(**cfg),
        now=now or (lambda: datetime(2026, 10, 13, 9, 0, tzinfo=CN_TZ)),
        sleep=lambda _s: None,
        poll_interval=0,
        job_timeout=5,
    )


def _pair(pair_id, *, instances=("O", "N"), model=EXPECTED_MODEL, symbol="600519.SH",
          date="2026-10-09"):
    return {
        "pair_id": pair_id,
        "symbol": symbol,
        "trade_date": date,
        "instances": list(instances),
        "expected_model": model,
    }


def _two_servers(plan=None):
    StubServer._store = {}
    StubServer._store_meta = {}
    plan = plan or {}
    return {
        "O": StubServer("http://stub:8021", plan.get("O", {}), name="O"),
        "N": StubServer("http://stub:8022", plan.get("N", {}), name="N"),
    }


# ---------------------------------------------------------------------------
# 1. 作废路径
# ---------------------------------------------------------------------------


def test_pair_ok_records_per_attempt_accounting(tmp_path):
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    report = ex.run(_manifest([_pair("p1")]))

    assert report.pairs_ok == 1
    assert report.pairs_voided == 0
    assert ex.ledger.totals["analysis_requests"] == 2      # O + N 各一次
    assert ex.ledger.totals["total_tokens"] == 2400       # 1200 × 2
    lines = (tmp_path / "state" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["outcome"] == "ok"
    assert [r["instance"] for r in rec["runs"]] == ["O", "N"]  # 连续、按序
    assert rec["elapsed_seconds"] >= 0


def test_pair_is_atomic_all_instances_run_before_verdict(tmp_path):
    """O2 预抽时三份必须连着跑完再统一核对，不能中途判。"""
    StubServer._store = {}
    StubServer._store_meta = {}
    servers = {
        "O": StubServer("http://stub:8021", {}, name="O"),
        "N": StubServer("http://stub:8022", {}, name="N"),
        "O2": StubServer("http://stub:8023", {}, name="O2"),
    }
    ex = _executor(tmp_path, servers)
    report = ex.run(_manifest([_pair("p1", instances=("O", "N", "O2"))]))

    assert report.pairs_ok == 1
    # 三台都在同一对里跑过
    for name in servers:
        assert any(c[2] == "/v1/analyze" for c in servers[name].calls)
    # 顺序：三个 submit 交错在同一个 trace 上，没有第二个对的提交插进来
    trace = [f"{name}:{a}" for srv in servers.values() for name, a in srv.parallel]
    submits = [t for t in trace if ":submit:" in t]
    assert len(submits) == 3
    assert len({s.split(":")[0] for s in submits}) == 3


def test_intra_pair_model_mismatch_voids_pair_and_reruns_once(tmp_path):
    """对内不一致（N 报了别的模型）→ 整对作废 + 补跑 1 次。

    补跑用的是同一份桩计划（N 仍报别的模型），所以两次都过不了核对，
    最终终态是 voided 且用满 1 次补跑机会。
    """
    plan = {
        "O": {"default": {"status": "completed", "model": EXPECTED_MODEL}},
        "N": {"600519.SH@2026-10-09": {"status": "completed", "model": "gpt-4o-mini"}},
    }
    servers = _two_servers(plan)
    ex = _executor(tmp_path, servers)
    report = ex.run(_manifest([_pair("p1"), _pair("p2", symbol="600520.SH")]))

    assert report.pairs_ok == 0
    assert report.pairs_voided == 2          # 首跑 + 补跑各记一次
    assert report.pairs_rerun == 1
    state = ex.ledger.pair_state("p1")
    assert state.status == "voided"
    assert state.attempts == 2               # 首跑 + 补跑 1 次，不再无限重试
    assert "model_mismatch_across_pair" in state.reason
    assert state.reason == "model_mismatch_across_pair:['deepseek-v3.2-exp-frozen', 'gpt-4o-mini']"
    # 停批：非冻结模型 → 停在当前这一对跑完之后，p2 未被发起
    assert report.stop_reason == "non_frozen_model"
    assert ex.ledger.pair_state("p2").status == "pending"
    assert ex.ledger.total_analysis_requests() == 4   # p1 两对次 × 2 实例


def test_rerun_can_recover_a_voided_pair(tmp_path):
    """桩在补跑时修好响应体的 model → 补跑通过，该对终态 ok。"""
    StubServer._store = {}
    StubServer._store_meta = {}
    calls = {"n": 0}

    class FlakyN(StubServer):
        def _behaviour(self, instance, body):
            calls["n"] += 1
            if calls["n"] <= 1:          # 第一次报错模型
                return {"status": "completed", "model": "rogue-model"}
            return {"status": "completed", "model": EXPECTED_MODEL}

    servers = {
        "O": StubServer("http://stub:8021", {}, name="O"),
        "N": FlakyN("http://stub:8022", {}, name="N"),
    }
    ex = _executor(tmp_path, servers)
    report = ex.run(_manifest([_pair("p1")]))
    assert report.pairs_rerun == 1
    assert ex.ledger.pair_state("p1").status == "ok"
    assert report.pairs_ok == 1
    assert report.pairs_voided == 1


def test_model_not_frozen_voids_pair(tmp_path):
    """两份都自洽，但报的模型不是冻结身份 → 整对作废。"""
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    pair = _pair("p1", model="some-other-frozen-id")
    report = ex.run(_manifest([pair]))
    assert report.pairs_ok == 0
    assert report.pairs_voided == 2          # 首跑 + 补跑各记一次
    state = ex.ledger.pair_state("p1")
    assert state.status == "voided"
    assert "model_not_frozen" in state.reason
    # 用满 1 次补跑后定终态，续跑也不会再重试
    assert state.attempts == 2
    assert report.pairs_rerun == 1


def test_missing_model_field_fails_pair(tmp_path):
    """响应体没带 model → 无法核对，绝不默认放行。"""
    servers = _two_servers({"O": {"default": {"status": "completed", "missing": True}}})
    ex = _executor(tmp_path, servers)
    report = ex.run(_manifest([_pair("p1")]))
    state = ex.ledger.pair_state("p1")
    # 请求本身成功、只是核对不通过 → 作废（voided），并给补跑 1 次机会
    assert state.status == "voided"
    assert "model_missing" in state.reason
    assert report.pairs_voided == 2
    assert report.pairs_rerun == 1
    assert ex.ledger.totals["analysis_requests"] == 4


def test_verify_pair_unit():
    pair = Manifest.from_dict({"pairs": [_pair("p")]}).pair_by_id("p")
    ok, reason, models, bucket = verify_pair(pair, {
        "O": {"error_bucket": "none", "models": [EXPECTED_MODEL], "model_missing": 0},
        "N": {"error_bucket": "none", "models": [EXPECTED_MODEL], "model_missing": 0},
    })
    assert ok == "ok" and reason is None and models == [EXPECTED_MODEL]
    assert bucket == "none"

    ok, reason, _, bucket = verify_pair(pair, {
        "O": {"error_bucket": "none", "models": ["x"], "model_missing": 0},
        "N": {"error_bucket": "none", "models": ["y"], "model_missing": 0},
    })
    assert ok == "voided" and reason.startswith("model_mismatch_across_pair")
    assert bucket == "none"

    # 配额错误：结构化 bucket 供停批判定，不依赖 reason 前缀
    ok, reason, _, bucket = verify_pair(pair, {
        "O": {"error_bucket": "quota_or_rate_limit", "models": [], "model_missing": 0},
        "N": {"error_bucket": "none", "models": [EXPECTED_MODEL], "model_missing": 0},
    })
    assert ok == "failed"
    assert reason.startswith("quota_or_rate_limit")
    assert bucket == "quota_or_rate_limit"

    ok, _, _, bucket = verify_pair(pair, {
        "O": {"error_bucket": "server_error", "models": [], "model_missing": 0},
        "N": {"error_bucket": "none", "models": [EXPECTED_MODEL], "model_missing": 0},
    })
    assert ok == "failed" and bucket == "server_error"


def test_flatten_served_models_handles_missing_shape():
    assert flatten_served_models(None) == ([], 1)
    assert flatten_served_models({}) == ([], 1)
    # 采到了模型就算部分调用缺失也仍然可核对（缺失数照实记账）
    models, missing = flatten_served_models({
        "served_models": {"by_role": {"a": ["m1"]}, "served_model_missing": 2}
    })
    assert models == ["m1"] and missing == 2
    # 一个都没采到 → 无法核对
    models, missing = flatten_served_models({
        "served_models": {"by_role": {}, "served_model_missing": 7}
    })
    assert models == [] and missing == 7


# ---------------------------------------------------------------------------
# 2. 续跑路径
# ---------------------------------------------------------------------------


def test_resume_does_not_rerun_completed_pairs(tmp_path):
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    ex.run(_manifest([_pair("p1"), _pair("p2")]))
    assert ex.ledger.pair_state("p1").status == "ok"
    n_before = len(servers["O"].calls)

    # 模拟中断：新增一对后同目录续跑
    ex2 = _executor(tmp_path, servers)
    report = ex2.run(_manifest([_pair("p1"), _pair("p2"), _pair("p3")]))

    assert report.pairs_skipped_done == 2
    assert report.pairs_run == 1
    assert report.pairs_ok == 1
    # O 实例只为新的一对发起过提交
    submits = [c for c in servers["O"].calls if c[2] == "/v1/analyze"]
    # 首轮 p1+p2 两次 + 续跑只为 p3 一次
    assert len(submits) == 3


def test_resume_after_stop_continues_where_left(tmp_path):
    plan = {"O": {"default": {"status": "completed", "model": EXPECTED_MODEL}}}
    servers = _two_servers(plan)
    ex = _executor(tmp_path, servers, batch_request_cap=2, pair_retries=0)
    # 每对 2 次请求，批预算 2 → 第 1 对跑完即停批
    report = ex.run(_manifest([_pair("p1"), _pair("p2"), _pair("p3")]))
    assert report.stop_reason == "batch_or_total_budget"
    assert report.pairs_ok == 1
    assert ex.ledger.pair_state("p2").status == "pending"

    servers2 = _two_servers(plan)
    ex2 = _executor(tmp_path, servers2)
    ex2.ledger.stop_reason = None   # 人工确认后清除停批，才允许继续
    report2 = ex2.run(_manifest([_pair("p1"), _pair("p2"), _pair("p3")]))
    assert report2.pairs_skipped_done == 1      # p1 不重跑
    assert report2.pairs_ok == 2               # p2 / p3 补齐
    assert report2.stop_reason == "all_pairs_done" or report2.stop_reason is None


def test_stop_reason_is_sticky_across_resume(tmp_path):
    servers = _two_servers()
    ex = _executor(tmp_path, servers, total_request_cap=2, pair_retries=0)
    ex.run(_manifest([_pair("p1"), _pair("p2")]))
    assert ex.ledger.stop_reason == "batch_or_total_budget"
    servers2 = _two_servers()
    ex2 = _executor(tmp_path, servers2)
    report = ex2.run(_manifest([_pair("p1"), _pair("p2")]))
    assert report.pairs_run == 0               # 已停批就直接退出，等人工确认


def test_voided_pair_is_not_rerun_on_resume(tmp_path):
    """作废的对是终态：续跑不再消耗请求去重跑它。"""
    servers = _two_servers({"O": {"default": {"status": "completed", "missing": True}}})
    ex = _executor(tmp_path, servers)
    ex.run(_manifest([_pair("p1")]))
    assert ex.ledger.pair_state("p1").status == "voided"
    requests_before = ex.ledger.total_analysis_requests()
    assert requests_before == 4                 # 首跑 + 补跑，各 2 个实例

    servers2 = _two_servers({"O": {"default": {"status": "completed", "missing": True}}})
    ex2 = _executor(tmp_path, servers2)
    ex2.ledger.stop_reason = None              # 人工确认后清停批
    ex2.ledger.stop_detail = None
    report = ex2.run(_manifest([_pair("p1")]))
    assert report.pairs_run == 0                # 作废的终态对不重跑
    assert report.pairs_skipped_done == 1
    assert servers2["O"].calls == []
    assert ex2.ledger.total_analysis_requests() == requests_before


# ---------------------------------------------------------------------------
# 3. 停批路径
# ---------------------------------------------------------------------------


def test_stop_on_quota_error(tmp_path):
    plan = {"N": {"default": {"status": "failed", "error": "429 rate_limit exceeded"}}}
    servers = _two_servers(plan)
    ex = _executor(tmp_path, servers, pair_retries=0)
    report = ex.run(_manifest([_pair("p1"), _pair("p2")]))
    assert report.stop_reason == "quota_or_rate_limit"
    assert report.pairs_run == 1                # 停在当前这一对跑完之后
    assert report.pairs_failed == 1
    assert ex.ledger.pair_state("p2").status == "pending"


def test_classify_error_recognises_quota_variants():
    assert classify_error("429 Too Many Requests", 429) == "quota_or_rate_limit"
    assert classify_error("insufficient_quota") == "quota_or_rate_limit"
    assert classify_error("余额不足") == "quota_or_rate_limit"
    assert classify_error("upstream boom", 502) == "server_error"
    assert classify_error("bad symbol", 400) == "client_error"
    assert classify_error(None) == "none"


def test_stop_on_non_frozen_model(tmp_path):
    servers = _two_servers({"N": {"default": {"status": "completed", "model": "rogue-model"}}})
    ex = _executor(tmp_path, servers, pair_retries=0)
    report = ex.run(_manifest([_pair("p1"), _pair("p2")]))
    assert report.stop_reason == "non_frozen_model"
    assert report.pairs_run == 1
    assert ex.ledger.pair_state("p2").status == "pending"


def test_stop_on_fail_rate_above_five_percent(tmp_path):
    """失败率按「对」计：第 10 对跑完时 1/10=10% > 5% → 停。

    失败对仍完整跑完（O 与 N 都提交），停在它之后，不再发起第 11 对。
    """
    pairs = [_pair(f"p{i}", symbol=f"6005{i:02d}.SH") for i in range(21)]
    plan = {"N": {"default": {"status": "completed", "model": EXPECTED_MODEL}}}
    plan["N"]["600509.SH@2026-10-09"] = {"status": "failed", "error": "boom"}
    servers = _two_servers(plan)
    ex = _executor(tmp_path, servers, pair_retries=0)
    report = ex.run(_manifest(pairs))
    assert report.stop_reason == "batch_fail_rate"
    assert "0.1000" in report.stop_detail       # 1/10
    assert report.pairs_run == 10                # 第 11 对没被发起
    assert report.pairs_failed == 1
    assert report.batches[0]["fail_rate"] == pytest.approx(0.1)
    # 失败那一对的两侧都提交过（没在半路掐断）
    assert len([c for c in servers["O"].calls if c[2] == "/v1/analyze"]) == 10
    assert len([c for c in servers["N"].calls if c[2] == "/v1/analyze"]) == 10


def test_fail_rate_exactly_five_percent_does_not_stop(tmp_path):
    """1/20 = 5.0% 恰好不过阈值（严格大于才停批）→ 跑完全部 20 对。"""
    pairs = [_pair(f"p{i}", symbol=f"6007{i:02d}.SH") for i in range(20)]
    plan = {"N": {"default": {"status": "completed", "model": EXPECTED_MODEL}}}
    plan["N"]["600719.SH@2026-10-09"] = {"status": "failed", "error": "boom"}
    servers = _two_servers(plan)
    ex = _executor(tmp_path, servers, pair_retries=0)
    report = ex.run(_manifest(pairs))
    assert report.stop_reason is None
    assert report.pairs_run == 20
    assert report.pairs_failed == 1
    assert report.batches[0]["fail_rate"] == pytest.approx(0.05)


def test_stop_on_total_request_cap(tmp_path):
    pairs = [_pair(f"p{i}", symbol=f"6009{i:02d}.SH") for i in range(5)]
    servers = _two_servers()
    ex = _executor(tmp_path, servers, total_request_cap=4, pair_retries=0)
    report = ex.run(_manifest(pairs))
    assert report.stop_reason == "batch_or_total_budget"
    assert report.pairs_ok == 2
    assert ex.ledger.total_analysis_requests() == 4


def test_default_total_cap_is_20328():
    assert StopConfig().total_request_cap == 20_328


def test_default_fail_rate_threshold_is_five_percent():
    assert StopConfig().fail_rate_threshold == pytest.approx(0.05)


def test_default_pair_retries_is_one():
    assert StopConfig().pair_retries == 1


# ---------------------------------------------------------------------------
# 4. 交易日 16:00–19:50 自动不跑
# ---------------------------------------------------------------------------


def test_blackout_window_covers_1600_to_1950():
    at = lambda h, m: datetime(2026, 10, 13, h, m, tzinfo=CN_TZ)  # 周二
    assert blackout_active(at(16, 0)) is True
    assert blackout_active(at(18, 30)) is True
    assert blackout_active(at(19, 49)) is True
    assert blackout_active(at(15, 59)) is False
    assert blackout_active(at(19, 50)) is False
    assert blackout_active(at(20, 10)) is False


def test_blackout_wait_policy_waits_and_then_runs(tmp_path):
    slept = []
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    clock = {"now": datetime(2026, 10, 13, 16, 30, tzinfo=CN_TZ)}
    ex.now = lambda: clock["now"]
    ex.sleep = lambda s: (slept.append(s), clock.__setitem__("now", clock["now"] + timedelta(seconds=s)))
    report = ex.run(_manifest([_pair("p1")]))
    assert slept and 11900 <= slept[0] <= 12060   # 16:30 → 19:50 = 12000s
    assert report.pairs_ok == 1
    assert report.stop_reason is None


def test_blackout_stop_policy_stops_before_submitting(tmp_path):
    servers = _two_servers()
    ex = _executor(tmp_path, servers, blackout_policy="stop")
    ex.now = lambda: datetime(2026, 10, 13, 17, 0, tzinfo=CN_TZ)
    report = ex.run(_manifest([_pair("p1")]))
    assert report.stop_reason == "trading_day_blackout"
    assert report.pairs_run == 0
    assert servers["O"].calls == []


def test_weekend_is_not_blackout():
    assert blackout_active(datetime(2026, 10, 10, 17, 0, tzinfo=CN_TZ)) is False


# ---------------------------------------------------------------------------
# 5. 记账字段完整性
# ---------------------------------------------------------------------------


def test_ledger_row_carries_requests_tokens_elapsed_models_error(tmp_path):
    plan = {"N": {"default": {"status": "failed", "error": "kaboom"}}}
    servers = _two_servers(plan)
    ex = _executor(tmp_path, servers, pair_retries=0)
    ex.run(_manifest([_pair("p1")]))
    rec = json.loads((tmp_path / "state" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["analysis_requests"] == 2
    assert rec["http_requests"] >= 2
    # N 这一份失败了，拿不到 result → 只记到 O 的用量；失败的错误字段照记
    assert rec["prompt_tokens"] == 1000 and rec["completion_tokens"] == 200
    assert rec["total_tokens"] == 1200
    assert rec["served_models"] == [EXPECTED_MODEL]
    assert rec["error"] and "kaboom" in rec["error"]
    assert rec["elapsed_seconds"] >= 0
    assert rec["started_at"] and rec["finished_at"]
    assert rec["batch_index"] == 0


def test_state_file_records_pair_terminal_states(tmp_path):
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    ex.run(_manifest([_pair("p1"), _pair("p2")]))
    payload = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert {p["pair_id"]: p["status"] for p in payload["pairs"]} == {"p1": "ok", "p2": "ok"}
    assert payload["totals"]["analysis_requests"] == 4


def test_manifest_batches_follow_frozen_order():
    man = Manifest.from_dict({
        "pairs": [_pair("a"), _pair("b")],
        "batches": [["a", "b"]],
    })
    assert [[p.pair_id for p in b] for b in man.ordered_batches()] == [["a", "b"]]
    assert man.unlisted_pairs() == []


def test_manifest_rejects_pairs_missing_from_batch_order(tmp_path):
    """🟡#4：批序未覆盖的对**不并进既有批**（那会改写该批失败率分母与预算），
    直接报错逼人改正清单。"""
    man = Manifest.from_dict({
        "pairs": [_pair("a"), _pair("b"), _pair("c")],
        "batches": [["a", "c"]],
    })
    assert [p.pair_id for p in man.unlisted_pairs()] == ["b"]
    with pytest.raises(ValueError, match="批序未覆盖"):
        man.ordered_batches()


def test_manifest_rejects_unknown_pair_in_batch_order():
    man = Manifest.from_dict({"pairs": [_pair("a")], "batches": [["a", "ghost"]]})
    with pytest.raises(KeyError):
        man.ordered_batches()


def test_config_overrides_are_identical_across_instances(tmp_path):
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    pair = dict(_pair("p1"), config_overrides={"v2_debate_enabled": True})
    ex.run(_manifest([pair]))
    bodies = [c for c in servers["O"].calls if c[2] == "/v1/analyze"]
    assert bodies
    assert ex.ledger.pair_state("p1").status == "ok"


# ---------------------------------------------------------------------------
# 6. 实例不可达：记账并继续，不拖垮整批
# ---------------------------------------------------------------------------


def test_unreachable_instance_fails_pair_without_crashing(tmp_path):
    class DeadTransport(HttpTransport):
        def request(self, method, url, *, headers=None, json_body=None):
            raise OSError("Connection refused")

    ledger = Ledger(tmp_path / "state")
    ex = ExperimentExecutor(
        instances={"O": "http://dead:1", "N": "http://dead:2"},
        ledger=ledger,
        transport_factory=lambda name, url: DeadTransport(),
        config=StopConfig(pair_retries=0),
        now=lambda: datetime(2026, 10, 13, 9, 0, tzinfo=CN_TZ),
        sleep=lambda _s: None,
        poll_interval=0,
    )
    report = ex.run(_manifest([_pair("p1"), _pair("p2")]))

    # 第 1 对 100% 失败即停批，停在它跑完之后；p2 未被发起
    assert report.pairs_failed == 1
    assert report.pairs_run == 1
    assert report.stop_reason == "batch_fail_rate"
    assert ledger.pair_state("p2").status == "pending"
    assert ledger.total_analysis_requests() == 2
    rec = json.loads((tmp_path / "state" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "Connection refused" in rec["error"]
    assert rec["outcome"] == "failed"


def test_cli_status_prints_ledger(tmp_path, capsys):
    from scripts.phase2.experiment_executor import main

    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    ex.run(_manifest([_pair("p1")]))
    rc = main(["status", "--state-dir", str(tmp_path / "state")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pairs"][0]["pair_id"] == "p1"
    assert payload["totals"]["analysis_requests"] == 2


# ---------------------------------------------------------------------------
# 7. 🔴 DAV-1889#1 回归：轮询收到非 200 必须立刻按错误处理，不得死等到超时
# ---------------------------------------------------------------------------


class _PollStatusTransport(HttpTransport):
    """提交成功，轮询固定返回给定 HTTP 状态。"""

    def __init__(self, status: int, body=None):
        super().__init__(session=None)
        self.status = status
        self.body = body if body is not None else {"detail": "boom"}
        self.polls = 0

    def request(self, method, url, *, headers=None, json_body=None):
        from urllib.parse import urlparse

        path = urlparse(url).path
        if path == "/v1/analyze":
            return 200, {"job_id": "j1", "status": "pending"}
        if path.endswith("/result"):
            return 200, {"result": {}}
        self.polls += 1
        return self.status, self.body


def _executor_with_transport(tmp_path, transports, **cfg):
    ledger = Ledger(tmp_path / "state")
    cfg.setdefault("pair_retries", 0)
    ex = ExperimentExecutor(
        instances={name: f"http://stub:{i}" for i, name in enumerate(transports)},
        ledger=ledger,
        transport_factory=lambda name, url: transports[name],
        config=StopConfig(**cfg),
        now=lambda: datetime(2026, 10, 13, 9, 0, tzinfo=CN_TZ),
        sleep=lambda _s: None,
        poll_interval=0,
        job_timeout=7200.0,   # 若死等分支被触发，测试会被拖满 2 小时
    )
    return ex, ledger


@pytest.mark.parametrize("status,expected_bucket", [
    (404, "client_error"),
    (401, "client_error"),
    (500, "server_error"),
    (503, "server_error"),
])
def test_non_200_poll_fails_fast_instead_of_waiting_for_timeout(tmp_path, status, expected_bucket):
    """🔴#1 回归：轮询 4xx/5xx → 立即 status_failed，error_bucket 正确，
    不进入挂到 job_timeout(7200s) 的死等分支。"""
    ex, ledger = _executor_with_transport(tmp_path, {"O": _PollStatusTransport(status)})
    pair = Manifest.from_dict({"pairs": [_pair("p1", instances=("O",))]}).pair_by_id("p1")
    record = ex.run_pair(pair, 0)

    assert record.outcome == "failed"
    run = record.runs[0]
    assert run["status"] == "status_failed"
    assert run["error_bucket"] == expected_bucket
    assert "boom" in run["error"]
    # 只轮询一次就退出，没有反复重试直到超时
    assert ex.transport("O").polls == 1


def test_poll_200_with_non_dict_body_fails_fast(tmp_path):
    """200 但响应体不是 dict（代理层畸形响应）→ 同样立即 status_failed，
    不能拿它去 .get("status") 然后挂到超时。"""
    ex, _ = _executor_with_transport(tmp_path, {"O": _PollStatusTransport(200, body=["oops"])})
    pair = Manifest.from_dict({"pairs": [_pair("p1", instances=("O",))]}).pair_by_id("p1")
    record = ex.run_pair(pair, 0)
    run = record.runs[0]
    assert run["status"] == "status_failed"
    assert run["error_bucket"] == "malformed_response"
    assert record.outcome == "failed"   # 请求本身没成 → failed，不是 voided
    assert ex.transport("O").polls == 1


def test_poll_still_waits_while_job_is_running(tmp_path):
    """守住修复的另一侧：正常 pending/running 依旧轮询到终态。"""
    class _Running(StubServer):
        def handle(self, instance, method, path, body):
            if path == "/v1/analyze":
                self._store["O-job-1"] = {"status": "completed", "model": EXPECTED_MODEL}
                self._store_meta["O-job-1"] = {"symbol": "600519.SH"}
                return 200, {"job_id": "O-job-1", "status": "pending"}
            return super().handle(instance, method, path, body)

    StubServer._store = {}
    StubServer._store_meta = {}
    server = _Running("http://stub:8021", {}, name="O")
    ex, _ = _executor_with_transport(
        tmp_path, {"O": server.transport()}, pair_retries=0
    )
    ex.sleep = lambda _s: None
    pair = Manifest.from_dict({"pairs": [_pair("p1", instances=("O",))]}).pair_by_id("p1")
    record = ex.run_pair(pair, 0)
    assert record.outcome == "ok"
    assert record.runs[0]["status"] == "completed"
    assert record.served_models == [EXPECTED_MODEL]


def test_quota_stop_uses_structured_bucket_not_reason_prefix(tmp_path):
    """🟡#2：配额停批读结构化 error_bucket；reason 文案改了也不漏判。"""
    StubServer._store = {}
    StubServer._store_meta = {}
    servers = _two_servers({"N": {"default": {"status": "failed", "error": "upstream busy"}}})
    transports = {name: srv.transport() for name, srv in servers.items()}
    ex, _ = _executor_with_transport(tmp_path, transports, pair_retries=0)
    ex.sleep = lambda _s: None
    ex.run(_manifest([_pair("p1")]))
    assert ex.ledger.totals["analysis_requests"] == 2
    rec = json.loads((tmp_path / "state" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
    # "upstream busy" 无状态码 → 归到通用 error；结构化字段照实写进记账
    assert rec["error_bucket"] == "error"
    assert rec["void_reason"].startswith("request_failed")


def test_executor_has_no_class_level_mutable_batch_state():
    """🟡#3：批次状态不得再有类级可变默认（会跨实例串状态）。"""
    assert "_last_batch_records" not in vars(ExperimentExecutor)
    assert "_non_frozen_seen" not in vars(ExperimentExecutor)


# ---------------------------------------------------------------------------
# 8. 总控 10-12 追加：前瞻现取名单（种子 = 20261010 + 批号）
# ---------------------------------------------------------------------------


def _fake_sampler(rows, *, digest="deadbeef"):
    """rows 可以是代码字符串，也可以是原始取样行 dict（原样透传）。"""
    def runner(spec):
        picks = [
            {"ts_code": r, "sw_l1": "银行"} if isinstance(r, str) else r
            for r in rows
        ]
        return {
            "trade_date": f"{spec.date[:4]}-{spec.date[4:6]}-{spec.date[6:]}",
            "n_picked": len(picks),
            "eligible_digest": digest,
            "picks": picks,
            "_spec": spec,
        }
    return runner


def test_batch_seed_is_reproducible_base_plus_index():
    specs = [BatchSpec(batch_index=i, date="20261013", n=5) for i in range(3)]
    assert [s.sampler_seed for s in specs] == [20261010, 20261011, 20261012]
    assert BatchSpec(batch_index=0, date="20261013", n=5).sampler_seed == 20261010


def test_forward_sampling_builds_pairs_with_frozen_identity():
    spec = BatchSpec(batch_index=0, date="20261013", n=2)
    pairs, sampler = fetch_batch_pairs(
        spec, instances=("O", "N"), expected_model=EXPECTED_MODEL,
        runner=_fake_sampler(["600519.SH", "000001.SZ"]),
    )
    assert [p.symbol for p in pairs] == ["600519.SH", "000001.SZ"]
    assert all(p.trade_date == "2026-10-13" for p in pairs)
    assert all(p.expected_model == EXPECTED_MODEL for p in pairs)
    assert all(p.instances == ("O", "N") for p in pairs)
    # pair_id 含批号，跨批不撞名
    assert pairs[0].pair_id == "b00-600519.SH"
    assert sampler["eligible_digest"] == "deadbeef"   # 输入指纹留档可复现


def test_forward_sampling_refuses_empty_batch():
    spec = BatchSpec(batch_index=0, date="20261013", n=2)
    with pytest.raises(ValueError, match="取样为空"):
        fetch_batch_pairs(spec, instances=("O", "N"), expected_model=EXPECTED_MODEL,
                          runner=_fake_sampler([]))


def test_forward_sampling_refuses_row_without_symbol():
    spec = BatchSpec(batch_index=0, date="20261013", n=1)
    with pytest.raises(ValueError, match="缺 ts_code"):
        fetch_batch_pairs(spec, instances=("O", "N"), expected_model=EXPECTED_MODEL,
                          runner=_fake_sampler([{"ts_code": "  ", "sw_l1": "银行"}]))


def test_forward_sampled_pairs_run_through_executor(tmp_path):
    """前瞻名单直接进执行器：同样的核对/记账/停批口径。"""
    spec = BatchSpec(batch_index=0, date="20261013", n=2)
    pairs, _ = fetch_batch_pairs(spec, instances=("O", "N"),
                                 expected_model=EXPECTED_MODEL,
                                 runner=_fake_sampler(["600519.SH", "000001.SZ"]))
    man = Manifest(name="forward", pairs=tuple(pairs), frozen_models=FROZEN)
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    report = ex.run(man)
    assert report.pairs_ok == 2
    assert ex.ledger.total_analysis_requests() == 4


def test_load_batch_specs_and_plan_cli(tmp_path, capsys):
    spec_file = tmp_path / "batches.json"
    spec_file.write_text(json.dumps({"batches": [
        {"batch_index": 0, "date": "20261013", "n": 2},
        {"batch_index": 1, "date": "20261014", "n": 1},
    ]}), encoding="utf-8")
    specs = load_batch_specs(spec_file)
    assert [(s.batch_index, s.date, s.n) for s in specs] == [(0, "20261013", 2), (1, "20261014", 1)]

    import scripts.phase2.experiment_executor as mod
    out = tmp_path / "planned.json"
    real = mod.fetch_batch_pairs
    def _stub_fetch(spec, *, instances, expected_model, **kw):
        pairs, _ = fetch_batch_pairs(spec, instances=instances,
                                     expected_model=expected_model,
                                     runner=_fake_sampler(["600519.SH"]))
        return pairs, {"eligible_digest": "d1", "n_picked": 1, "trade_date": "2026-10-13"}

    mod.fetch_batch_pairs = _stub_fetch
    try:
        rc = mod.main(["plan", "--batch-spec", str(spec_file), "--instance", "O=http://x",
                       "--instance", "N=http://y", "--expected-model", EXPECTED_MODEL,
                       "--out", str(out)])
    finally:
        mod.fetch_batch_pairs = real
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [b["sampler_seed"] for b in payload["batches"]] == [20261010, 20261011]
    assert payload["batches"][0]["pairs"][0]["expected_model"] == EXPECTED_MODEL


# ---------------------------------------------------------------------------
# 9. 🟡1（DAV-1941）：seed_base 必须真实贯通，不得是静默无效的假开关
# ---------------------------------------------------------------------------


def test_seed_base_actually_changes_the_sampler_seed():
    """硬口径只是默认值：换基数必须真的改变抽样种子。"""
    default = BatchSpec(batch_index=0, date="20261013", n=5)
    custom = BatchSpec(batch_index=0, date="20261013", n=5, seed_base=777)
    assert default.sampler_seed == 20261010
    assert custom.sampler_seed == 777          # 传入的 777 必须生效
    assert custom.sampler_seed != default.sampler_seed


def test_seed_base_is_threaded_into_actual_sampler_invocation(tmp_path):
    """不只看属性：桩 runner 收到的 spec 必须带着非默认 seed_base。"""
    seen = []

    def runner(spec):
        seen.append(spec)
        return {
            "trade_date": "2026-10-13", "n_picked": 1,
            "eligible_digest": "d", "picks": [{"ts_code": "600519.SH"}],
        }

    spec = BatchSpec(batch_index=3, date="20261013", n=1, seed_base=4242)
    fetch_batch_pairs(spec, instances=("O", "N"), expected_model=EXPECTED_MODEL,
                      runner=runner)
    assert seen[0].sampler_seed == 4242 + 3


def test_load_batch_specs_honours_seed_base_argument(tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"batches": [{"batch_index": 2, "date": "20261013", "n": 5}]}),
                 encoding="utf-8")
    specs = load_batch_specs(f, seed_base=999)
    assert specs[0].seed_base == 999
    assert specs[0].sampler_seed == 999 + 2
    # 默认仍是硬口径
    assert load_batch_specs(f)[0].sampler_seed == 20261010 + 2


def test_load_batch_specs_rejects_conflicting_seed_base(tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"batches": [
        {"batch_index": 0, "date": "20261013", "n": 5, "seed_base": 111}
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="冲突"):
        load_batch_specs(f, seed_base=222)


def test_batch_spec_has_no_dead_seed_field():
    """旧的 seed 字段必须消失，杜绝「加载即弃」的静默失效入口。"""
    assert not hasattr(BatchSpec(0, "20261013", 1), "seed")


def test_plan_cli_sampler_seed_base_switch_is_wired(tmp_path, capsys):
    """CLI 开关必须真正影响落盘的 sampler_seed（DAV-1941 实测该处引用数为 0）。"""
    import scripts.phase2.experiment_executor as mod
    spec_file = tmp_path / "b.json"
    spec_file.write_text(json.dumps({"batches": [
        {"batch_index": 0, "date": "20261013", "n": 1},
        {"batch_index": 1, "date": "20261014", "n": 1},
    ]}), encoding="utf-8")
    out = tmp_path / "planned.json"

    def _stub_fetch(spec, *, instances, expected_model, **kw):
        pairs, _ = fetch_batch_pairs(spec, instances=instances,
                                     expected_model=expected_model,
                                     runner=_fake_sampler(["600519.SH"]))
        return pairs, {"eligible_digest": "d", "n_picked": 1, "trade_date": "2026-10-13"}

    real = mod.fetch_batch_pairs
    mod.fetch_batch_pairs = _stub_fetch
    try:
        rc = mod.main(["plan", "--batch-spec", str(spec_file), "--instance", "O=http://x",
                       "--expected-model", EXPECTED_MODEL,
                       "--sampler-seed-base", "777", "--out", str(out)])
    finally:
        mod.fetch_batch_pairs = real
    assert rc == 0
    seeds = [b["sampler_seed"] for b in json.loads(out.read_text(encoding="utf-8"))["batches"]]
    assert seeds == [777, 778]          # 777 + 批号，CLI 开关真实生效


def test_run_batch_spec_requires_expected_model(tmp_path):
    """🟢 落实：缺 --expected-model 会让所有对冻结身份为 None，静默丧失核对。"""
    import scripts.phase2.experiment_executor as mod
    spec_file = tmp_path / "b.json"
    spec_file.write_text(json.dumps({"batches": [{"batch_index": 0, "date": "20261013", "n": 1}]}),
                         encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.main(["run", "--batch-spec", str(spec_file), "--instance", "O=http://x",
                  "--state-dir", str(tmp_path / "st")])


# ---------------------------------------------------------------------------
# 10. 总控 10-12 晚裁定：批与基准日分离（每日一批、每批一个基准日、每基准日≤5 对）
# ---------------------------------------------------------------------------


def test_daily_gate_opens_only_after_1950_on_trading_day():
    at = lambda h, m: datetime(2026, 10, 13, h, m, tzinfo=CN_TZ)   # 周二
    assert daily_batch_gate_open(at(19, 49)) is False
    assert daily_batch_gate_open(at(19, 50)) is True
    assert daily_batch_gate_open(at(23, 30)) is True
    assert daily_batch_gate_open(at(9, 0)) is False


def test_daily_gate_closed_on_weekend():
    assert daily_batch_gate_open(datetime(2026, 10, 10, 20, 0, tzinfo=CN_TZ)) is False


def test_batch_spec_exposes_single_base_day():
    spec = BatchSpec(batch_index=0, date="20261013", n=5)
    assert spec.base_day == "2026-10-13"


def test_sampling_refuses_more_than_five_pairs(tmp_path):
    """每基准日最多 5 对：超了显式报错，不靠事后截断。"""
    spec = BatchSpec(batch_index=0, date="20261013", n=20)
    with pytest.raises(ValueError, match="超过每基准日上限"):
        fetch_batch_pairs(spec, instances=("O", "N"), expected_model=EXPECTED_MODEL,
                          runner=_fake_sampler([f"6005{i:02d}.SH" for i in range(6)]))


def test_sampling_allows_exactly_five_pairs():
    spec = BatchSpec(batch_index=0, date="20261013", n=5)
    pairs, _ = fetch_batch_pairs(spec, instances=("O", "N"), expected_model=EXPECTED_MODEL,
                                 runner=_fake_sampler([f"6005{i:02d}.SH" for i in range(5)]))
    assert len(pairs) == 5


def test_batch_may_not_span_two_base_days(tmp_path):
    """一批只对应当天一个基准日 T。"""
    man = Manifest.from_dict({
        "pairs": [_pair("a", date="2026-10-13"), _pair("b", date="2026-10-14")],
        "batches": [["a", "b"]],
    })
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    ex.now = lambda: datetime(2026, 10, 13, 20, 0, tzinfo=CN_TZ)
    with pytest.raises(ValueError, match="一个基准日"):
        ex.run(man)
    assert servers["O"].calls == []      # 发现问题前一个请求都没发


def test_at_most_one_batch_per_base_day(tmp_path):
    """同一基准日的第二批直接停（每日一批）。"""
    man = Manifest.from_dict({
        "pairs": [_pair("a", date="2026-10-13"), _pair("b", date="2026-10-13"),
                  _pair("c", date="2026-10-14")],
        "batches": [["a"], ["b"], ["c"]],
    })
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    ex.now = lambda: datetime(2026, 10, 13, 20, 0, tzinfo=CN_TZ)
    report = ex.run(man)
    assert report.stop_reason == "duplicate_base_day"
    assert ex.ledger.pair_state("a").status == "ok"
    assert ex.ledger.pair_state("b").status == "pending"


def test_run_before_1950_waits_for_gate(tmp_path):
    """19:50 前不跑：wait 策略睡到开闸。"""
    slept = []
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    clock = {"now": datetime(2026, 10, 13, 19, 40, tzinfo=CN_TZ)}
    ex.now = lambda: clock["now"]

    def _sleep(s):
        slept.append(s)
        clock["now"] = clock["now"] + timedelta(seconds=s)

    ex.sleep = _sleep
    report = ex.run(_manifest([_pair("p1", date="2026-10-13")]))
    assert slept and 590 <= slept[0] <= 700      # 19:40 → 19:50 = 600s
    assert report.pairs_ok == 1


def test_run_before_1950_stop_policy_stops(tmp_path):
    servers = _two_servers()
    ex = _executor(tmp_path, servers, daily_gate_policy="stop")
    ex.now = lambda: datetime(2026, 10, 13, 19, 0, tzinfo=CN_TZ)
    report = ex.run(_manifest([_pair("p1", date="2026-10-13")]))
    assert report.stop_reason == "daily_batch_gate_not_open"
    assert report.pairs_run == 0
    assert servers["O"].calls == []


def test_weekend_run_is_skipped(tmp_path):
    """周末不跑。"""
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    ex.now = lambda: datetime(2026, 10, 10, 20, 0, tzinfo=CN_TZ)   # 周六
    report = ex.run(_manifest([_pair("p1", date="2026-10-09")]))
    assert report.stop_reason == "non_trading_day"
    assert report.pairs_run == 0
    assert servers["O"].calls == []


def test_run_record_carries_snapshot_whitelist_sha256(tmp_path):
    """总控 §3-3：运行记录必须留下所用白名单的 sha256，否则事后无法复核口径。"""
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"paths": ["collected_at"]}), encoding="utf-8")
    servers = _two_servers()
    ex = _executor(tmp_path, servers)
    ex.now = lambda: datetime(2026, 10, 13, 20, 0, tzinfo=CN_TZ)
    report = ex.run(_manifest([_pair("p1", date="2026-10-13")]))
    assert report.snapshot_whitelist_sha256 is None      # 未提供 → None

    from scripts.phase2.compare_input_snapshots import load_whitelist
    _, sha = load_whitelist(str(wl))
    ex.ledger.snapshot_whitelist_sha256 = sha
    report2 = ex.run(_manifest([_pair("p2", symbol="600520.SH", date="2026-10-14")]))
    assert report2.snapshot_whitelist_sha256 == sha
    state = json.loads((tmp_path / "state" / "state.json").read_text(encoding="utf-8"))
    assert state["snapshot_whitelist_sha256"] == sha


def test_same_seed_same_list_different_seed_different_list():
    """总控 10-12 晚：换种子 → 名单变；同种子 → 名单同（端到端到取样结果）。

    桩 runner 按 ``spec.sampler_seed`` 做确定性置换，行为对齐 m2_sampler
    「同种子同名单、异种子换名单」的可复现契约。
    """
    pool = [f"6005{i:02d}.SH" for i in range(10)]

    def seeded_runner(spec):
        import random
        rng = random.Random(spec.sampler_seed)
        picked = rng.sample(pool, 5)
        return {
            "trade_date": "2026-10-13", "n_picked": 5,
            "eligible_digest": f"d{spec.sampler_seed}",
            "picks": [{"ts_code": s} for s in picked],
        }

    def names(spec):
        pairs, _ = fetch_batch_pairs(spec, instances=("O", "N"),
                                     expected_model=EXPECTED_MODEL, runner=seeded_runner)
        return [p.symbol for p in pairs]

    base = BatchSpec(batch_index=0, date="20261013", n=5)
    again = BatchSpec(batch_index=0, date="20261013", n=5)
    other = BatchSpec(batch_index=0, date="20261013", n=5, seed_base=777)

    assert names(base) == names(again)          # 同种子 → 同名单
    assert names(base) != names(other)          # 换种子 → 名单变
    assert names(other) == names(
        BatchSpec(batch_index=0, date="20261013", n=5, seed_base=777)
    )
