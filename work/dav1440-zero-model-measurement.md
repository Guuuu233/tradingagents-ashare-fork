# DAV-1440 第 1 步：零模型、只读存量决策测量

## 授权与边界

总控 2026-10-03 派工仅授权第 1 步；交测量后回 blocked，等审计与 NEUTRAL / ABSTAIN 裁定。这里新增的两个 Python 文件只是一项审计工具及其测试，不被产品导入，不实现生产门控，不修改提示词或模型调用。不合入、不部署、不重启、不改生产库或个人配置。

完整报告级清单与冻结输入通过 DAV-1440 评论附件交付，不把报告原文、个人配置或生产数据库提交到 Git。以附件中的 `summary.json.snapshot_cutoff_utc` 为本次一致性快照边界；后续新增 completed 不自动属于该快照。

## 口径

- 活库以 `mode=ro` + `PRAGMA query_only=ON` + 一个 `BEGIN` 读事务取得一致快照，读取全部 `status='completed'`，不按创建日期或账户筛选；不使用绕过 WAL 的 `immutable=1`。
- 各档优先取 `short_term` / `medium_term` / `long_term`，仅在缺少对应主字段时取 `horizons`；两处投影不一致显式记录。单档保留 `horizon`，未知档位标记 `single_unspecified`。双档顶层聚合不是第三档，不重复计数。
- 本次是**落库最终决策的条件 what-if 重放**，不是重新运行历史模型、语义核验器、研究经理或供应商；不把当前主干其他既有规则引起的历史重分类混进 DAV-1440 增量。
- 命中条件严格为该档最终决策 `reason_codes` 中含 `no_adjudicated_support`，或研究经理的 `direction_basis.status == 'unledgered'` 且 `same_direction_claims == []`。
- 只有原最终决策为 `analysis_status=VALID` 且方向为 `BULL/BEAR` 的命中档进入待改判清单。已有中性、ABSTAIN、INVALID_RUN、PARTIAL 保持不变。
- 缺失 `direction_basis`、缺失/null 的 `same_direction_claims`、`unknown` 和 `partial_only` **不**推断为空账本。`partially_adopted_claims` 非空本身不构成豁免：同向依据为空的部分采纳仍属本卡条件。
- 原方向、有效 partial 账本来自落库字段；另外提取 `investment_plan`（回退 `judge_decision`）中的 `MANAGER_VERDICT` JSON 的原始 direction 与原始 partial。只导出这些字段，不导出报告正文。
- 每档同时列 `VALID/NEUTRAL/{WAIT或NO_TRADE}` 与 `ABSTAIN/N/A/{WAIT或NO_TRADE}` 两套候选供总控裁定；保留原 WAIT/NO_TRADE，原 BUY/SELL/HOLD 在候选中按 WAIT 处理。这只是保守 what-if，**不是已决定的最终状态/动作策略**。本项目 ABSTAIN 是 analysis_status，方向枚举是 N/A，而非名为 ABSTAIN 的 direction。
- null/旧格式报告均计入扫描库存；无明确规范决策字段者标为不可判定，不虚构 VALID 或新命中。全量扫描不等于证明所有旧报告均有可重放裁决账本。

## 复现

在精确 agent SHA checkout 下执行（解释器必须实际输出 `Python 3.10.20`）：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -V
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
  -m unittest discover -s work -p test_dav1440_zero_model_measurement.py -v
```

重新取只读活库快照：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
  work/dav1440_zero_model_measurement.py \
  --db /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db \
  --output <独立审计输出目录>
```

不依赖活库复核本次附件：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
  work/dav1440_zero_model_measurement.py \
  --frozen-input <附件解压目录>/frozen_inputs.json \
  --output <独立复核输出目录>
```

两次输出应逐字节相同。冻结输入为裁决最小字段白名单：无 Key、Cookie、provider 配置、用户上下文或全文；另含每份原 `result_data` 的 SHA256，仅用于确认输入版本，不可用于重建原文。

## 附件与验证边界

- `changes.csv` / `changes.json`：每份报告精确 id、档位、原方向、原 analysis_status、原动作、两套候选、命中条件、经理 direction 原文和 partial 列表。
- `inventory.json`：扫描各档、命中但不改判档、无规范状态历史档。
- `frozen_inputs.json`：可重复测量的白名单输入；`summary.json`：覆盖率、计数、时间、输入指纹。
- 附件审计 memo 与 SHA256SUMS：本轮实际结果、独立条件核对、文件校验。
- RT-FULL **未执行**：本阶段没有任何产品代码修改，不能以审计工具单测替代后续发布门禁。门控获授权修改后，按项目要求使用锁定解释器、隔离临时库、`-q -p no:randomly`；显式 deselect DAV-979 已知主干死锁用例，并判断相对目标主干有无新增失败。
- 后续仍需总控确定 NEUTRAL / ABSTAIN 与动作策略，授权门控实现；届时重取主干 tip 为父提交，同 SHA 复审、总控终签与 D-037 发布门。不得在 10-08 / 10-09 两个每日批监测期修改生产生成侧，最早 10-09 收盘后部署。
