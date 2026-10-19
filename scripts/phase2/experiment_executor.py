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

交易日 16:00–19:50（Asia/Shanghai）自动不跑：默认等到静默窗口结束再继续
（``--blackout-policy stop`` 则原地停下，下次续跑接着走）。
每个交易日 19:50 后最多跑一批（总控 10-12 晚裁定）；未开闸时同样默认等
到开闸，``--daily-gate-policy stop`` 则原地停下，下次续跑接着走。

用法（真实实例）::

    python scripts/phase2/experiment_executor.py run \
        --manifest work/exp-1789/manifest.json \
        --instance O=http://127.0.0.1:8021 \
        --instance N=http://127.0.0.1:8022 \
        --state-dir work/exp-1789/state \
        --daily-gate-policy stop \
        --snapshot-whitelist work/exp-1789/whitelist.json

``--total-request-cap`` 默认 20328，与总控 10-11 令一致。

DAV-2285（总控 10-20 令）四项收紧：

1. **每次请求都带 ``experiment_run_identity``**：由 ``pair_id`` / 批号 /
   实例名 / attempt / 本次 uuid 现造，附在 ``POST /v1/analyze`` 请求体里；
   取回结果后**回读核对**服务端是否真的承接（``--run-identity-policy
   require`` 默认 fail-closed）——当前 O/N 服务端
   ``AnalyzeRequest`` 会静默丢弃未声明字段，只发不核对等于没发。
2. **没给某实例配 ``--token`` 就拒绝启动**，并对每份报告回读 ``user_id``
   核对归属固定账户（``--expect-account``）。不配 token 时服务端
   ``RequireUser`` 会退到「本地默认账户」，请求照样成功，却不属实验口径的
   固定账户——这类静默串号必须在开跑前就挡住。
3. **轮询遇连接中断不直接判失败**：改为按退避重新查询服务端终态；重查耗尽
   仍拿不到才记失败（``connection`` 桶），并如实记 ``poll_reconnects``。
4. **``--pair-retries`` 对已判 ``failed`` 的对同样生效**：原先补跑只挂在
   ``voided`` 分支上，请求本身失败的对永远不会用掉这次机会。配额类停批下
   仍不补跑（重试无意义且白烧额度）。另：原补跑是单个 ``if``，
   ``--pair-retries > 1`` 时第二次补跑永不发生（声明的次数是假的），
   现改为按剩余额度循环。
5. **凭据 / 身份门禁不通过即停批**（``run_guard_failed``，持久化到
   ``state.json``）：这是**系统性**状态（服务端未承接身份、或凭据指向了非固定
   账户），不是这一对的偶发波动。不立刻停的话，5 对 × 2 臂 × 首跑+补跑
   = 20 次真实请求会全部白烧。

主干守卫（DAV-2204）：``run --batch-spec`` 时先校验「所在检出 HEAD ==
``batch_spec.sampler.trunk_commit``」，不符即**拒绝运行**并给出原因码
（``trunk_head_mismatch`` / ``trunk_head_unresolved`` / ``trunk_commit_unspecified``），
取样与任何请求都不发生；``--manifest`` 与无该字段的老口径不启用守卫。

测试用桩服务跑在内存里（见 ``tests/test_dav1882_experiment_executor.py``），
覆盖作废、续跑、停批三条路径。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
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

# 前瞻取样的种子基数：种子 = SAMPLER_SEED_BASE + 批号，保证可复现
SAMPLER_SEED_BASE = 20261010
# 前瞻取样脚本（主干 m2_sampler：沪深300+中证500 当时成分，剔除 ST / 上市
# 不足 1 年 / 停牌，按申万一级分层）
SAMPLER_SCRIPT = "scripts/phase2/m2_sampler.py"

# 总控 10-12 晚裁定：批与基准日分离
#   * 每个交易日 19:50 后最多跑一批
#   * 一批只对应当天**一个**基准日 T
#   * 每个基准日最多 5 对（目标 40 基准日 × 5 对）
PAIRS_PER_BASE_DAY = 5
# 一天最多一批的执行闸：只允许在 19:50（静默窗口结束）之后起跑
DAILY_BATCH_AFTER = dt_time(19, 50)

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

# 主干守卫：开跑前校验「所在检出 HEAD == batch_spec 签发的 trunk_commit」。
# 三类原因码分别对应三种拒绝，避免只给一句人话而无法机器判定。
TRUNK_GUARD_MISMATCH = "trunk_head_mismatch"
TRUNK_GUARD_UNRESOLVED = "trunk_head_unresolved"
TRUNK_GUARD_UNSPECIFIED = "trunk_commit_unspecified"

# DAV-2285-1：请求体里带实验运行身份的键名。它与 O/N 被测代码落库的
# ``result_data.experiment_run_identity``（DAV-1477 P3 / DAV-1486 独立键名）
# 同名，便于服务端一旦支持就直接承接；当前 O/N 服务端
# ``AnalyzeRequest``（pydantic v2, extra 默认 ignore）会**静默丢弃**该字段，
# 所以执行器必须自己回读核对（见 ``verify_run_identity``），不得假设「发了
# 就到了」。
RUN_IDENTITY_KEY = "experiment_run_identity"

# DAV-2285-2：未给实例配 token / 回读核对归属不通过时的原因码。
PREFLIGHT_TOKEN_MISSING = "instance_token_missing"
PREFLIGHT_ACCOUNT_MISMATCH = "report_account_mismatch"
PREFLIGHT_ACCOUNT_UNAVAILABLE = "report_account_unavailable"

# DAV-2285-3：轮询遇连接中断时，不直接判失败，改为重新查询服务端终态。
# 重查次数与退避上限：够覆盖一次「实例瞬时重启 / 代理重连」，又不至于把
# 一次真掉线拖成挂到 job_timeout 的长等待。
POLL_RECONNECT_MAX_ATTEMPTS = 5
POLL_RECONNECT_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 20.0)

STOP_ALL_PAIRS_DONE = "all_pairs_done"
STOP_QUOTA = "quota_or_rate_limit"
STOP_NON_FROZEN_MODEL = "non_frozen_model"
STOP_FAIL_RATE = "batch_fail_rate"
STOP_BUDGET = "batch_or_total_budget"
STOP_BLACKOUT = "trading_day_blackout"
STOP_DAILY_GATE = "daily_batch_gate_not_open"
STOP_WEEKEND = "non_trading_day"
STOP_DUPLICATE_BASE_DAY = "duplicate_base_day"
# DAV-2285：凭据 / 身份门禁不通过是**系统性**状态（服务端没承接身份字段、或
# 凭据指向了非固定账户），不是这一对的偶发问题。不立刻停批的话，5 对 × 2 臂
# × 首跑+补跑 = 20 次真实请求会全部白烧。命中即停在当前这一对跑完之后。
STOP_RUN_GUARD = "run_guard_failed"

# 只有「需人工确认才能解除」的停批原因才写进 ``state.json``；窗口类原因
# （每日一批闸 / 静默窗口 / 周末 / 同基准日第二批）只是**本次进程所处时刻的
# 快照**，换个时刻续跑本就该继续。把它们持久化，续跑读到旧 state 时就会在
# ``run()`` 开头直接 break（``if self.ledger.stop_reason: break``），于是隔天
# 再来永远跑不动——必须有人手工改 state.json 才能解除的死锁。
PERSISTENT_STOP_REASONS = frozenset({
    STOP_QUOTA, STOP_NON_FROZEN_MODEL, STOP_FAIL_RATE, STOP_BUDGET,
    STOP_RUN_GUARD,
})


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

    def unlisted_pairs(self) -> List[PairSpec]:
        """批序里没列、但清单里有的对（加载时报错，不静默改写批序）。"""
        seen = {pid for batch in self.batches for pid in batch}
        return [p for p in self.pairs if p.pair_id not in seen]

    def ordered_batches(self) -> List[List[PairSpec]]:
        """按**冻结的批序**分批。

        批序里有、清单里没有的 pair_id 直接报错；反过来清单里有、批序没列的
        对**不并进任何既有批**——把 leftovers 塞进最后一批会悄悄改写那一批的
        失败率分母与批预算，与「批序冻结」的实验设计初衷冲突。这两种不一致都
        必须由人显式改正清单，而不是由执行器替它猜。
        """
        if not self.batches:
            return [list(self.pairs)]
        out: List[List[PairSpec]] = []
        for bid_batch in self.batches:
            out.append([self.pair_by_id(pid) for pid in bid_batch])
        leftovers = self.unlisted_pairs()
        if leftovers:
            raise ValueError(
                "批序未覆盖这些对，拒绝执行（避免悄悄改写批序）："
                + ", ".join(p.pair_id for p in leftovers)
            )
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
# 前瞻取样：每批现取对名单（总控 10-12 令）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchSpec:
    """一批的取样口径。

    ``seed_base`` 是抽样种子基数，实际种子 = ``seed_base + batch_index``。
    默认 20261010（总控 10-12 令的硬口径），但它**真实生效**——传别的值就
    会改变取样，不得留下「声明可调、实际被忽略」的假开关。
    """

    batch_index: int
    date: str                       # YYYYMMDD，交给 m2_sampler 的信号日
    n: int                          # 该批抽多少个标的
    seed_base: int = SAMPLER_SEED_BASE
    # 签发这份口径时的主干提交；开跑前据此校验所在检出 HEAD（见
    # ``verify_trunk_head``）。老口径文件没有这个字段 → None → 不做守卫。
    trunk_commit: Optional[str] = None

    @property
    def sampler_seed(self) -> int:
        """种子 = seed_base + 批号，可复现。"""
        return self.seed_base + self.batch_index

    @property
    def base_day(self) -> str:
        """该批对应的基准日 T（YYYY-MM-DD）。一批只对应一个基准日。"""
        d = str(self.date)
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d


def load_batch_specs(path: Path, *, seed_base: Optional[int] = None) -> List[BatchSpec]:
    """读批序口径。

    ``seed_base`` 参数与命令行 ``--sampler-seed-base`` 优先于文件里的值；
    文件里若显式给了 ``seed_base`` 而与参数冲突，直接报错而不是静默取一个。
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = []
    for item in payload.get("batches") or []:
        file_base = item.get("seed_base")
        if seed_base is not None and file_base is not None and int(file_base) != int(seed_base):
            raise ValueError(
                f"批 {item.get('batch_index')}：文件 seed_base={file_base} 与传入的 "
                f"seed_base={seed_base} 冲突，拒绝静默取其一"
            )
        effective = seed_base if seed_base is not None else file_base
        sampler = payload.get("sampler")
        trunk_commit = None
        if isinstance(sampler, dict):
            trunk_commit = str(sampler.get("trunk_commit") or "").strip() or None
        specs.append(BatchSpec(
            batch_index=int(item["batch_index"]),
            date=str(item["date"]),
            n=int(item["n"]),
            seed_base=int(effective) if effective is not None else SAMPLER_SEED_BASE,
            trunk_commit=trunk_commit,
        ))
    if not specs:
        raise ValueError(f"{path}: batches 为空")
    return specs


def fetch_batch_pairs(
    spec: BatchSpec,
    *,
    instances: Sequence[str],
    expected_model: Optional[str],
    horizons: Sequence[str] = ("short",),
    runner: Callable[..., Dict[str, Any]] = None,
) -> Tuple[List[PairSpec], Dict[str, Any]]:
    """调用主干 m2_sampler 取该批对名单。

    返回 ``(pairs, sampler_result)``；``sampler_result`` 原样保留（含
    ``eligible_digest``），便于复现时核对同一份输入。

    已知局限（不在本轮修）：``pair_id`` 只由批号与标的构成，不含基准日与种子，
    同一标的两批重复命中时两次运行 pair_id 相同，续跑会把后一批误判成「已完成」。
    改它会作废已落盘 ``state.json`` / ``ledger.jsonl`` 里的既有 pair_id，与
    「续跑不得重跑已完成的对」冲突，需与总控确认口径后单独处理。
    """
    if runner is None:
        runner = _run_sampler_subprocess
    result = runner(spec)
    picks = result.get("picks") or []
    if not picks:
        raise ValueError(
            f"批 {spec.batch_index}@{spec.date} 取样为空（n={spec.n}），拒绝执行空批"
        )
    # 总控 10-12 晚裁定：每个基准日最多 5 对。取回来超了要显式报错，
    # 不能靠事后截断——那会让抽样结果与落盘名单对不上。
    if len(picks) > spec.n or len(picks) > PAIRS_PER_BASE_DAY:
        raise ValueError(
            f"批 {spec.batch_index}@{spec.base_day} 取样 {len(picks)} 对，"
            f"超过每基准日上限 {min(spec.n, PAIRS_PER_BASE_DAY)} 对，拒绝执行"
        )
    pairs = []
    for row in picks:
        raw = row.get("ts_code") if isinstance(row, dict) else None
        if raw is None and isinstance(row, dict):
            raw = row.get("symbol")
        symbol = str(raw or "").strip()
        if not symbol:
            raise ValueError(f"批 {spec.batch_index} 取样行缺 ts_code：{row!r}")
        pairs.append(PairSpec(
            pair_id=f"b{spec.batch_index:02d}-{symbol}",
            symbol=symbol,
            trade_date=result.get("trade_date") or spec.date,
            instances=tuple(instances),
            expected_model=expected_model,
            horizons=tuple(horizons),
        ))
    return pairs, result


def _run_sampler_subprocess(spec: BatchSpec) -> Dict[str, Any]:
    """以子进程跑 m2_sampler（它要 pandas/numpy + phase2 缓存）。"""
    import subprocess

    repo_root = Path(__file__).resolve().parents[2]
    cmd = [
        _locked_python(), str(repo_root / SAMPLER_SCRIPT),
        "--date", spec.date, "--n", str(spec.n), "--seed", str(spec.sampler_seed),
    ]
    env = dict(os.environ)
    env["DATABASE_URL"] = env.get("DATABASE_URL", "")  # 取样不碰生产库
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(repo_root))
    if proc.returncode != 0:
        raise RuntimeError(f"m2_sampler 失败 rc={proc.returncode}: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def _locked_python() -> str:
    """锁定解释器优先，避免拿系统 python3 跑取样。"""
    override = os.environ.get("DAV1882_PYTHON")
    if override:
        return override
    candidate = Path.home() / "Documents/TradingAgents-AShare/.venv310/bin/python"
    return str(candidate) if candidate.exists() else "python3"


# ---------------------------------------------------------------------------
# 主干守卫
# ---------------------------------------------------------------------------


class TrunkGuardError(RuntimeError):
    """主干守卫拒绝执行。``reason_code`` 供机器判定，文案供人读。"""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(f"[{reason_code}] {detail}")
        self.reason_code = reason_code
        self.detail = detail


class TokenPreflightError(RuntimeError):
    """DAV-2285-2：凭据前置拒绝启动。理由码同 ``TrunkGuardError``，口径一致。"""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(f"[{reason_code}] {detail}")
        self.reason_code = reason_code
        self.detail = detail


def resolve_trunk_head(repo_root: Optional[Path] = None) -> Optional[str]:
    """所在检出的完整 HEAD sha；无法确定（如非 git 工作树）时返回 None。"""
    import subprocess

    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip()
    return head or None


def verify_trunk_head(
    specs: Sequence[BatchSpec],
    *,
    head_sha: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> Tuple[Optional[str], str]:
    """开跑前的主干守卫。

    ``batch_spec`` 里的 ``sampler.trunk_commit`` 是签发口径时的主干提交。执行器
    必须跑在**同一份检出**上，否则取样器 / 缓存 / 核对口径都可能与签发时不同，
    跑出来的数据却记在同一份口径下。因此返回 ``(None, "")`` 放行，任何不一致
    返回 ``(原因码, 人话)``，由调用方拒绝执行。

    放行条件：

    1. 所有批的 ``trunk_commit`` 一致（不一致 → ``trunk_commit_unspecified``，
       否则无法判定该对齐谁）；
    2. ``trunk_commit`` 非空（老口径无此字段 → 不启用守卫，向后兼容）；
    3. 实际 HEAD 等于 ``trunk_commit``（短 sha 视为前缀匹配；HEAD 取不到 →
       ``trunk_head_unresolved``）。
    """
    declared = {s.trunk_commit for s in specs if s.trunk_commit is not None}
    if len(declared) > 1:
        return TRUNK_GUARD_UNSPECIFIED, (
            "batch_spec 各批的 trunk_commit 不一致（"
            + ", ".join(sorted(declared))
            + "），无法判定应对齐哪个主干，拒绝执行"
        )
    if not declared:
        return None, ""
    want = next(iter(declared))
    head = head_sha if head_sha is not None else resolve_trunk_head(repo_root)
    if not head:
        return TRUNK_GUARD_UNRESOLVED, (
            f"无法确定所在检出的 HEAD（git rev-parse 失败），无法校验是否等于 "
            f"trunk_commit={want}，拒绝执行"
        )
    if not (head == want or (len(want) >= 7 and head.startswith(want))):
        return TRUNK_GUARD_MISMATCH, (
            f"检出 HEAD={head} 与 batch_spec.trunk_commit={want} 不符，拒绝执行"
        )
    return None, ""


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
    # 结构化失败分类，停批判定读它而不是 reason 的字符串前缀
    error_bucket: str = "none"
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
        self.snapshot_whitelist_sha256: Optional[str] = None
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
        # 只认需人工确认才能解除的停批原因：旧版把窗口类原因也写了进来，在这里
        # 一并丢弃，否则被污染的 state.json 会让此后每次续跑都直接退出。
        raw_stop = payload.get("stop_reason")
        if raw_stop not in PERSISTENT_STOP_REASONS:
            raw_stop = None
        self.stop_reason = raw_stop
        self.stop_detail = payload.get("stop_detail") if raw_stop else None
        self.snapshot_whitelist_sha256 = payload.get("snapshot_whitelist_sha256")
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
        # 窗口类停批原因只留在内存：本次运行的 RunReport / run_report.json 仍照实
        # 记录（可事后审计），但不写回 state.json，理由见 PERSISTENT_STOP_REASONS。
        persistent = self.stop_reason in PERSISTENT_STOP_REASONS
        payload = {
            "attempt_seq": self.attempt_seq,
            "totals": self.totals,
            "stop_reason": self.stop_reason if persistent else None,
            "stop_detail": self.stop_detail if persistent else None,
            "snapshot_whitelist_sha256": self.snapshot_whitelist_sha256,
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


# ---------------------------------------------------------------------------
# DAV-2285-1：实验运行身份（随每次请求下发 + 回读核对）
# ---------------------------------------------------------------------------


def build_run_identity(
    *,
    pair_id: str,
    batch_index: int,
    instance: str,
    attempt: int,
    run_uuid: Optional[str] = None,
) -> Dict[str, Any]:
    """现造本次请求的 ``experiment_run_identity``。

    字段只用执行器自己掌握、且事后能从报告回读核对的值；**不**编造
    ``thread_id`` / ``checkpointer``——那两个由服务端图在 experiment 模式下
    生成（DAV-1477 P3），执行器编出来只会是假身份。核对只比对下发的这组
    字段（``experiment_id`` / ``pair_id`` / ``batch_index`` / ``arm`` /
    ``replicate`` / ``run_uuid``），与预注册 §3「harness 附加或关联而不覆盖
    图产生的 thread_id/checkpointer」一致。
    """
    return {
        "experiment_id": pair_id.split("-", 1)[0] if "-" in pair_id else pair_id,
        "pair_id": pair_id,
        "batch_index": int(batch_index),
        "arm": str(instance),
        "replicate": int(attempt),
        "run_uuid": run_uuid or uuid.uuid4().hex,
    }


def extract_run_identity(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从 job result 里取出服务端回写/承认的 ``experiment_run_identity``。

    取不到（None / 非 dict）返回 ``None``；只挑出执行器下发的那些键，避免
    服务端额外附加的字段（thread_id/checkpointer）把比对变成永假。
    """
    if not isinstance(result, dict):
        return None
    identity = result.get(RUN_IDENTITY_KEY)
    if not isinstance(identity, dict):
        return None
    return {
        "experiment_id": identity.get("experiment_id"),
        "pair_id": identity.get("pair_id"),
        "batch_index": identity.get("batch_index"),
        "arm": identity.get("arm"),
        "replicate": identity.get("replicate"),
        "run_uuid": identity.get("run_uuid"),
    }


def verify_run_identity(
    sent: Dict[str, Any],
    result: Optional[Dict[str, Any]],
    *,
    instance: str,
) -> Tuple[bool, Optional[str]]:
    """核对服务端是否真的承接了本次请求的身份。

    返回 ``(ok, reason)``：``ok`` 为 False 时 reason 说明是「没回读到身份」
    还是「回读到但对不上」——两者都不得当成通过。当前 O/N 服务端会静默丢弃
    未声明字段，因此默认口径（``require``）下**每一对都会判 failed**；这是
    有意的：宁可实验跑不出数据，也不能让一份没有身份的报告混进可比集。
    """
    echoed = extract_run_identity(result)
    if echoed is None:
        return False, (
            f"identity_not_echoed:{instance}"
        )
    mismatched = [k for k in sent if echoed.get(k) != sent.get(k)]
    if mismatched:
        return False, (
            f"identity_mismatch:{instance}:{','.join(sorted(mismatched))}"
        )
    return True, None


# ---------------------------------------------------------------------------
# DAV-2285-2：凭据前置 + 报告归属回读核对
# ---------------------------------------------------------------------------


def missing_instance_tokens(
    instances: Iterable[str],
    token_for: Callable[[str], Optional[str]],
) -> List[str]:
    """返回「配了实例但没配 token」的实例名列表。

    服务端 ``RequireUser`` 在拿不到 Authorization 时会退到本地默认账户
    （``get_or_create_default_user``），请求照样 200、报告照样落库，只是归属
    不是实验口径的固定账户。执行器必须在开跑前就挡住这种情况。
    """
    out: List[str] = []
    for name in instances:
        if not (token_for(name) or "").strip():
            out.append(name)
    return out


def extract_report_account(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """从 ``/v1/jobs/{id}/result`` 的响应里取报告归属的 ``user_id``。

    ``result`` 是 ``/result`` 端点的整个响应体。归属落在落库报告的
    ``user_id`` 上（``ReportDB.user_id``），而 job result 端点不回显它，
    所以优先从 ``result`` 顶层找，找不到再看 ``result_data`` 内的同名键；
    都取不到 → ``None``（调用方按「不可核对」处理，不得默认放行）。
    """
    if not isinstance(result, dict):
        return None
    for source in (result, result.get("result")):
        if isinstance(source, dict):
            value = source.get("user_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def verify_report_account(
    observed: Optional[str],
    expected: str,
    *,
    instance: str,
    job_id: Optional[str],
) -> Tuple[bool, Optional[str]]:
    """核对单份报告是否归属固定账户。取不到归属同样判不通过。"""
    if not observed:
        return False, f"{PREFLIGHT_ACCOUNT_UNAVAILABLE}:{instance}:{job_id or '-'}"
    if observed != expected:
        return False, f"{PREFLIGHT_ACCOUNT_MISMATCH}:{instance}:{job_id or '-'}:{observed}"
    return True, None


# ---------------------------------------------------------------------------
# DAV-2285-3：轮询遇连接中断 → 重新查询服务端终态
# ---------------------------------------------------------------------------


def _backoff_delay(attempt_index: int) -> float:
    """按尝试序号取退避秒数，超出表尾就用最后一档。"""
    table = POLL_RECONNECT_BACKOFF_SECONDS
    if attempt_index < 0:
        attempt_index = 0
    return float(table[min(attempt_index, len(table) - 1)])


def verify_pair(pair: PairSpec, per_instance: Dict[str, Dict[str, Any]]) -> Tuple[
    str, Optional[str], List[str], str
]:
    """核对一对。返回 ``(outcome, reason, 观测到的模型列表, error_bucket)``。

    ``outcome`` ∈ ``ok`` / ``voided`` / ``failed``：

    * ``ok``：每个实例都跑成、served model 齐全、对内一致且等于冻结值；
    * ``voided``：请求都成功了但核对不过（对内不一致 / 非冻结模型 / 缺失）；
    * ``failed``：有实例请求本身失败（含配额类）。

    ``error_bucket`` 是**结构化**的失败分类（``none`` / ``quota_or_rate_limit`` /
    ``server_error`` / ``client_error`` / ``timeout`` / ``error``）。停批判定读它，
    不读 ``reason`` 的字符串前缀——否则改动 reason 文案就会静默漏掉配额停批。
    """
    observed: List[str] = []
    request_failures: List[str] = []
    quota = False
    unverified: List[str] = []
    gates: List[str] = []
    for name in pair.instances:
        info = per_instance.get(name)
        if info is None:
            request_failures.append(f"{name}:missing")
            continue
        # DAV-2285：凭据 / 身份类门禁优先于一切模型核对。不配 token 会退到
        # 本地默认账户、身份没被服务端承接都属于「这台实例的这次运行不可采信」，
        # 补跑也不会变好 → 直接 failed，不得退回 voided 走补跑额度。
        gate_reason = info.get("gate_reason")
        if gate_reason:
            gates.append(f"{name}:{gate_reason}")
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

    # 凭据 / 身份门禁不通过：请求可能根本成功，只是这次运行不可采信。
    if gates:
        return "failed", f"guard_failed:{';'.join(gates)}", observed, "guard_failed"

    # 请求本身失败（超时、5xx、配额……）优先于核对结论：补跑也不会变好。
    if request_failures:
        kind = "quota_or_rate_limit" if quota else "request_failed"
        return "failed", f"{kind}:{';'.join(request_failures)}", observed, (
            "quota_or_rate_limit" if quota else _dominant_bucket(per_instance, pair.instances)
        )

    # 核对不通过：作废，给补跑 1 次机会。
    if unverified:
        return "voided", f"model_unverifiable:{';'.join(unverified)}", observed, "none"
    if len(observed) != 1:
        return "voided", f"model_mismatch_across_pair:{observed}", observed, "none"

    only = observed[0]
    if pair.expected_model is not None and only != pair.expected_model:
        return "voided", f"model_not_frozen:{only}!={pair.expected_model}", observed, "none"
    return "ok", None, observed, "none"


def _dominant_bucket(per_instance: Dict[str, Dict[str, Any]], instances) -> str:
    """取实例里第一个真实错误桶，供记账与停批判定使用。"""
    for name in instances:
        bucket = (per_instance.get(name) or {}).get("error_bucket") or "none"
        if bucket != "none":
            return bucket
    return "error"


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


def daily_batch_gate_open(now: datetime) -> bool:
    """「每日一批」闸：只在交易日 19:50 之后开闸，周末不开闸。

    总控 10-12 晚裁定把批与基准日绑定：**每个交易日 19:50 后最多跑一批**，
    一批只对应当天一个基准日 T。因此运行窗口必须落在静默窗口结束后。
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    else:
        now = now.astimezone(CN_TZ)
    if not is_trading_day(now.date().isoformat()):
        return False
    return now.time() >= DAILY_BATCH_AFTER


def seconds_until_daily_gate(now: datetime) -> float:
    """距今日开闸（19:50）的秒数；当天已开闸返回 0。"""
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    else:
        now = now.astimezone(CN_TZ)
    gate = now.replace(hour=DAILY_BATCH_AFTER.hour, minute=DAILY_BATCH_AFTER.minute,
                       second=0, microsecond=0)
    return max(0.0, (gate - now).total_seconds())


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
    # 每日一批闸：wait = 等到 19:50 再跑；stop = 不在窗口则直接停
    daily_gate_policy: str = "wait"


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
    # 两臂输入快照比对所用白名单的 sha256（总控 10-12 晚 §3-3）：事后据此判断
    # 某次结论用的是哪份白名单。为 None 表示未提供白名单（不剔除任何字段）。
    snapshot_whitelist_sha256: Optional[str] = None

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
        # 主干守卫：run() 开跑前必须先核过 HEAD；由 main() 用 verify_trunk_head 填。
        # None = 未配置守卫（离线/测试可直接调 run），非 None = 命中即拒绝运行。
        trunk_guard: Optional[Tuple[Optional[str], str]] = None,
        # DAV-2285-1：require = 回读核对身份，不通过即 failed（默认）；
        # off = 只下发不核对（供先跑通链路、尚未接身份回写的服务端）。
        run_identity_policy: str = "require",
        # DAV-2285-2：报告归属要核对的固定账户 user_id；None = 不核对归属
        # （此时仍强制要求每个实例配 token，见 ``assert_tokens_present``）。
        expect_account: Optional[str] = None,
        # DAV-2285-3：轮询遇连接中断时的重查次数上限。
        poll_reconnect_attempts: int = POLL_RECONNECT_MAX_ATTEMPTS,
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
        self.trunk_guard = trunk_guard
        self.run_identity_policy = run_identity_policy
        self.expect_account = expect_account
        self.poll_reconnect_attempts = int(poll_reconnect_attempts)
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
    def wait_for_daily_gate(self) -> bool:
        """每日一批闸。返回 True 表示因闸未开而停下（stop 策略）。

        已开闸返回 False；wait 策略下未开闸则睡到 19:50 再继续；周末/非交易日
        直接停（周末不跑）。
        """
        now = self.now()
        if daily_batch_gate_open(now):
            return False
        if not is_trading_day(now.astimezone(CN_TZ).date().isoformat()):
            # 周末/非交易日不跑（总控 10-12 晚裁定）
            self.ledger.stop_reason = STOP_WEEKEND
            self.ledger.stop_detail = f"{now.date().isoformat()} 非交易日，不跑"
            return True
        if self.config.daily_gate_policy == "stop":
            self.ledger.stop_reason = STOP_DAILY_GATE
            self.ledger.stop_detail = (
                f"未到每日开闸时间 {DAILY_BATCH_AFTER.isoformat()}（每个交易日 19:50 后最多一批）"
            )
            return True
        self.sleep(seconds_until_daily_gate(now))
        return False

    def wait_out_blackout(self) -> bool:
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
        attempt_no = self.ledger.pair_state(pair.pair_id).attempts + 1
        identity = build_run_identity(
            pair_id=pair.pair_id,
            batch_index=record.batch_index,
            instance=name,
            attempt=attempt_no,
        )
        body = {
            "symbol": pair.symbol,
            "trade_date": pair.trade_date,
            "horizons": list(pair.horizons),
            # DAV-2285-1：每次请求都带实验运行身份，服务端一旦支持即直接承接。
            RUN_IDENTITY_KEY: identity,
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
            "run_identity": identity,
            "run_identity_confirmed": False,
            "poll_reconnects": 0,
            "gate_reason": None,
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
        # DAV-2285-3：轮询遇连接中断不直接判失败——job 可能已在服务端跑完，
        # 只是这一次查询没送达。按退避重新查服务端终态，重查耗尽才记失败。
        poll_disconnects = 0
        job: Any = None
        status: Optional[str] = None
        while True:
            code, polled = self._call(
                transport, "GET", f"{base}/v1/jobs/{job_id}", headers, None, record
            )
            if code == 0:
                if poll_disconnects < self.poll_reconnect_attempts:
                    delay = _backoff_delay(poll_disconnects)
                    poll_disconnects += 1
                    info["poll_reconnects"] = poll_disconnects
                    info["last_poll_error"] = str(polled)
                    self.sleep(delay)
                    continue
                info["error"] = (
                    f"job {job_id} 轮询连接中断 {poll_disconnects} 次后仍拿不到服务端状态：{polled}"
                )
                info["error_bucket"] = classify_error(str(polled)) if polled else "connection"
                info["status"] = "status_unreachable"
                return info
            # 非 200（含 404/401/5xx）必须立刻按错误处理：否则 job.get("status")
            # 取不到终态，循环只会挂到 job_timeout（默认 7200s）才记 timeout，
            # 把一次瞬时 5xx 误记成超时，且停批四条件迟迟判不了。
            if code != 200 or not isinstance(polled, dict):
                detail = polled.get("detail") if isinstance(polled, dict) else polled
                info["error"] = str(detail if detail is not None else polled)
                # 200 但响应体不是 dict（代理层畸形响应）没有可归类的错误，
                # 显式记成 malformed_response，不能落到 "none"——否则这一对
                # 会被当成「请求成功但无法核对」而误判成作废。
                info["error_bucket"] = (
                    classify_error(info["error"], code)
                    if code != 200
                    else "malformed_response"
                )
                info["status"] = "status_failed"
                return info
            job = polled
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

        # DAV-2285-1：回读核对服务端是否真的承接了本次身份。当前 O/N 服务端会
        # 静默丢弃未声明字段 → 这里判不通过。默认 require（fail-closed）：宁可
        # 跑不出数据，也不能让一份没身份的报告进可比集。
        if self.run_identity_policy == "require":
            ok, reason = verify_run_identity(
                identity, payload_result, instance=name
            )
            info["run_identity_confirmed"] = ok
            if not ok:
                info["gate_reason"] = reason
                info["status"] = "run_identity_unconfirmed"
                return info

        # DAV-2285-2：回读报告归属，确认它确实落在固定账户名下。
        # 无论核不核对都把观测值记下来（关核对时也有审计价值）。
        observed_account = extract_report_account(result)
        info["report_account"] = observed_account
        if self.expect_account:
            ok, reason = verify_report_account(
                observed_account, self.expect_account,
                instance=name, job_id=job_id,
            )
            if not ok:
                info["gate_reason"] = reason
                info["status"] = "account_unverified"
                return info

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

        outcome, reason, observed, error_bucket = verify_pair(pair, per_instance)
        record.outcome = outcome
        record.void_reason = reason
        record.error_bucket = error_bucket
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
            if rec.error_bucket == "quota_or_rate_limit":
                return STOP_QUOTA, f"{rec.pair_id} {rec.void_reason}"
        # DAV-2285：凭据 / 身份门禁属于系统性失败（改配置或服务端变更才能解除），
        # 不是这一对的偶发波动。继续跑只会把每一对都烧成 failed。
        for rec in self._last_batch_records:
            if rec.error_bucket == "guard_failed":
                return STOP_RUN_GUARD, f"{rec.pair_id} {rec.void_reason}"
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

    # -- 凭据前置（DAV-2285-2）-------------------------------------------
    def missing_tokens(self) -> List[str]:
        """配了实例却没配 token 的实例名。``run()`` 开跑前必查。"""
        return missing_instance_tokens(self.instances.keys(), self.token_for)

    # -- 主循环 -------------------------------------------------------------
    def run(self, manifest: Manifest) -> RunReport:
        if self.trunk_guard is not None:
            code, detail = self.trunk_guard
            if code:
                raise TrunkGuardError(code, detail)
        # DAV-2285-2：没给某个实例传 --token 就拒绝启动，一个请求都不发。
        # 服务端拿不到 Authorization 时会退到本地默认账户，请求照样成功，
        # 但报告不属固定账户——这类串号必须在开跑前就挡住。
        missing = self.missing_tokens()
        if missing:
            raise TokenPreflightError(
                PREFLIGHT_TOKEN_MISSING,
                "以下实例未配置 --token，拒绝启动：" + ", ".join(sorted(missing))
                + "（不配 token 时服务端会退到本地默认账户，报告不属固定账户）",
            )
        report = RunReport(
            manifest=manifest.name,
            started_at=self.now().isoformat(timespec="seconds"),
            pairs_planned=len(manifest.pairs),
        )
        batches = manifest.ordered_batches()
        seen_base_days: set = set()
        for bi, batch in enumerate(batches):
            if self.ledger.stop_reason:
                break
            # 总控 10-12 晚裁定：每个交易日 19:50 后最多跑一批 → 一批一个基准日。
            base_days = {p.trade_date for p in batch}
            if len(base_days) > 1:
                raise ValueError(
                    f"一批只允许对应一个基准日，实际出现 {sorted(base_days)}"
                )
            base_day = next(iter(base_days)) if base_days else None
            if base_day is not None and base_day in seen_base_days:
                self.ledger.stop_reason = STOP_DUPLICATE_BASE_DAY
                self.ledger.stop_detail = f"基准日 {base_day} 已跑过一批，每日一批上限"
                break
            if base_day is not None:
                seen_base_days.add(base_day)
            if self.wait_for_daily_gate():
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
                if self.wait_out_blackout():
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
                # DAV-2285-4：补跑对「作废」与「请求本身失败」两类都生效。
                # 原先只挂在 voided 分支，--pair-retries 对已判 failed 的对完全
                # 不起作用（server_error / timeout / 身份归属门禁都不消耗那次
                # 机会）。配额这类停批条件下仍不补跑——重试无意义且白烧额度。
                # 另：原先这是单个 ``if``，--pair-retries > 1 时第二次补跑永远
                # 不发生（声明的次数是假的），改成按剩余额度循环。
                retryable = record.outcome == "failed" and record.error_bucket != (
                    "quota_or_rate_limit"
                )
                if record.outcome == "voided" or retryable:
                    while stop is None and (
                        self.ledger.pair_state(pair.pair_id).attempts
                        <= self.config.pair_retries
                    ):
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
                        # 补跑已转 ok 就不再消耗额度
                        if rerun.outcome == "ok":
                            break
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
        report.snapshot_whitelist_sha256 = self.ledger.snapshot_whitelist_sha256
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


def build_forward_manifest(
    specs: List[BatchSpec],
    *,
    instances: Sequence[str],
    expected_model: Optional[str],
) -> Manifest:
    """按前瞻口径把各批对名单汇成一份可续跑的清单。

    注意：前瞻取样是**每批执行前现取**，因此本函数只用于离线预览与核对；
    真正的批内名单以 ``plan``/执行时落盘的 ``batches[].pairs`` 为准。
    """
    pairs: List[PairSpec] = []
    batch_ids: List[List[str]] = []
    for spec in specs:
        batch_pairs, _ = fetch_batch_pairs(
            spec, instances=instances, expected_model=expected_model
        )
        pairs.extend(batch_pairs)
        batch_ids.append([p.pair_id for p in batch_pairs])
    return Manifest(
        name="forward-looking",
        pairs=tuple(pairs),
        batches=tuple(tuple(b) for b in batch_ids),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DAV-1882 配对原子实验执行器")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="按清单执行")
    run.add_argument("--manifest")
    run.add_argument("--batch-spec", help="前瞻取样口径 JSON（与 --manifest 二选一）")
    run.add_argument("--expected-model", required=True,
                     help="冻结身份：该批所有对的期望 message.model（必填，否则核对会失效）")
    run.add_argument("--sampler-seed-base", type=int, default=SAMPLER_SEED_BASE,
                     help=f"抽样种子基数，实际种子 = base + 批号（默认 {SAMPLER_SEED_BASE}）")
    run.add_argument("--instance", action="append", default=[],
                     help="实例名=基址，如 O=http://127.0.0.1:8021（可重复）")
    run.add_argument("--state-dir", required=True)
    run.add_argument("--token", action="append", default=[],
                     help="实例名=token（可重复；只落 state 文件，不回显）")
    run.add_argument("--total-request-cap", type=int, default=DEFAULT_TOTAL_REQUEST_CAP)
    run.add_argument("--batch-request-cap", type=int, default=None)
    run.add_argument("--fail-rate-threshold", type=float, default=DEFAULT_FAIL_RATE_THRESHOLD)
    run.add_argument("--pair-retries", type=int, default=DEFAULT_PAIR_RETRIES,
                     help="作废 / 请求失败的对各自允许的补跑次数（默认 1）")
    run.add_argument("--expect-account", default=None,
                     help="固定账户 user_id：每份报告回读 user_id 核对归属，"
                          "不符即该对作 failed（不配 --token 时服务端会退到本地"
                          "默认账户，这里就是防串号的那一道）")
    run.add_argument("--run-identity-policy", choices=["require", "off"],
                     default="require",
                     help="experiment_run_identity 回读核对：require=服务端未承接"
                          "即该对 failed（默认 fail-closed）；off=只下发不核对")
    run.add_argument("--poll-reconnect-attempts", type=int,
                     default=POLL_RECONNECT_MAX_ATTEMPTS,
                     help="轮询遇连接中断时重新查询服务端终态的次数上限"
                          f"（默认 {POLL_RECONNECT_MAX_ATTEMPTS}）")
    run.add_argument("--blackout-policy", choices=["wait", "stop"], default="wait")
    run.add_argument("--daily-gate-policy", choices=["wait", "stop"], default="wait",
                     help="每日一批闸（交易日 19:50 后）未开时的行为：wait=睡到开闸，"
                          "stop=原地停下、下次续跑接着走（默认 wait）")
    run.add_argument("--poll-interval", type=float, default=5.0)
    run.add_argument("--job-timeout", type=float, default=7200.0)
    run.add_argument("--snapshot-whitelist", default=None,
                     help="输入快照比对白名单文件（精确 JSON 路径，默认空=不剔除）")

    plan = sub.add_parser("plan", help="只按前瞻口径取样并落盘对名单，不发起任何分析")
    plan.add_argument("--batch-spec", required=True)
    plan.add_argument("--instance", action="append", default=[])
    plan.add_argument("--expected-model", required=True)
    plan.add_argument("--sampler-seed-base", type=int, default=SAMPLER_SEED_BASE)
    plan.add_argument("--out", required=True)

    st = sub.add_parser("status", help="只看当前记账与停批原因")
    st.add_argument("--state-dir", required=True)
    return p


_COMPARE_INPUT_SNAPSHOTS: Any = None


def _load_compare_input_snapshots() -> Any:
    """按**文件路径**加载同目录的 ``compare_input_snapshots``。

    直接以脚本方式运行时 ``sys.path[0]`` 是 ``scripts/phase2``——既没有仓库根，
    也没有 ``scripts/__init__.py``，任何 ``from scripts.phase2.x import y`` 都会
    ``ModuleNotFoundError``。改用绝对路径加载后既不依赖 cwd 也不依赖包结构，
    且整个进程只执行一次（缓存）。别名加 ``_dav1882_`` 前缀，避免撞上该模块
    自己 ``if __name__ == "__main__"`` 的命令行分支。
    """
    global _COMPARE_INPUT_SNAPSHOTS
    if _COMPARE_INPUT_SNAPSHOTS is None:
        import importlib.util

        path = Path(__file__).resolve().with_name("compare_input_snapshots.py")
        spec = importlib.util.spec_from_file_location("_dav1882_compare_input_snapshots", path)
        if spec is None or spec.loader is None:      # pragma: no cover - 安装损坏
            raise SystemExit(f"无法加载 {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COMPARE_INPUT_SNAPSHOTS = module
    return _COMPARE_INPUT_SNAPSHOTS


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

    if args.cmd == "plan":
        specs = load_batch_specs(Path(args.batch_spec), seed_base=args.sampler_seed_base)
        order = [n for n in _instances_from_args(args.instance)] or ["O", "N"]
        out_batches = []
        for spec in specs:
            pairs, sampler = fetch_batch_pairs(
                spec, instances=order, expected_model=args.expected_model
            )
            out_batches.append({
                "batch_index": spec.batch_index,
                "date": spec.date,
                "n": spec.n,
                "sampler_seed": spec.sampler_seed,
                "eligible_digest": sampler.get("eligible_digest"),
                "n_picked": sampler.get("n_picked"),
                "pairs": [asdict(p) for p in pairs],
            })
            print(f"[plan] 批{spec.batch_index} date={spec.date} seed={spec.sampler_seed} "
                  f"picked={len(pairs)} digest={str(sampler.get('eligible_digest'))[:12]}",
                  file=sys.stderr)
        Path(args.out).write_text(
            json.dumps({"batches": out_batches}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 0

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
    # DAV-2285-2：先做凭据前置（不取样、不发任何请求），漏传 --token 直接拒启。
    missing_tokens = missing_instance_tokens(instances.keys(), lambda n: tokens.get(n))
    if missing_tokens:
        detail = (
            "以下实例未配置 --token，拒绝启动：" + ", ".join(sorted(missing_tokens))
            + "（不配 token 时服务端 RequireUser 会退到本地默认账户，"
              "报告不属固定账户）"
        )
        print(f"[token-preflight] {PREFLIGHT_TOKEN_MISSING}: {detail}", file=sys.stderr)
        raise SystemExit(f"[{PREFLIGHT_TOKEN_MISSING}] 凭据前置拒绝执行：{detail}")
    trunk_guard = None
    if args.batch_spec:
        specs = load_batch_specs(Path(args.batch_spec), seed_base=args.sampler_seed_base)
        # 开跑前先核主干：不符即拒绝，不取样、不建 executor、不发任何请求。
        code, detail = verify_trunk_head(specs)
        if code:
            print(f"[trunk-guard] {code}: {detail}", file=sys.stderr)
            raise SystemExit(f"主干守卫拒绝执行：{detail}")
        trunk_guard = (code, detail)
        manifest = build_forward_manifest(
            specs,
            instances=[n for n in instances] or ["O", "N"],
            expected_model=args.expected_model,
        )
    elif args.manifest:
        manifest = Manifest.load(Path(args.manifest))
    else:
        raise SystemExit("必须给 --manifest 或 --batch-spec 之一")
    ledger = Ledger(state_dir)
    if getattr(args, "snapshot_whitelist", None):
        load_whitelist = _load_compare_input_snapshots().load_whitelist
        try:
            _, wl_sha = load_whitelist(args.snapshot_whitelist)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"--snapshot-whitelist 读取失败：{exc}") from exc
        ledger.snapshot_whitelist_sha256 = wl_sha
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
            daily_gate_policy=args.daily_gate_policy,
        ),
        trunk_guard=trunk_guard,
        run_identity_policy=getattr(args, "run_identity_policy", "require"),
        expect_account=getattr(args, "expect_account", None),
        poll_reconnect_attempts=getattr(args, "poll_reconnect_attempts",
                                        POLL_RECONNECT_MAX_ATTEMPTS),
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
