# falsification_conditions 产出契约诊断（先诊断定约，不施工）

**本卡只做契约诊断与方案建议，不改代码、不改门禁、不改数据、不建子卡。**
总工 2026-09-19 定案：先明确「claim-specific 证伪条件由哪个节点负责产出」与
「缺失应记 warning 还是门禁」，在此之前不施工、不自动补字段、不得用占位句或
decision_status 猜造历史内容。

## 已知事实（DAV-1089 诊断已核实，直接采用不必重查）

- 链路：`extract_structured_data`（`api/services/report_service.py:1043`，LLM 抽取，
  仅读 `final_trade_decision[:3000]` + `fundamentals_report[:1000]`）→
  `_apply_structured_report_fields`（`api/main.py:2675-2705`）→
  `aggregate_horizon_metadata`（`report_service.py:2113-2143`）→
  `db_report.falsification_conditions`（:1664）→ `ReportDB` 列（`api/database.py:459`）。
- 落库端零丢失：47 份 VALID 的 DB 列与 `result_data.falsification_conditions` 逐字一致。
- 缺口 31/47：30 份为 VALID+WAIT——`risk_manager.py:77-86` 用 50 字模板句覆盖
  `final_trade_decision`，抽取输入无料可抽；1 份为 LLM 抽取单发漏检（`2bb0ef3b`）。
- 图内没有任何节点自己产出 `falsification_conditions`——完全依赖事后 LLM 抽取。

## 需要诊断回答的问题

1. **语义归属**：`falsification_conditions` 在协议里应是 claim-specific（每个 INV-x
   一条可证伪条件）还是 report 级？对照 `claims` 结构与冻结协议给出依据。
2. **产出节点归属**：谁应负责产出——manager verdict 结构化字段？`decision_status`
   自带？还是仍走事后抽取但扩输入（`investment_plan`/`manager_verdict` 段）？
   给出各自对 PIT/前视、机读性、生成时点的影响。
3. **门禁定性**：缺失应记 `warning`（软信号）还是硬门禁（VALID 判资格驳回）？
   结合 VALID+WAIT 不入 H1b 的现状给出建议，不得自行拍板为施工。
4. **历史数据处理**：已落库 31 份缺字段的 VALID 报告怎么办——不回写的前提下，
   统计/回测口径如何标注（legacy 隔离还是剔除），给方案不实施。

## 硬边界

- 只读诊断：可查代码与生产库副本（`mode=ro`+`query_only` 或 `.backup()`），
  禁止 `verify_h1b_gates.py --db-path` 对生产库（有越界前例）。
- 不改代码、不写库、不改历史报告、不建实现子卡；产出为契约建议供总工拍板。
- 环境：`env -u PYTHONPATH .venv310/bin/python`（3.10.20），隔离 `DATABASE_URL`。

## 交付

契约诊断报告：①falsification_conditions 的语义与产出节点建议（含取舍依据）；
②warning/门禁定性建议；③历史 31 份的处理口径建议；④证据（文件:行、计数、
解释器 `-V`、只读证据）。完成后精确 mention `项目调度助手`（D-032）。
