## Cursor 冻结 DAV-600 设计（吸收 Conditional Pass）

对照 `e10b106df9d3173258b0a3fefc90ba7f3559f109`。

**准予按修正后的规格进入实现，但实现另卡 DAV-601，且不得与 DAV-595 抢 `evidence_verifier.py`。**

冻结点：
- cohort key = 三元组，SHA 只做溯源摘要
- 旧 121 永久 `legacy_unversioned`，禁止回填 v1
- 未传 `--cohort` fail-closed；空 cohort 非 PASS
- 线上未标记样本平权 1.0，不中断主链路
- 暂停 H1b 补样本；不开加权；不部署

规格文件已更新：`work/2026-09-04-decision-model-version-isolation-design.md`
