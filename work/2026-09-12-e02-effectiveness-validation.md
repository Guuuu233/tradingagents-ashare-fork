# 2-1 / E-02 生效验证（只读）

核验时间：2026-09-12（UTC+8）

## 基线

- detached worktree：`/private/tmp/ta-ff-dav830.gaecIY`
- exact trunk SHA：`41b77dc7a0871a849744b8013db3290700ccc883`
- 未修改代码、未提交、未 FF、未部署、未写库。

## 实测

```text
env -u PYTHONPATH \
  /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q \
  tests/test_evidence_duplicate_invariance.py \
  tests/test_evidence_relation_producer.py \
  tests/test_evidence_relations.py \
  tests/test_claim_cluster.py

155 passed in 0.88s
```

覆盖证据：

- `tests/test_evidence_duplicate_invariance.py`：同边重复幂等、关系顺序置换、SOURCE_REPETITION 正反边不改变结果；复制/派生环路、断链、孤立 claim、支持/反驳非折叠等 fail-closed 场景均通过。
- `tests/test_claim_cluster.py`：同一价格/成交量观测由不同 analyst speaker 提出时合并到同一 cluster；独立基本面观察形成不同 cluster；无可验证观测的叙事 claim 不计入。
- E-02 seam：显式关系图可用时才折叠；缺图保持 `pending` / `UNKNOWN` / contribution cap；非法、自动推断、矛盾关系均不产出有效贡献，并保留审计错误。

E-02 当前实现的关键边界是：贡献关系来自显式 E-01 relation graph，不从 claim 文本、speaker 名称或关键词自行推断。因此“语义改写”或“换 Agent”只有在上游仍产出同一事实关系时才应保持同一折叠结果；没有显式关系时，结果应继续是 pending，而不是被当成独立观察。这一点由 `claim_cluster.py` 的 seam 与上述测试共同证明。

与施工表旧措辞的 reconcile：表中曾写“独立观察 retain increment / 增量保留”。该表述已被 DAV-807 / DAV-806 在 2026-09-11 的后续裁定收紧并取代：未连接的新观察保留为 `pending` / `unknown`，在独立性契约明确前不计为新增独立贡献，沿用 reducer 的 0/1 全局上限。因此本报告不是按旧措辞声称“独立观察必然增加贡献”，而是按后续裁定验证保守语义。

## 结论

代码级 2-1 验证：**按后续裁定通过**。当前证据证明了复制、关系顺序/反向边、同观测跨 analyst 以及未连接观察保持 pending/unknown、贡献不超过 0/1 上限的保守折叠语义；没有发现需要改码的 seam 缺陷。旧施工表中“独立观察 retain increment”的字面要求不再作为当前验收标准。

证据边界：这只是离线 reducer / producer / consumer 测试，不是生产图实时 trace，也不是生产报告落库回读或真实样本效果证明。后两者仍属于 L2/L3 与部署/真实采集边界，不能由本报告扩大解释。

测试：155 passed；本项未写生产库。
