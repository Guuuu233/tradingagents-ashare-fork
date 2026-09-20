# DAV-862：V-03a-3 续施工——恢复既有测试并收口离线 Harness

## 任务定位

DAV-861 的第一次施工 run 因候选全量测试超过调度器后台等待上限而失败，现场已保存在 WIP 提交：

```text
WIP SHA: 7cd1a85
WIP parent: a227cdc3bb466edf2e910419cb6013cfc021d309
remote ref: origin/agent/1/dav861-wip
```

本卡从这个 WIP 接着修，不从 a227 重做。WIP 只代表未审查现场，不能合入、部署或作为收益结论。

## 第一硬门禁：不得删改弱既有测试

父版本 `a227cdc3bb466edf2e910419cb6013cfc021d309` 的
`tests/test_v03_return_measure.py` 有 17 个既有测试函数。必须把这 17 个函数及其原有断言恢复到候选中；函数体和原断言不得删除、改松、改名或用新测试替代。

新增 RT-1～RT-16 场景只能作为追加测试，使用不与既有函数冲突的新名字。允许对 fixture 做纯追加式扩展，但不得删除或改变原 fixture 数据和原有断言语义。交付前必须提供以下可复核证据：

- `git show a227...:tests/test_v03_return_measure.py` 与候选对照，父版本 17 个函数逐一仍存在；
- `git diff --unified=0` 中没有既有测试函数的删除、重命名或断言放宽；
- 旧测试 + 新 RT 测试均实际执行并通过/按父版本基线明确记录。

## 第二硬门禁：保留现有实现契约

保留 WIP 已完成的只读离线能力，并修复其测试暴露的问题：

- 生产库只通过 SQLite backup 复制到副本后读取；前后生产库 hash、计数、quick_check/integrity_check 必须一致；
- 只计目标账号、`status=completed`，cutoff 与 requested/as-of 分开记录；
- 25 字段审计行齐全；字段来源不明/缺失必须 typed gap，不填 0、默认值、carry-forward，不由模型补齐；
- DEV / HISTORICAL_OOS / FORWARD_OOS 三段边界正确；当前没有 FORWARD_OOS 时如实输出 0；六只回归标的永久 `sample_role=regression`，不进入 OOS；
- T+1 Open、不可执行入场、成本、沪深300基准、收益分母和 coverage 继续沿用冻结口径；
- 四类消融只使用 Mock/契约层：复制、顺序、缺口、命题；所有变体共享 snapshot hash、cutoff、资格和成本，只改一个控制变量；
- purging/embargo 必须留下审计证据；不得由 confidence 推 probability，不得增加 Sharpe/最大回撤或收益结论。

若修复需改代码，只允许继续修改：

```text
tradingagents/eval/v03_return_measure.py
scripts/run_v03_return_measure.py
tests/test_v03_return_measure.py
```

不得新增平行收益计算路径，不得修改 API、生产数据库 schema/数据、provider/role/model 配置、加权开关、社交 active、历史报告、V-02 门槛或 DAV-808 代码。

## 第三硬门禁：报告产物不进代码提交

`work/` 下的 SQLite 副本、`-shm/-wal`、JSON/Markdown 实验报告只用于本次证据，不得混入候选代码提交。交付时必须列出：

- 代码提交实际包含的文件；
- `git status --short` 与 `git diff --check`；
- 证据文件的路径、hash、生产库前后计数；
- 任何临时产物都留在工作目录，不进入提交白名单。

## 测试要求

使用固定解释器并清除 `PYTHONPATH`：

```text
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
```

至少实际执行：

1. 恢复后的全部 V-03 原测试 + RT-1～RT-16 定向测试；
2. `scripts/run_v03_return_measure.py` 的离线副本运行，确认无真实大模型、无生产写入；
3. 父 SHA `a227cdc...` 与候选 SHA 的同口径全量 `pytest -q -p no:randomly`。整套测试可能超过 10 分钟；如使用后台任务，设置 `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`，不得因默认 600 秒上限把未完成测试记为通过；
4. 对照失败集合：候选不得比父版本新增失败。若因现行生产库在 cutoff 之后增加报告导致计数变化，必须按 cutoff 口径记录，不得改测试硬编码来掩盖。

## 交付格式

交付必须是一个可复核的完整候选 SHA（40 位）及直接父 SHA、远端分支、白名单 diff、测试输出和 WIP 修复说明。不要在本卡自行合入、部署、重启或写生产库；交付状态置为 `in_review`。

交付后先做独立红队覆盖复核；复核通过后才创建只读代码审查卡，并且审查人必须是**代码审核员**，针对同一个完整 SHA，禁止派给独立代码审核员，禁止实施者自审。
