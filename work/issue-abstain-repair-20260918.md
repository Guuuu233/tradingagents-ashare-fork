# ABSTAIN 窄修：去重排除与裁决生命周期冲突 + E-04 否定误报

当前用户询问高 ABSTAIN 如何处理。Hermes 已独立读取生产库并在生产精确代码上复现，授权本卡只修下述两个缺陷；不改产品阈值、不减功能。一个实现者串行处理，两件事分别 commit，最终候选一次同 SHA 审查。不要重放旧 H1b 类型修复历史。

## 已证实的缺陷 A
research_manager.apply_manager_double_count_guard() 在 680-696 / 795-809 将重复事件 claim 从 adopted_claim_ids 剥离，仅记 excluded_evidence 与 double_count_guard_audit。decision_status.evaluate_confirmation_state() 426-447 的 decided_cids 只用 adopted/partially_adopted/rejected，完全不读合法排除。因此已处理的重复 claim 被误报 unadjudicated_material_claims_adopt，status_from_manager_verdict 722-732 整单 ABSTAIN。
最小实测：两条同 event_id、全部 verified 且已采纳的 claim，去重前 CONFIRMED，去重后 INV-2 已 excluded 却 UNRESOLVED。本批 7 条该错误涉及的全部 flagged claim 都存在 double_count_guard 结构化排除。真实例 42882a59bf6244bdb39e72257072fa1f、ba9ff1e5747546948e3fd2df379564c8。
修复契约：合法且可核对的去重排除代表已裁决但不计额外贡献，不能再被当漏裁决；不得重新采纳、不得简单塞 rejected（会命中 rejected+adopt 闸）、不得信任任意 excluded_evidence 字符串或未绑定的外来 claim_id。真正漏裁决、PIT、contradicted、source_unavailable 仍拦。检查二次重入以及 focus/core 引用被折叠 claim 的交互。事件分组真实性若发现另一个缺陷，只报告不顺手扩大范围。

## 已证实的缺陷 B
research_manager.validate_manager_expectation_revision_consumption() 473-495 按词命中，否定/不确定句误报。最小实测“利好尚未充分定价。”“目前无法确认利好已定价。”“The benefit is not fully priced in.”全部 rejected；“利好已充分定价。”正确 rejected。真实报告 2d7f66ddf185495f9099ec488916116f 的 judge_decision 去除系统告警尾巴后，唯一命中是“但未充分定价低估值…避险虹吸效应”，仍被判成已定价断言。
修复契约：明确否定/不确定/明确不采纳的引述，不应当作已确证事实；不能全局包含“未/不”就放行，双重否定和同段另有肯定断言仍拒绝。对 priced_in=unknown 仍禁止肯定断言。其它财务/重复计票 guard 不放宽。

## 白名单与证据门
只允许 research_manager.py、agents/utils/decision_status.py 以及对应现有 tests（先发现路径）。需要新增 schema/依赖/改变证据门语义则停在设计说明，不擅自扩大。先 RED 再 GREEN。父版本与候选同 Python/同命令 RT-FULL 无筛选全量对照；只承认 0 新增失败，不能拿定向绿替代全量。保持宿主 .env 存在条件的隔离回归、但不泄露凭据/不允许联网。参考当前 tests/conftest 离线隔离，不沿用旧“固定 deselect 即绕过死锁”的失效口径。发布前另需正式部署和单条受控真实 smoke，非本卡权限。

## 红队验收场景（交第二双眼睛复核）
A1 同事件双 claim 正确折叠，排除项零额外贡献且不被漏裁决闸二次误伤。
A2 真漏裁决仍 ABSTAIN；伪造/无来源 excluded ID 不绕过。
A3 PIT/contradicted/source_unavailable/focus关联不放宽，首次与重入一致。
A4 两个独立事件不能被错误合并；旧字符串/None 排除项原样保留。
B1 中英文明确否定、不确定句不被判成肯定已定价（至少上述三句）。
B2 无证据肯定断言仍拒绝；否定+另一肯定并列、双重否定仍拒绝。
B3 引用对方观点明确驳回 vs 自己采纳区分；原有超预期/数字/重复加票测试不退化。
B4 真实报告离线重放，仅用于诊断，不将旧报告改成 VALID 或纳入新版本前向 cohort。

交付完整远端 branch/SHA、直接父、白名单 diff、逐场景命令/实测、全量基线对照、未合入未上线状态。

## 固定基线与边界
- 远端 origin=https://github.com/Guuuu233/tradingagents-ashare-fork.git，主线 codex/dav-4-p2a-trunk，已回读 10c4c0a3f59448de3bfbb73b1e87d1c470e392f1。生产 4b540b0c9b08d77a12ce7d08cfdf0095288cd350；只差部署证据文档。
- 解释器 /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python（实测 Python 3.10.20），env -u PYTHONPATH。所有测试 DATABASE_URL 指隔离临时库，不得碰生产写入。
- 生产只读库 /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db（SQLite mode=ro, PRAGMA query_only=ON）。
- 不调用真实模型/数据商，不追加分析、不改用户配置/模型/提示词正文/3:1轮次，不开 credit_weighting，不改历史报告，不合入、不部署、不停现有服务或批次。
- Hermes 复现脚本 /tmp/ta_abstain_repro_20260918.py，运行命令 env -u PYTHONPATH DATABASE_URL=sqlite:////tmp/isolated-abstain.db /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python /tmp/ta_abstain_repro_20260918.py；这是诊断脚本，不是发布门禁。脚本执行过、生产零写入。
- 交付后精确 mention 项目调度助手；调度不得自己 mention 自己。只推进已授权范围，不新开其他实现者争写同一文件。
