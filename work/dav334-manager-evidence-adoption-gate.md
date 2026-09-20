# P0：研究总监 Claim 采纳证据覆盖率硬闸

**基线/线上：`79a552f4b28742983c01d3af91fa45bc17f191f0`。独立分支，不合主干。**

## 真实 E2E 证据

三份终局报告：京东方 `ef6c4b87`、宁德时代 `cda1abff`、招商银行 `0e01618b`。
- 唯一 contradicted：宁德时代 INV-4，已被拒绝（正确）。
- 但部分被采纳 claim 含 `verified + unsupported` 混合证据，研究总监仍标“证据充分”：
  - 京东方 INV-5
  - 宁德时代 INV-5/6/9
  - 招商银行 INV-1/9
这会让未核实细节随一个真值捆绑进入裁决。

## 契约

1. Evidence verification 按 claim 聚合：verified/unsupported/contradicted/source_unavailable 数量与 coverage ratio。
2. adopted claim 必须：0 contradicted、0 source_unavailable，且 verified coverage 达阈值；建议全部关键数值证据 verified，最低 coverage>=0.67且未验证项不参与裁决正文。
3. 若 claim 是混合证据：可采纳其 verified 子结论，但 manager_verdict 必须记录 `partially_adopted_claims`、`excluded_evidence`，正文只能引用 verified 内容，不能把整条标“证据充分”。
4. unsupported 全部或 coverage不足：必须 rejected/降权，不得 adopted。
5. manager_verdict 增加 `claim_evidence_summary`：每claim counts、coverage、decision=adopt/reject/partial、reason。
6. consistency gate：adopted_claim_ids 中出现 coverage不足或非verified关键证据时 fail-closed；LLM输出不能覆盖确定性判定。
7. Prompt注入按claim展示核验状态，要求总监明确标证据充分/部分支持/不支持/矛盾。
8. 不改变辩论轮数、方向guard、用户配置。

白名单：evidence_verifier.py、research_manager.py、agent_states.py、zh/en manager prompt、对应tests。
验收：混合证据partial；contradicted拒绝；全verified采纳；unsupported不采纳；结构化裁决与正文一致；`.venv310`核心+全量、compileall、diff-check。禁止@调度助手。
