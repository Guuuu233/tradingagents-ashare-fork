# V-03a-3：V-03 离线实验重放框架与数据快照协议

## 任务定位

这是 DAV-799 已冻结设计之后的**第一步实现卡**，不是收益结论卡，也不是生产回测或 H1b 门槛卡。

当前远端施工主干开工前必须重新读取：

```text
origin/codex/dav-4-p2a-trunk
```

本卡创建时实测基线为：

```text
a227cdc3bb466edf2e910419cb6013cfc021d309
```

若真正开工时远端主干已变化，停止，不在旧基线上继续写；以新的 trunk tip 重建第一父、白名单和全量基线。

本卡只实现**只读离线重放 Harness、快照清单、最小审计表和四类消融控制的契约层**。结果必须盖章为：

```text
半成品基线，非定性判断
```

不得把任何输出写成“AI 能否盈利”的结论、策略有效性证明或 H1b 资格证据。

## 设计输入（必须逐条遵守）

- `work/v03-freeze-sheet-20260909.md`
- DAV-799 的设计审定与冻结评论
- `work/card-v03a-rework.md` 的单账号、仅 completed 作用域约束
- 当前已有的 `tradingagents/eval/v03_return_measure.py`、`scripts/run_v03_return_measure.py`、`tests/test_v03_return_measure.py`

已冻结口径不得改义：T+1 Open、单边 5bps、佣金含交易规费且另计过户费和卖方印花税、沪深300基准、DEV/HISTORICAL_OOS/FORWARD_OOS 三段、typed-missing 不进收益分母但进入 coverage、六只典型标的只作 regression、蓝思缺口不得事后补 BUY。

## 实施范围

### 1. 只读数据快照与范围证明

在现有 V-03a 引擎上补齐一个可复核的快照 manifest / audit metadata：

- 明确 `target_user_id`、`status=completed`、总数 / completed / failed 计数；
- 记录生产库源路径、只读副本路径、副本文件 hash、快照生成时间、cutoff 与 requested/as-of 的区别；
- 记录完整代码 SHA、运行服务 SHA、模型、temperature、prompt hash、horizon profile、成本假设及系统完整度；
- 六只回归标的永久标记 `sample_role=regression`，不得进入 OOS 指标；缺少事件/行业/披露时间等必要分组字段时必须输出 typed gap，不能静默推断或回退；
- 生产库只允许通过 SQLite backup 复制后读取；不得调用 `ReportDB` / `HistoricalCaseDB` 写入口。

### 2. 最小 25 字段离线审计表

对现有测量记录补齐或严格校验以下字段，不得用另一套平行结果结构绕开原引擎：

`sample_id`、`symbol`、`cutoff_datetime`、`requested_as_of`、`profile_id`、`model_name`、`prompt_version`、`sample_role`、`evaluation_eligible`、`exclusion_reason`、`label_horizon`、`eval_offset_days`、`signal_date`、`executable_entry_date`、`actual_exit_date`、`roll_days_used`、`trade_action`、`entry_price`、`exit_price`、`cost_assumptions`、`gross_return_pct`、`net_return_pct`、`performance_category`、`wait_subsequent_return_pct`、`evidence_provenance`。

字段缺失、来源不明或语义冲突时必须显式标记缺口；不得填默认值、carry-forward、静默 drop 或由模型补齐。

### 3. 四类消融控制（只做 Harness/Mock 契约，不做真实大模型消融）

每个变体必须共享同一快照、cutoff、样本资格和成本假设；每次只改变一个控制变量，并在结果中记录变体名、控制值和输入快照 hash：

1. **复制 / Repetition**：`enable_evidence_deduplication=True/False`。测试重复同质事实与明确独立事实的区别；不得把关键词数量当独立票，也不得把独立事实误杀后宣称去重成功。
2. **顺序 / Order**：`Canonical/Reversed/Seeded-Shuffled`。固定 seed，证明顺序变化不会悄悄改变样本集合或时间切分；不能用未记录的随机状态。
3. **缺口 / Gap**：对 social、fund-flow、latest-report 等字段做显式 mask。缺失必须成为 typed gap，不能填 0、填默认值、carry-forward 或静默删除样本。
4. **命题 / Proposition**：`enable_claim_verification=True/False`。无命题输入时只能是 `not_checked` / typed gap；不得把 confidence 变成 probability，不得伪造挑战、解决或交易信号。

消融结果只说明机制抗扰动和数据利用边界，不得写成因果结论或盈利证明。

### 4. 时间泄漏与重叠标签框架

- 实现可测试的 purging / embargo 记录与资格判定；同一标的的重叠标签不得被当作独立样本；
- 事件日期、行业、披露日等必要字段缺失时输出不可解释的 typed gap，不得用当前日期、报告日期或字符串顺序代替；
- regression 样本、开发样本、calibration 样本与 OOS 样本必须在审计表中可区分；不得把回归集充作 OOS；
- 不能因为当前数据库没有纯 FORWARD_OOS 就伪造样本。FORWARD_OOS=0 时如实报告 0。

## 允许修改

优先只改以下既有路径；禁止为了绕开现有引擎另造一套平行收益计算路径：

- `tradingagents/eval/v03_return_measure.py`
- `scripts/run_v03_return_measure.py`
- `tests/test_v03_return_measure.py`
- 必要时新增同目录下仅负责快照/审计 schema 校验的测试文件；不得新增第三方依赖。

禁止修改：生产 API、数据库 schema/数据、`api/main.py`、provider 配置、role_bindings、模型/密钥、加权 flag、社交 active、历史报告、V-02 门槛逻辑及 DAV-808 已上线代码。

## 红队场景（实现交付必须逐条实跑）

| 编号 | 场景 | 预期 |
|---|---|---|
| RT-1 | 生产库与 SQLite 副本隔离 | 只读副本运行；生产库 hash/报告计数不因实验改变 |
| RT-2 | 多账号 + failed/pending/running 混库 | 只计目标账号且只计 completed；作用域计数可回读 |
| RT-3 | 25 字段审计表 | 每行字段齐全；缺失来源显式 typed gap，不伪造默认值 |
| RT-4 | 三段 OOS 边界 | DEV/HISTORICAL_OOS/FORWARD_OOS 边界正确，不能跨段泄漏 |
| RT-5 | 六只回归标的 | 永久 `sample_role=regression`，不进入 OOS 汇总 |
| RT-6 | typed-missing | return=NULL、进 coverage、不进 return 指标、不静默 drop/carry-forward |
| RT-7 | T+1 Open 与不可执行入场 | 停牌/涨跌停/缺价标 `untradable`，不得假装成交 |
| RT-8 | 成本与沪深300基准 | 成本项按冻结口径计算，基准收益与超额收益可追溯 |
| RT-9 | 无概率校准 / 无组合规则 | 不输出 confidence→probability、Sharpe 或最大回撤 |
| RT-10 | 复制消融 | 只改变去重开关；同质复制不增加独立票，独立事实不被误杀 |
| RT-11 | 顺序消融 | Canonical/Reversed/Seeded-Shuffled 共用同一快照与样本集合，seed 可回放 |
| RT-12 | 缺口消融 | mask 后出现 typed gap；不填默认、不 carry-forward、不静默删样本 |
| RT-13 | 命题消融 | 无命题时 `not_checked`；不由 confidence 推概率、不伪造 claim 状态 |
| RT-14 | 同事件/重叠标签 | purging/embargo 留审计证据；重叠样本不能当独立样本 |
| RT-15 | 变体同快照 | 各变体只改一个控制变量，cutoff、资格、成本、快照 hash 一致 |
| RT-16 | 当前没有 FORWARD_OOS | 如实输出 0 和原因，不把历史样本改名为 forward |
| RT-FULL | 全量回归 | 父 SHA 与候选 SHA 使用同一 Python 3.10 环境对照，失败集合不得新增 |

## 测试与交付门禁

必须使用：

```text
/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
```

并以 `env -u PYTHONPATH` 运行。至少交付：

1. V-03 定向测试及红队 RT-1~RT-16 的逐项实际输出；
2. SQLite 副本路径、hash、生产库前后计数和 `quick_check`；
3. 候选完整 40 位 SHA、直接父、远端分支、工作树状态、白名单 diff、`git diff --check`；
4. 父 SHA 与候选 SHA 的真实全量 `pytest -q -p no:randomly` 对照，保留完整失败集合；
5. 一份示例审计表/manifest，明确盖章“半成品基线，非定性判断”，并明确当前系统完整度与 FORWARD_OOS 样本数；
6. 交付状态置 `in_review`，不得在本卡自行合入、部署、重启、写生产库或发起真实报告。

候选交付后，先由不同角色复核 RT 覆盖面，再由**代码审核员**针对同一完整 SHA 只读审查；不得派给独立代码审核员，不得由实施者自审。审查、合入、部署仍是后续独立动作。
