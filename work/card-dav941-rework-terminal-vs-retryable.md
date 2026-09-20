主线 `5a0320f0618d`（DAV-941 合入结果）存在**阻断级缺陷**：所有持久化的 typed refusal 都被当作「永久终止」，从回填队列中永久排除，导致历史案例 T+1 学习闭环被破坏。**部署前必须返修。**

本卡为窄返修，只做一件事：**把「永久拒绝」与「可重试缺口」分开**。

## 缺陷位置

`tradingagents/knowledge/historical_cases.py:793-799`（`backfill_pending_cases` 内）：

```python
pending_cases = [
    case for case in pending_candidates
    if _refusal_from_claims(case.claims) is None
]
```

只要 claims 里存在任何 refusal 元数据（`_REFUSAL_METADATA_KEY`），该案例就被永久排除出回填队列，不区分 refusal 的性质。

## 为什么是阻断级

refusal 中至少有三类属于**本质临时**、将来必然可重试的状态，它们却被当成终态：

| code | 含义 | 是否可重试 |
|---|---|---|
| `future_eval_date` | 评估日晚于当前日期（T+1 尚未到来） | **必然可重试**，这是新建案例的常态 |
| `eval_date_not_closed` | 评估日为今日但尚未收盘 | **可重试**，当日收盘后即可 |
| `calendar_unavailable` | 交易日历暂时不可用（如离线、缓存未就绪） | **可重试** |

正常业务流程就是「今天生成报告 → T+1 之后回填收益」，因此几乎每条新案例落库时都会带上 `future_eval_date` 或 `calendar_unavailable` 的 refusal，随即被永久排除，**收益永远停留在 `【数据缺失】`**。

## 独立复现（运维已实测，`.venv310` / Python 3.10.20 / 隔离 sqlite）

```
① 落库案例: 成功
   actual_outcome = '【数据缺失】'
   持久化 refusal  = calendar_unavailable
② T+1 到期后再回填（as_of = 今日+30天）:
   {'total_scanned': 0, 'backfilled': 0, 'still_missing': 0, 'skipped_future': 0, 'errors': 0}
```

`total_scanned=0` 即该案例根本没有进入扫描队列。Codex 侧以 `future_eval_date` 路径独立复现了同一结果（`total_scanned=0`、`calculate_called=0`）。

## 修复要求

1. **区分 refusal 的终态性**：为 refusal 引入明确的「终态 / 可重试」语义（例如在 `_REFUSAL_METADATA_KEY` 元数据中增加 `terminal: bool`，或维护 code 白名单/黑名单）。`backfill_pending_cases` 只排除**终态** refusal，可重试的必须留在队列里。
2. `future_eval_date`、`eval_date_not_closed`、`calendar_unavailable` **必须**归类为可重试。请完整梳理 `historical_cases.py` 中所有产生 refusal 的分支，逐个给出归类及理由，不要只改这三个。
3. 保留本次合入的原意图：真正的终态拒绝（数据确定不会存在的情形）**不应**每次进程启动都重试。请说明你如何判定某个 code 属于终态。
4. **向后兼容**：已落库的历史数据中已经带有 refusal 元数据（无 `terminal` 字段）。必须说明这些既有记录如何处理——默认按可重试还是终态、是否需要迁移。**不得**让既有数据继续被永久排除。

## 验收标准（逐条给出实际输出）

- **AC-1**：复现上述场景，`total_scanned` 必须 > 0，案例能进入回填队列并在数据可用时正确回填。
- **AC-2**：`future_eval_date` / `eval_date_not_closed` / `calendar_unavailable` 三条路径各给出一个回归测试，证明它们在条件满足后能被重新扫描。
- **AC-3**：保留终态语义的测试——构造一个真正的终态 refusal，证明它不会被反复重试。
- **AC-4**：既有数据兼容性测试：构造一条「旧格式」（无 terminal 字段）的持久化 refusal 记录，证明其行为符合第 4 条的设计并说明理由。
- **AC-5**：**完整 RT-FULL 对照**（见下），失败集合相对基线零新增。

## 版本与回归（严格执行 D-012 §4b）

- 直接父必须等于交付当时的 `origin/codex/dav-4-p2a-trunk` tip，提交前 `git ls-remote` 复核。
- **必须跑完整 RT-FULL，不得用分文件或定向子集替代**（D-012 §4b，`DECISIONS.md:71`）。
- 基线参考（主线 `28d1adc6`）：`20 failed, 4777 passed, 1 skipped, 3 deselected in 1700.10s (28:20)`，20 项失败清单见 DAV-979 的「主干首次取得完整 RT-FULL 基线」评论。当前 tip `5a0320f` 的同口径结果为 `20 failed, 4801 passed`，失败集合与基线一致。
- ⚠️ 全量约 28 分钟，**不要设短看门狗**（低于 45 分钟一律不要设）。此前多次「挂死」判定均为看门狗误判。
- ⚠️ **不要用宽泛的 `pkill -f pytest`**，会误杀他人正在进行的门禁跑。
- 解释器必须是 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python` 配 `env -u PYTHONPATH`，报告贴 `-V`（须 `Python 3.10.20`）；`DATABASE_URL` 指向隔离临时库，**禁止**写 `data/tradingagents.db`。

## 白名单

`tradingagents/knowledge/historical_cases.py`、`tests/test_historical_cases.py`。如需扩大请先在卡内说明理由并等确认。

## 禁止项

不合入、不部署、不重启服务、不写生产库、不改个人配置。**禁止**通过放宽或删除既有断言来让测试变绿。写审分离：复审另派代码审核员。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
