# DAV-866 后续代码审查卡（待 DAV-867 红队正式交付后创建）

## 审查对象

- 候选提交：`020d3e3b18147f5ea20e90d3878b20952d20fd97`
- 直接父提交：`8c69eab186bde58e49cbc134fb4da5f015c77e92`
- 主干祖先：`a227cdc3bb466edf2e910419cb6013cfc021d309`
- 远端分支：`origin/agent/1/14ab0be94894`
- 只允许审查以下三条路径：
  - `tradingagents/eval/v03_return_measure.py`
  - `scripts/run_v03_return_measure.py`
  - `tests/test_v03_return_measure.py`

## 指派规则

本卡只能指派给“代码审核员”（`c732eba5-bbdd-40ac-b2c6-e0be14c0d3be`）。不得指派独立代码审核员；不得由实施代理自审。

## 只读审查范围

1. 确认默认 `EvaluationStamp`、`SnapshotManifest`、`measure_dataset([])` 的 `running_service_sha` 均为显式 `offline_replay_gap`，不再冒充历史样本生成 SHA。
2. 确认显式传入运行服务 SHA、healthz 探针、探针不可达和 `--offline` 四条路径的 provenance 语义没有混淆。
3. 确认历史样本生成 SHA 仍保留为历史事实，不被删除、覆盖或改名。
4. 确认 DAV-865 的真实统计 fail-closed、25 字段审计表、7 个消融变体和生产库零写入契约未被放宽。
5. 对测试改动逐行核对：既有测试不得被删除、放宽或改成无法证明原契约；新增断言必须确实覆盖 DAV-866 修复。
6. 核对提交父子关系、三文件白名单、`git diff --check` 和候选 SHA 与代理报告一致。

## 证据门禁

- DAV-867 红队报告必须先正式落账并与本卡候选 SHA 一致。
- 审查必须对同一完整 SHA 进行，只读，不修代码、不提交、不合入、不部署、不重启服务。
- 审查结论必须明确写出 HIGH/MEDIUM/LOW 数量；任何未解决的 HIGH/MEDIUM 均不得合入。
- 只有“代码审核员”对同一 SHA 明确“准予合入”后，才进入合入前回归和受保护 FF；审查通过不自动代表部署授权。
