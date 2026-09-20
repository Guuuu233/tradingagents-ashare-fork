# DAV-864：V-03a 候选代码只读审查（代码审核员）

## 审查对象

只审查下面这个完整 SHA，不审分支 tip，不审 WIP，不审工作目录未提交产物：

```text
candidate: c36fbc525355a880835db8c43d65fc445b6bdc95
direct parent: 7cd1a8523e5f879712739576d7b6f101f2c1ff2c
trunk ancestor: a227cdc3bb466edf2e910419cb6013cfc021d309
remote: origin/agent/1/01a09942
red-team: DAV-863（已完成，建议进入本卡）
```

这是只读代码审查。不得改代码、不得补测试、不得提交、不得 FF、不得部署、不得重启、不得写生产库；发现问题只报告路径/行号、严重度和返修建议。审查人必须是**代码审核员**，不得派给独立代码审核员，也不得由实施者自审。

## 白名单范围

相对于基线 `a227cdc3bb466edf2e910419cb6013cfc021d309`，预期只有：

```text
scripts/run_v03_return_measure.py
tests/test_v03_return_measure.py
tradingagents/eval/v03_return_measure.py
```

必须确认：完整 SHA、直接父提交、远端回读、`git diff --name-status`、`git diff --check` 与工作目录状态；`work/` 下的 SQLite/JSON/Markdown/`-shm`/`-wal` 不能进入提交。

## 审查重点

1. V-03a 冻结协议：只读 SQLite backup、副本隔离、单账号与 `completed` 过滤、cutoff/requested_as_of、25 字段审计表、typed-missing、DEV/HISTORICAL_OOS/FORWARD_OOS、回归标的隔离、T+1 Open、成本和沪深300基准。
2. 禁止项：默认值或 carry-forward 把未知变成事实、静默 drop、由 confidence 推 probability、Sharpe/最大回撤/组合规则、平行收益路径、时间泄漏、生产写入或真实模型调用。
3. 测试完整性：父版本 17 个既有测试函数和断言不得删除、重命名、改松；RT-1 至 RT-16 必须是追加测试；已知 `test_rt_s4` 计数失败只能按父版本基线记录，不能借改硬编码掩盖。
4. 重点核对报告 provenance：
   - 候选提交后复跑的 `work/v03_snapshot_manifest.json` 已把 `code_sha` 记为 `c36fbc525355a880835db8c43d65fc445b6bdc95`；
   - 但 `running_service_sha` 仍由 `BASELINE_RUNNING_SERVICE_SHA` 填为 `a6d4540feaa8043ff36b0607a31c1d2d5f004149`；
   - 当前现场 `GET http://127.0.0.1:8000/healthz` 回读的是 `a227cdc3bb466edf2e910419cb6013cfc021d309`；
   - 请判定这是冻结字段的有意定义还是来源追溯错误，并给出 HIGH/MEDIUM/LOW 分级。不得绕过该差异直接写“证据闭环”。
5. 重点核对是否存在硬编码指标的伪造风险：`SnapshotManifest` 的默认账号计数、`measure_dataset` 的 `.get(..., 317/231/86)` 回退，以及任何在缺少真实来源时仍能生成貌似真实数值的路径。

## 测试证据

候选交付与红队复核已经记录：

- 定向文件：`32 passed, 1 failed`；唯一失败是父基线已有的 `test_rt_s4...`（当前 live 318 对旧硬编码 317）。
- 全量同口径报告：候选 `4284 passed, 19 failed, 1 skipped, 3 deselected`，声明与父版本 19 项失败集合一致；请按需要抽查并明确哪些是本人实跑、哪些是交付证据。
- 候选提交后离线复跑：生产库 SHA 前后均为 `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`，副本 SHA `d5ecd2137fc049062aa43b1d1f5e59b467ca9cea8b7a1680b65f289e3875cc3d`，无生产写入。

## 审查交付格式

只发布一条汇总报告，必须包含：

- 关联 issue、候选完整 SHA、直接父 SHA、审查范围；
- 精确测试命令和结果；
- 每个 HIGH/MEDIUM/LOW 发现的路径/行号、影响与建议；
- 对 DAV-862 和 DAV-863 验收项逐条 ✅/❌；
- 总体评级：通过 / 有条件通过 / 建议打回；
- 明确写“建议准予合入 / 有条件合入 / 打回返修”，但不得写准予部署、不得代替总工签字、不得执行 FF 或部署。
