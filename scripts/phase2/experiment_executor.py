#!/usr/bin/env python3
"""DAV-1882 — 实验执行器：配对原子执行 + 逐份 model 核对 + 逐次记账 + 停批 + 续跑。

执行单元是「一对」：同一 (symbol, trade_date) 在两套隔离实例上各跑一次
（O = 基线、N = 处理臂；可按清单预抽第三个 O2）。一对内的所有请求必须**连续
跑完**，不允许把 O 的某次重试与 N 的某次重试拆到不同批次里——否则两侧观察
到的市场状态就不可比了。

逐份核对用的是每份报告 ``result_data.served_models``：它来自 DAV-1430 对
**响应体里的 ``model`` 字段**（即 ``message.model``，provider 实际服务的模型）
的采集，不是本地配置里的名义模型名。核对口径：

* 对内不一致：同一对里 O / N / O2 报告出现不止一个 served model → 整对作废；
* 与冻结身份不一致：任一报告的 served model 不等于清单里冻结的期望值 → 整对作废；
* served model 缺失（响应体没带 ``model``，或调用在读到之前就失败）→ 无法核对，
  按作废处理，绝不默认放行。

作废的对允许**补跑 1 次**（``max_pair_retries=1``），重跑同样要过完整核对；再失败
就记为最终失败，不再补跑。

停批条件（任一命中即在**当前这一对跑完后**停，不打断进行中的那一对）：

1. 429 或配额类错误（``rate_limit`` / ``quota`` / ``insufficient_quota``…）；
2. 出现非冻结模型（响应体报的模型不在冻结集合里）；
3. 单批失败率 > 5%（失败对数 / 该批已完成对数，含作废）；
4. 达到批预算或全局上限 20,328 次分析请求。

续跑：``state.json`` 记录每个「对」的终态（``ok`` / ``voided`` / ``failed``，
以及用掉几次补跑）。再次启动时已完成的对一律不重跑，只补没跑过的。

交易日 16:00–19:50（Asia/Shanghai）自动不跑：默认等���窗口结束再继续
（``--blackout-policy stop`` 则原地停下，下次续跑接着走）。

用法（真实实例）::

    python scripts/phase2/experiment_executor.py run \
        --manifest work/exp-1789/manifest.json \
        --instance O=http://127.0.0.1:8021 \
        --instance N=http://127.0.0.1:8022 \
        --state-dir work/exp-1789/state

``--total-request-cap`` 默认 20328，与总控 10-11 令一致。

测试用桩服务跑在内存里（见 ``tests/test_dav1882_experiment_executor.py``），
覆盖作废、续跑、停批三条路径。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # requests is the only runtime dep; tests inject a stub transport
    import requests
except Exception:  # pragma: no cover - import guard
    requests = None  # type: ignore[assignment]

CN_TZ = timezone(timedelta(hours=8))

# 总控 10-11 令：全局分析请求上限
DEFAULT_TOTAL_REQUEST_CAP = 20_328
# 单批失败率停批阈值（严格大于）
DEFAULT_FAIL_RATE_THRESHOLD = 0.05
# 交易日 16:00–19:50 自动不跑
BLACKOUT_START = dt_time(16, 0)
BLACKOUT_END = dt_time(19, 50)
# 作废对允许的补跑次数
DEFAULT_PAIR_RETRIES = 1

# 失败率的分母下限：完成 1 对即开始判定（0/1 = 0% 不停批）
MIN_FAIL_RATE_SAMPLES = 1

_QUOTA_MARKERS = (
    "rate_limit",
    "rate limit",
    "ratelimit",
    "too many requests",
    "429",
    "quota",
    "insufficient_quota",
    "exceeded your current quota",
    "billing",
    "余额不足",
    "配额",
    "限流",
)

STOP_ALL_PAIRS_DONE = "all_pairs_done"
STOP_QUOTA = "quota_or_rate_limit"
STOP_NON_FROZEN_MODEL = "non_frozen_model"
STOP_FAIL_RATE = "batch_fail_rate"
STOP_BUDGET = "batch_or_total_budget"
STOP_BLACKOUT = "trading_day_blackout"
STOP_MAX_RETRIES = "pair_max_retries"


# ---------------------------------------------------------------------------
# 冻结清单
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairSpec:
    """一对 = 同一 (symbol, trade_date) 落在若干实例上各跑一次。"""

    pair_id: str
    symbol: str
    trade_date: str
    # 实例顺序；默认 ["O", "N"]，预抽 O2 时为 ["O", "N", "O2"]（O2 必须紧跟 N 连跑）
    instances: Tuple[str, ...] = ("O", "N")
    # 冻结身份：该对期望的 ``message.model`` 值。整对所有报告都必须等于它。
    expected_model: Optional[str] = None
    horizons: Tuple[str, ...] = ("short",)
    # 附加的 analyze 请求体覆盖（两套实例必须一致，否则不是单因素）
    config_overrides: Tuple[Tuple[str, Any], ...] = ()

    def overrides(self) -> Dict[str, Any]:
        return dict(self.config_overrides)


@dataclass(frozen=True)
class Manifest:
    """冻结的样本清单与批序。"""

    name: str
    pairs: Tuple[PairSpec, ...]
    # 批序：每批一批 pair_id，顺序即执行顺序；同批共用一个失败率与批预算
    batches: Tuple[Tuple[str, ...], ...] = ()
    # 冻结模型集合（served_model 必须落在这里面，否则算非冻结模型）
    frozen_models: Tuple[str, ...] = ()

    def pair_by_id(self, pair_id: str) -> PairSpec:
        for p in self.pairs:
            if p.pair_id == pair_id:
                return p
        raise KeyError(pair_id)

    def ordered_batches(self) -> List[List[PairSpec]]:
        if not self.batches:
            return [list(self.pairs)]
        seen = set()
        out: List[List[PairSpec]] = []
        for bid_batch in self.batches:
            batch = [self.pair_by_id(pid) for pid in bid_batch]
            seen.update(pid for pid in bid_batch)
            out.append(batch)
        # 清单里有、但批序没列的对不会被静默丢弃——按清单顺序补到最后一批。
        leftovers = [p for p in self.pairs if p.pair_id not in seen]
        if leftovers:
            if out:
                out[-1] = out[-1] + leftovers
            else:
                out.append(leftovers)
        return out

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Manifest":
        pairs = tuple(
            PairSpec(
                pair_id=p["pair_id"],
                symbol=p["symbol"],
                trade_date=p["trade_date"],
                instances=tuple(p.get("instances") or ("O", "N")),
                expected_model=p.get("expected_model"),
                horizons=tuple(p.get("horizons") or ("short",)),
                config_overrides=tuple(sorted((p.get("config_overrides") or {}).items())),
            )
            for p in payload["pairs"]
        )
        return cls(
            name=payload.get("name", "experiment"),
            pairs=pairs,
            batches=tuple(tuple(b) for b in payload.get("batches") or ()),
            frozen_models=tuple(payload.get("frozen_models") or ()),
        )

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 记账
# ---------------------------------------------------------------------------


@dataclass
class AttemptRecord:
    """一次「对」的尝试（首跑或补跑）的逐次记账。"""

    pair_id: str
    attempt: int
    batch_index: int
    started_at: str
    finished_at: str = ""
    elapsed_seconds: float = 0.0
    # 实例名 -> 该实例这一次请求的明细
    runs: List[Dict[str, Any]] = field(default_factory=list)
    analysis_requests: int = 0
    http_requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    served_models: List[str] = field(default_factory=list)
    served_model_missing: int = 0
    error: Optional[str] = None
    outcome: str = "unknown"  # ok / voided / failed
    void_reason: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PairState:
    pair_id: str
    status: str = "pending"  # pending / ok / voided / failed
    attempts: int = 0
    reason: Optional[str] = None
    served_models: List[str] = field(default_factory=list)
    last_attempt_index: Optional[int] = None


class Ledger:
    """逐次记账：每次尝试一行 JSONL，外加一个可续跑的状态文件。"""

    def __init__(self, state_dir: Path, *, now: Callable[[], datetime] = None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.state_dir / "ledger.jsonl"
        self.state_path = self.state_dir / "state.json"
        self.now = now or (lambda: datetime.now(CN_TZ))
        self.attempt_seq = 0
        self.pairs: Dict[str, PairState] = {}
        self.totals = {
            "analysis_requests": 0,
            "http_requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "elapsed_seconds": 0.0,
        }
        self.stop_reason: Optional[str] = None
        self.stop_detail: Optional[str] = None
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.attempt_seq = int(payload.get("attempt_seq") or 0)
        self.totals.update(payload.get("totals") or {})
        self.stop_reason = payload.get("stop_reason")
        self.stop_detail = payload.get("stop_detail")
        for item in payload.get("pairs") or []:
            self.pairs[item["pair_id"]] = PairState(
                pair_id=item["pair_id"],
                status=item.get("status", "pending"),
                attempts=int(item.get("attempts") or 0),
                reason=item.get("reason"),
                served_models=list(item.get("served_models") or []),
                last_attempt_index=item.get("last_attempt_index"),
            )

    def flush(self) -> None:
        payload = {
            "attempt_seq": self.attempt_seq,
            "totals": self.totals,
            "stop_reason": self.stop_reason,
            "stop_detail": self.stop_detail,
            "updated_at": self.now().isoformat(timespec="seconds"),
            "pairs": [asdict(p) for p in self.pairs.values()],
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def append_attempt(self, record: AttemptRecord) -> None:
        with self.ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- accessors ---------------------------------------------------------
    def pair_state(self, pair_id: str) -> PairState:
        return self.pairs.setdefault(pair_id, PairState(pair_id=pair_id))

    def is_terminal(self, pair_id: str) -> bool:
        st = self.pairs.get(pair_id)
        return bool(st and st.status in {"ok", "voided", "failed"})

    def next_attempt_index(self) -> int:
        self.attempt_seq += 1
        return self.attempt_seq

    def record_outcome(
        self,
        pair_id: str,
        *,
        status: str,
        reason: Optional[str],
        served_models: Sequence[str],
        attempt_index: int,
    ) -> None:
        st = self.pair_state(pair_id)
        st.status = status
        st.attempts += 1
        st.reason = reason
        st.served_models = list(served_models)
        st.last_attempt_index = attempt_index

    def account(self, record: AttemptRecord) -> None:
        self.totals["analysis_requests"] += record.analysis_requests
        self.totals["http_requests"] += record.http_requests
        self.totals["prompt_tokens"] += record.prompt_tokens
        self.totals["completion_tokens"] += record.completion_tokens
        self.totals["total_tokens"] += record.total_tokens
        self.totals["elapsed_seconds"] += record.elapsed_seconds

    def total_analysis_requests(self) -> int:
        return int(self.totals["analysis_requests"])


# ---------------------------------------------------------------------------
# 结果分类
# ---------------------------------------------------------------------------


def classify_error(text: Optional[str], status_code: Optional[int] = None) -> str:
    """把一次请求/一次 job 的失败归到桶里。返回 ``none`` 表示没失败。"""
    blob = f"{text or ''}".lower()
    if status_code == 429 or any(m in blob for m in _QUOTA_MARKERS):
        return "quota_or_rate_limit"
    if status_code and status_code >= 500:
        return "server_error"
    if status_code and status_code >= 400:
        return "client_error"
    if status_code:
        return "none"
    if not text:
        return "none"
    if any(m in blob for m in ("timeout", "timed out", "超时")):
        return "timeout"
    if any(m in blob for m in ("connection", "connect", "reset", "连接")):
        return "connection"
    return "error"


def flatten_served_models(result: Optional[Dict[str, Any]]) -> Tuple[List[str], int]:
    """从 job result 里取出 ``message.model`` 观测值。

    ``result_data.served_models`` 是 DAV-1430 采集的 provider 响应体 model 字段。
    返回 ``(去重后的模型列表, 没有可核对模型的调用数)``。
    """
    if not isinstance(result, dict):
        return [], 1
    served = result.get("served_models")
    if not isinstance(served, dict):
        return [], 1
    by_role = served.get("by_role")
    if not isinstance(by_role, dict):
        return [], 1
    models: List[str] = []
    for values in by_role.values():
        if isinstance(values, str):
            values = [values]
        for v in values or []:
            if isinstance(v, str) and v and v not in models:
                models.append(v)
    missing = served.get("served_model_missing")
    try:
        missing_n = int(missing or 0)
    except (TypeError, ValueError):
        missing_n = 0
    if not models:
        # 一个都没采到就没法核对，按缺失计。
        return [], max(1, missing_n)
    return models, missing_n


def verify_pair(pair: PairSpec, per_instance: Dict[str, Dict[str, Any]]) -> Tuple[str, Optional[str], List[str]]:
    """核对一对。返回 ``(outcome, reason, 观测到的模型列表)``。

    ``outcome`` ∈ ``ok`` / ``voided`` / ``failed``：

    * ``ok``：每个实例都跑成、served model 齐全、对内一致且等于冻结值；
    * ``voided``：请求都成功了但核对不过（对内不一致 / 非冻结模型 / 缺失）；
    * ``failed``：有实例请求本身失败（含配额类）。
    """
    observed: List[str] = []
    request_failures: List[str] = []
    quota = False
    unverified: List[str] = []
    for name in pair.instances:
        info = per_instance.get(name)
        if info is None:
            request_failures.append(f"{name}:missing")
            continue
        bucket = info.get("error_bucket") or "none"
        if bucket != "none":
            request_failures.append(f"{name}:{bucket}")
            if bucket == "quota_or_rate_limit":
                quota = True
            continue
        # 请求本身成功。采到模型就算可核对；一份都没采到就无法核对，
        # 绝不默认放行。
        models = info.get("models") or []
        if not models:
            unverified.append(f"{name}:model_missing")
            continue
        for m in models:
            if m not in observed:
                observed.append(m)

    # 请求本身失败（超时、5xx、配额……）优先于核对结论：补跑也不会变好。
    if request_failures:
        kind = "quota_or_rate_limit" if quota else "request_failed"
        return "failed", f"{kind}:{';'.join(request_failures)}", observed

    # 核对不通过：作废，给补跑 1 次机会。
    if unverified:
        return "voided", f"model_unverifiable:{';'.join(unverified)}", observed
    if len(observed) != 1:
        return "voided", f"model_mismatch_across_pair:{observed}", observed

    only = observed[0]
    if pair.expected_model is not None and only != pair.expected_model:
        return "voided", f"model_not_frozen:{only}!={pair.expected_model}", observed
    return "ok", None, observed


# ---------------------------------------------------------------------------
# 交易日 16:00–19:50 静默窗口
# ---------------------------------------------------------------------------


def in_blackout(now: datetime) -> bool:
    """交易日 16:00–19:50（含端点语义：16:00 起，19:50 止）自动不跑。"""
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    else:
        now = now.astimezone(CN_TZ)
    if now.weekday() >= 5:  # 周末休市
        return False
    return BLACKOUT_START <= now.time() < BLACKOUT_END


def is_trading_day(date_str: str) -> bool:
    """交易日判定。

    项目日历要联网取 akShare / 富途，取不到是常态；静默窗口只在工作日有
    意义，因此日历不可用时退回「周一–周五」并如实记到 detail，绝不让日历
    网络故障把 16:00–19:50 的静默窗口整体跳过。
    """
    try:  # pragma: no cover - 依赖外部日历，测试用打桩覆盖
        from tradingagents.dataflows.trade_calendar import is_cn_trading_day

        return bool(is_cn_trading_day(date_str))
    except Exception:
        y, m, d = (int(x) for x in date_str.split("-"))
        return datetime(y, m, d).weekday() < 5


def blackout_active(now: datetime) -> bool:
    if not is_trading_day(now.astimezone(CN_TZ).date().isoformat()):
        return False
    return in_blackout(now)


def seconds_to_blackout_end(now: datetime) -> float:
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    end = now.replace(hour=BLACKOUT_END.hour, minute=BLACKOUT_END.minute, second=0, microsecond=0)
    return max(1.0, (end - now).total_seconds())


# ---------------------------------------------------------------------------
# HTTP 传输（可注入桩）
# ---------------------------------------------------------------------------


class HttpTransport:
    """极薄的一层，方便测试打桩。"""

    def __init__(self, session: Any = None, *, timeout: float = 60.0):
        self.session = session or requests.Session()
        self.timeout = timeout

    def request(self, method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
                json_body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        resp = self.session.request(
            method, url, headers=headers, json=json_body, timeout=self.timeout
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"raw": getattr(resp, "text", "")}


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------


@dataclass
class StopConfig:
    total_request_cap: int = DEFAULT_TOTAL_REQUEST_CAP
    batch_request_cap: Optional[int] = None
    fail_rate_threshold: float = DEFAULT_FAIL_RATE_THRESHOLD
    min_fail_rate_samples: int = MIN_FAIL_RATE_SAMPLES
    pair_retries: int = DEFAULT_PAIR_RETRIES
    blackout_policy: str = "wait"  # wait / stop


@dataclass
class RunReport:
    manifest: str
    started_at: str
    finished_at: str = ""
    pairs_planned: int = 0
    pairs_run: int = 0
    pairs_skipped_done: int = 0
    pairs_ok: int = 0
    pairs_voided: int = 0
    pairs_failed: int = 0
    pairs_rerun: int = 0
    stop_reason: Optional[str] = None
    stop_detail: Optional[str] = None
    totals: Dict[str, Any] = field(default_factory=dict)
    batches: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


class ExperimentExecutor:
    """配对原子执行器。"""

    def __init__(
        self,
        *,
        instances: Dict[str, str],
        ledger: Ledger,
        token_for: Callable[[str], Optional[str]] = lambda name: None,
        transport_factory: Callable[[str, str], HttpTransport] = None,
        config: StopConfig = None,
        now: Callable[[], datetime] = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 5.0,
        job_timeout: float = 7200.0,
    ):
        self.instances = dict(instances)
        self.ledger = ledger
        self.token_for = token_for
        self.transport_factory = transport_factory or (
            lambda name, base_url: HttpTransport()
        )
        self.config = config or StopConfig()
        self.now = now or (lambda: datetime.now(CN_TZ))
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.job_timeout = job_timeout
        self._transports: Dict[str, HttpTransport] = {}
        self._last_batch_records: List[AttemptRecord] = []
        self._non_frozen_seen: List[Tuple[str, str]] = []

    # -- transport ---------------------------------------------------------
    def transport(self, name: str) -> HttpTransport:
        if name not in self._transports:
            self._transports[name] = self.transport_factory(name, self.instances[name])
        return self._transports[name]

    def headers(self, name: str) -> Dict[str, str]:
        token = self.token_for(name)
        return {"Authorization": f"Bearer {token}"} if token else {}

    # -- 窗口 ---------------------------------------------------------------
    def wait_out_blackout(self, report: RunReport) -> bool:
        """返回 True 表示因窗口停下（stop 策略）。"""
        if not blackout_active(self.now()):
            return False
        if self.config.blackout_policy == "stop":
            self.ledger.stop_reason = STOP_BLACKOUT
            self.ledger.stop_detail = "16:00-19:50 交易日静默窗口，stop 策略"
            return True
        self.sleep(seconds_to_blackout_end(self.now()))
        return False

    def _call(self, transport, method, url, headers, body, record) -> Tuple[int, Any]:
        """发一次请求并记账。传输层异常转成 ``(0, 错误文本)``，不往上抛。

        一个对里任何一次请求报错都只会让**这一对**变成 failed 并照实记账；
        批量执行不能因为某个实例掉线就把整批丢掉。
        """
        try:
            code, payload = transport.request(method, url, headers=headers, json_body=body)
        except Exception as exc:
            record.http_requests += 1
            if method == "POST":
                record.analysis_requests += 1
            return 0, f"{type(exc).__name__}: {exc}"
        record.http_requests += 1
        if method == "POST":
            record.analysis_requests += 1
        return code, payload

    # -- 单实例一次分析 ----------------------------------------------------
    def run_one(self, name: str, pair: PairSpec, record: AttemptRecord) -> Dict[str, Any]:
        transport = self.transport(name)
        base = self.instances[name].rstrip("/")
        headers = self.headers(name)
        body = {
            "symbol": pair.symbol,
            "trade_date": pair.trade_date,
            "horizons": list(pair.horizons),
        }
        if pair.overrides():
            body["config_overrides"] = pair.overrides()

        info: Dict[str, Any] = {
            "instance": name,
            "job_id": None,
            "status": "pending",
            "error_bucket": "none",
            "error": None,
            "models": [],
            "model_missing": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        code, payload = self._call(
            transport, "POST", f"{base}/v1/analyze", headers, body, record
        )
        if code == 0:
            # 传输层报错（实例不可达 / 连接重置）→ 记失败，不拖垮整批
            info["status"] = "submit_failed"
            info["error"] = payload
            info["error_bucket"] = classify_error(payload)
            return info
        if code != 200 or not isinstance(payload, dict) or not payload.get("job_id"):
            info["status"] = "submit_failed"
            info["error"] = str(payload.get("detail") if isinstance(payload, dict) else payload)
            info["error_bucket"] = classify_error(info["error"], code)
            return info

        job_id = payload["job_id"]
        info["job_id"] = job_id
        started = time.monotonic()
        deadline = started + self.job_timeout
        while True:
            code, job = self._call(
                transport, "GET", f"{base}/v1/jobs/{job_id}", headers, None, record
            )
            if code == 0:
                info["error"] = job
                info["error_bucket"] = classify_error(job)
                info["status"] = "status_failed"
                return info
                info["error"] = str(job)
                info["error_bucket"] = classify_error(info["error"], code)
                info["status"] = "status_failed"
                return info
            status = job.get("status")
            if status in ("completed", "failed"):
                break
            if time.monotonic() > deadline:
                info["status"] = "timeout"
                info["error"] = f"job {job_id} timeout after {self.job_timeout}s"
                info["error_bucket"] = "timeout"
                return info
            self.sleep(self.poll_interval)

        info["status"] = status
        info["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if status != "completed":
            info["error"] = job.get("error") or "job failed"
            info["error_bucket"] = classify_error(info["error"])
            return info

        code, result = self._call(
            transport, "GET", f"{base}/v1/jobs/{job_id}/result", headers, None, record
        )
        if code == 0:
            info["error"] = str(result)
            info["error_bucket"] = classify_error(str(result))
            return info
        if code != 200:
            info["error"] = f"result fetch failed: {code} {result}"
            info["error_bucket"] = classify_error(info["error"], code)
            return info

        # /v1/jobs/{id}/result returns {"result": <result_data>, ...}
        payload_result = result.get("result") if isinstance(result, dict) else None
        models, missing = flatten_served_models(payload_result)
        info["models"] = models
        info["model_missing"] = missing
        usage = payload_result.get("usage_summary") if isinstance(payload_result, dict) else None
        if isinstance(usage, dict):
            info["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
            info["completion_tokens"] = int(usage.get("completion_tokens") or 0)
            info["total_tokens"] = int(usage.get("total_tokens") or 0)
        return info

    # -- 一对 ---------------------------------------------------------------
    def run_pair(self, pair: PairSpec, batch_index: int) -> AttemptRecord:
        """原子执行：把 pair.instances 里所有实例连着跑完，再统一核对。"""
        index = self.ledger.next_attempt_index()
        record = AttemptRecord(
            pair_id=pair.pair_id,
            attempt=self.ledger.pair_state(pair.pair_id).attempts + 1,
            batch_index=batch_index,
            started_at=self.now().isoformat(timespec="seconds"),
        )
        wall = time.monotonic()
        per_instance: Dict[str, Dict[str, Any]] = {}
        for name in pair.instances:
            if name not in self.instances:
                info = {
                    "instance": name, "job_id": None, "status": "unknown_instance",
                    "error_bucket": "client_error",
                    "error": f"实例 {name} 未配置", "models": [], "model_missing": 0,
                }
            else:
                info = self.run_one(name, pair, record)
            per_instance[name] = info
            record.runs.append(info)
            record.prompt_tokens += info.get("prompt_tokens", 0)
            record.completion_tokens += info.get("completion_tokens", 0)
            record.total_tokens += info.get("total_tokens", 0)

        outcome, reason, observed = verify_pair(pair, per_instance)
        record.outcome = outcome
        record.void_reason = reason
        record.served_models = observed
        record.served_model_missing = sum(
            info.get("model_missing", 0) for info in per_instance.values()
        )
        record.error = next(
            (info.get("error") for info in per_instance.values() if info.get("error")), None
        )
        record.elapsed_seconds = round(time.monotonic() - wall, 3)
        record.finished_at = self.now().isoformat(timespec="seconds")

        self.ledger.record_outcome(
            pair.pair_id, status=outcome, reason=reason,
            served_models=observed, attempt_index=index,
        )
        self.ledger.append_attempt(record)
        self.ledger.account(record)
        self.ledger.flush()
        return record

    # -- 停批 ---------------------------------------------------------------
    def batch_fail_rate(self, outcomes: Sequence[str]) -> float:
        if not outcomes:
            return 0.0
        bad = sum(1 for o in outcomes if o != "ok")
        return bad / len(outcomes)

    def check_stop(self, outcomes: Sequence[str], batch_requests: int,
                   batch_touched: bool = False) -> Optional[Tuple[str, str]]:
        for rec in self._last_batch_records:
            if rec.void_reason and rec.void_reason.startswith("quota_or_rate_limit"):
                return STOP_QUOTA, f"{rec.pair_id} {rec.void_reason}"
        if self._non_frozen_seen:
            return STOP_NON_FROZEN_MODEL, f"{self._non_frozen_seen[0][0]} {self._non_frozen_seen[0][1]}"
        # 失败率是停批项 → 必须在**对**的粒度上判。只数「已发起提交」的对会
        # 把当前这一对漏出分母（例：1/19 = 5.26% 会被读成 0/19 = 0%），因此
        # 只在停批原因属于配额/非冻结模型/预算这些与对无关的条件时才用
        # 「已发起」口径。
        if not batch_touched:
            n = len(outcomes)
            if n >= self.config.min_fail_rate_samples:
                rate = self.batch_fail_rate(outcomes)
                if rate > self.config.fail_rate_threshold:
                    return STOP_FAIL_RATE, f"{rate:.4f} > {self.config.fail_rate_threshold}"
        cap = self.config.batch_request_cap
        if cap is not None and batch_requests >= cap:
            return STOP_BUDGET, f"batch {batch_requests} >= cap {cap}"
        if self.ledger.total_analysis_requests() >= self.config.total_request_cap:
            return STOP_BUDGET, (
                f"total {self.ledger.total_analysis_requests()} >= cap "
                f"{self.config.total_request_cap}"
            )
        return None

    # -- 主循环 -------------------------------------------------------------
    def run(self, manifest: Manifest) -> RunReport:
        report = RunReport(
            manifest=manifest.name,
            started_at=self.now().isoformat(timespec="seconds"),
            pairs_planned=len(manifest.pairs),
        )
        batches = manifest.ordered_batches()
        for bi, batch in enumerate(batches):
            if self.ledger.stop_reason:
                break
            outcomes: List[str] = []
            batch_requests = 0
            batch_touched = False
            self._last_batch_records = []
            self._non_frozen_seen = []
            for pair in batch:
                if self.ledger.is_terminal(pair.pair_id):
                    report.pairs_skipped_done += 1
                    continue
                if self.wait_out_blackout(report):
                    break
                batch_touched = True
                record = self.run_pair(pair, bi)
                self._last_batch_records.append(record)
                batch_requests += record.analysis_requests
                report.pairs_run += 1
                if record.attempt > 1:
                    report.pairs_rerun += 1
                if record.outcome == "ok":
                    report.pairs_ok += 1
                elif record.outcome == "voided":
                    report.pairs_voided += 1
                else:
                    report.pairs_failed += 1

                # 停批条件先判：命中就不再消耗配额补跑——配额/预算类错误下
                # 补跑只会把停批往前推，非冻结模型下补跑也无意义。
                stop = self.check_stop(outcomes, batch_requests, batch_touched)
                if record.outcome == "voided" and stop is None:
                    # 作废的对补跑 1 次；补跑仍不过就定终态，不无限重试。
                    if self.ledger.pair_state(pair.pair_id).attempts <= self.config.pair_retries:
                        rerun = self.run_pair(pair, bi)
                        self._last_batch_records.append(rerun)
                        batch_requests += rerun.analysis_requests
                        report.pairs_run += 1
                        report.pairs_rerun += 1
                        if rerun.outcome == "ok":
                            report.pairs_ok += 1
                        elif rerun.outcome == "voided":
                            report.pairs_voided += 1
                        else:
                            report.pairs_failed += 1
                        self._note_non_frozen(manifest, pair, rerun)
                        stop = self.check_stop(outcomes, batch_requests, batch_touched)
                self._note_non_frozen(manifest, pair, record)
                # 失败率只按「每个对一条」计：作废对补跑后按补跑结果定终态，
                # 首跑与补跑各计一次会把分母翻倍。
                outcomes.append(self.ledger.pair_state(pair.pair_id).status)

                stop = self.check_stop(outcomes, batch_requests)
                if stop:
                    self.ledger.stop_reason, self.ledger.stop_detail = stop
                    self.ledger.flush()
                    break
            report.batches.append({
                "batch_index": bi,
                "pairs": len(batch),
                "requests": batch_requests,
                "outcomes": outcomes,
                "fail_rate": round(self.batch_fail_rate(outcomes), 4),
            })
        report.stop_reason = self.ledger.stop_reason
        report.stop_detail = self.ledger.stop_detail
        report.totals = dict(self.ledger.totals)
        report.finished_at = self.now().isoformat(timespec="seconds")
        self.ledger.flush()
        return report

    def _note_non_frozen(self, manifest: Manifest, pair: PairSpec, record: AttemptRecord) -> None:
        """记录「服务端报了个不在冻结集合里的模型」。"""
        frozen = set(manifest.frozen_models)
        expected = pair.expected_model
        for model in record.served_models:
            if expected is not None and model != expected:
                self._non_frozen_seen.append((pair.pair_id, f"expected {expected} got {model}"))
            elif expected is None and frozen and model not in frozen:
                self._non_frozen_seen.append((pair.pair_id, f"{model} not in frozen set"))
            elif expected is not None and model == expected and frozen and model not in frozen:
                self._non_frozen_seen.append((pair.pair_id, f"{model} not in frozen set"))

    _last_batch_records: List[AttemptRecord] = []
    _non_frozen_seen: List[Tuple[str, str]] = []

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DAV-1882 配对原子实验执行器")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="按清单执行")
    run.add_argument("--manifest", required=True)
    run.add_argument("--instance", action="append", default=[],
                     help="实例名=基址，如 O=http://127.0.0.1:8021（可重复）")
    run.add_argument("--state-dir", required=True)
    run.add_argument("--token", action="append", default=[],
                     help="实例名=token（可重复；只落 state 文件，不回显）")
    run.add_argument("--total-request-cap", type=int, default=DEFAULT_TOTAL_REQUEST_CAP)
    run.add_argument("--batch-request-cap", type=int, default=None)
    run.add_argument("--fail-rate-threshold", type=float, default=DEFAULT_FAIL_RATE_THRESHOLD)
    run.add_argument("--pair-retries", type=int, default=DEFAULT_PAIR_RETRIES)
    run.add_argument("--blackout-policy", choices=["wait", "stop"], default="wait")
    run.add_argument("--poll-interval", type=float, default=5.0)
    run.add_argument("--job-timeout", type=float, default=7200.0)

    st = sub.add_parser("status", help="只看当前记账与停批原因")
    st.add_argument("--state-dir", required=True)
    return p


def _instances_from_args(pairs: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise SystemExit(f"--instance 需要 名称=基址 形式，收到 {item!r}")
        name, url = item.split("=", 1)
        out[name.strip()] = url.strip()
    return out


def _tokens_from_args(pairs: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in pairs:
        if "=" in item:
            name, token = item.split("=", 1)
            out[name.strip()] = token.strip()
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    state_dir = Path(args.state_dir)

    if args.cmd == "status":
        ledger = Ledger(state_dir)
        print(json.dumps({
            "totals": ledger.totals,
            "stop_reason": ledger.stop_reason,
            "stop_detail": ledger.stop_detail,
            "pairs": [asdict(p) for p in ledger.pairs.values()],
        }, ensure_ascii=False, indent=2))
        return 0

    instances = _instances_from_args(args.instance)
    tokens = _tokens_from_args(args.token)
    manifest = Manifest.load(Path(args.manifest))
    ledger = Ledger(state_dir)
    executor = ExperimentExecutor(
        instances=instances,
        ledger=ledger,
        token_for=lambda name: tokens.get(name),
        config=StopConfig(
            total_request_cap=args.total_request_cap,
            batch_request_cap=args.batch_request_cap,
            fail_rate_threshold=args.fail_rate_threshold,
            pair_retries=args.pair_retries,
            blackout_policy=args.blackout_policy,
        ),
        poll_interval=args.poll_interval,
        job_timeout=args.job_timeout,
    )
    report = executor.run(manifest)
    (state_dir / "run_report.json").write_text(
        json.dumps(report.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
