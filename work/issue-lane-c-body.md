# 泳道 C（P0）：核验器句式公平性 + 模板对称化

**执行纪律：**
- 基线：`final-dav336` @ `b26d040`。开工前 `git fetch target && git checkout -B fix/ta-audit-c target/codex/dav-4-p2a-trunk`。
- 只改本泳道文件：`tradingagents/agents/utils/evidence_verifier.py`、`tradingagents/prompts/zh.py`（仅两处 DEBATE_STATE 示例块）、新增测试/金标准文件。禁改 interface.py、data_collector.py、api/main.py、report_service.py。
- 金标准已入仓 `tests/golden/audit_20260823/`：三份 `_audit.json`（含全部 unsupported 判例）、`_result_data.json`（DB 全量直导）、`ta_symmetry.py`（注意：它读冻结字段，只作对照不作验收）。
- 环境铁律：宿主 `.venv310`，`env -u PYTHONPATH .venv310/bin/python -m pytest`。

## C1. 金标准测试集固化
1. 从三份 `_audit.json` 提取全部 unsupported 证据判例（证据原文+所在报告全文+期望判定），落 `tests/golden/evidence_sentences_20260823.json`。已知背景：被标 unsupported 的数字大多真实存在于七报告（85.99×29处、15.58×26处、79.79×14处、"310"×20处）——修复后应翻转为 verified；若个别判例数字确实不存在，归入负例子集，必须维持 unsupported。
2. 失败测试 `tests/test_evidence_verifier_fairness.py`：逐条跑现行 verifier，断言现状（正例 FAIL 复现问题）。

## C2. 匹配粒度升级（evidence_verifier.py:285-330）
设计约束——闸门强度只增不减：
1. 单行全命中 → 现行 verified 路径不变；
2. 单行未全命中 → 多行聚合模式：证据内每个数字独立搜索，**每个数字的命中行必须与证据句共享 ≥1 关键词**（防量比 1.4、分位 0.15 这类短数字在两万字里的巧合命中），先同报告内聚合再跨报告；全部数字合规命中 → verified（details 注明 `multi_line_match`）；任一数字无合规命中 → 维持 unsupported；
3. contradiction 检测原逻辑不动：同关键词数值差 >5% 仍 contradicted；
4. 负例集必须包含"数字在无关上下文巧合出现"用例且守住。

**新验收工具**：`tests/golden/audit_20260823/replay_verifier.py` —— 从三份 `_result_data.json` 提取 claims+七报告原文，调新版 verifier 重算每条 evidence 判定，统计 bull/bear verified 率差。基线：修复前 bear 67% vs bull 86%（差 19pt）。目标：率差收敛到 ±10pt 内，且金标准正例 ≥8/9 翻转、负例零翻转。

## C3. 模板示例对称化（一行级）
`tradingagents/prompts/zh.py` 两处 DEBATE_STATE 示例块：bull 版 `"resolved_claim_ids": ["INV-1"]` 改为 `[]`，与 bear 对齐（实测 block 0 vs block 1，已定位）。补测试断言两个模板块 resolved_claim_ids 结构一致。

## 回归红线
以下既有测试必须全绿（证明闸门未放松）：`test_evidence_citation_density.py`、`test_evidence_summary.py`、`test_fund_flow_evidence.py`、`test_research_manager_claim_evidence_coverage_gate.py`。另跑宿主树全量 pytest。

**验收标准：**
1. 金标准正例 ≥8/9 转 verified、负例全守住、四既有证据测试全绿；
2. replay_verifier.py 输出贴 PR（修复前后率差对比）；
3. 分支推送远端回报精确 SHA；等 Hermes 精确 SHA 复审，禁止自行合主干。
