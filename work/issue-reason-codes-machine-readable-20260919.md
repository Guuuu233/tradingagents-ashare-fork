# reason_codes 机读化修复 — 待 is_fatal 契约卡合入后派工

**状态：已批准、已备待派。** 总工 2026-09-19 批准开卡，但**不得与 DAV-1088 并行施工**；
本卡改 `decision_status.py`，排序在 `is_fatal` 契约卡之后，避免同文件写集冲突。
诊断依据：DAV-1089（已验收，`in_review` 收口）。

## 总工约束（2026-09-19）

- **必须保留 `failed_checks` 的结构化详细信息，不能简单丢弃**——叙述性内容挪到合适字段
  （如 `failed_checks`/`human_reasons`），不是删除；
- **历史报告不回写**，旧数据上的统计口径标为 legacy；
- 不处理 `falsification_conditions`（另案，见下）与 `extraction_warning`（低优先，另记）。

## 诊断结论（DAV-1089 已核实，两个产出点）

**产出点 A（主因，污染 186/661 份，28%）**：`tradingagents/graph/game_theory_node.py:810`

```python
"reason_codes": [signals.get("dominant_strategy", "")[:30]],
```

`dominant_strategy` 是同文件 :557-577 硬编码中文整句（如「顺势进攻：主力资金净流入且筹码
集中度良好，多头占优，建议跟随主力做多」），截断后写入 TraceItem → `analyst_traces` 及
`short_term.analyst_traces` 镜像。修复方向：改固定枚举码（如 `game_theory:<direction_key>`），
策略句挪入已有的 `key_finding` 字段。

**产出点 B（次因，85 份，全部 ⊂ A）**：`tradingagents/agents/utils/decision_status.py:664`

```python
failed = [str(x) for x in (mv.get("failed_checks") or []) if x]
return abstain_status(reason_codes=["manager_consistency_hard_gate", *failed], ...)
```

`consistency_check_passed=False` 时把 `manager_verdict.failed_checks` 中文叙述句整体 splat
进 `reason_codes`。`failed_checks` 产自 `evidence_verifier.py:1759-2045` 一致性硬闸与
`research_manager.py:822-972` E-04 守卫 violations（经 `evidence_verifier.py:1981` 并入）。
修复方向：`reason_codes` 只保留机读码，`failed` 详细叙述挪至 `failed_checks`/`human_reasons`
等结构化字段（信息不丢，字段归位）。

## 白名单

`tradingagents/graph/game_theory_node.py`（reason_codes 产出点与 `key_finding`）、
`tradingagents/agents/utils/decision_status.py`（:664 区域 reason_codes 组装），
以及对应测试文件。**不得**改 `failed_checks` 的生产端（evidence_verifier/research_manager
的 violations 生成逻辑保持原样，只动其落点字段）。

## 前置依赖

1. `is_fatal` 契约卡合入且过同 SHA 复审后（同改 `decision_status.py`，须串行）。
2. 证据门同前：先 RED 后 GREEN、RT-FULL 候选与直接父零新增失败、同 SHA 只读复审
   （`代码审核员`，D-014）、实施与审查分离。
3. 环境/红线同 DAV-1088：`.venv310` + `env -u PYTHONPATH`、隔离 `DATABASE_URL`、
   生产库只读；不合入、不部署、不写生产库、不改历史报告。

## 完成后

精确 mention `项目调度助手` 报告候选 SHA 与证据包，由调度派同 SHA 复审。
本卡收口后统计口径切换：新 `reason_codes` 全机读，旧数据按 legacy 标注，不再混计。

---

## 另案登记（不在本卡）

- **`falsification_conditions` 缺口**：暂不施工、不自动补字段。待先明确「claim-specific
  证伪条件由哪个节点负责产出」与「缺失应记 warning 还是门禁」。VALID+WAIT 本不入 H1b，
  不得用占位句或 decision_status 猜造历史内容。
- **`extraction_warning` 落库标记**：低优先，Matcher + is_fatal + reason_codes 三卡
  收口后再单独处理。
