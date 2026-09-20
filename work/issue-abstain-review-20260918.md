# ABSTAIN 两个已复现缺陷：只读复核根因与红队覆盖

这是与唯一实现者并行的只读分析，不是候选代码 PASS。不得改产品/测试文件、不替实现者修复。当前没有新候选 SHA，因此不作发布审查。

Hermes 在 /tmp/ta_abstain_repro_20260918.py 已复现：
1. 去重前全部 claim 有裁决且 CONFIRMED，research_manager double_count_guard 剥离重复 INV-2 到 excluded_evidence，confirmation decided_cids 不包含排除，错误生成 unadjudicated_material_claims_adopt:INV-2。
2. E-04 将“利好尚未充分定价。”“目前无法确认利好已定价。”“The benefit is not fully priced in.”全部误判为已定价肯定断言。真实报告 2d7f66ddf185495f9099ec488916116f 的唯一命中“但未充分定价…”。

请独立执行复现，审阅 work/issue-abstain-repair-20260918.md 红队 A1-A4/B1-B4 覆盖面，并特别检查：
- 去重排除不能变成不可信输入绕过裁决/PIT 闸的后门；正确处置是有来源的已裁决零额外贡献，不是把列表补满强行 VALID。
- 核验状态 verified 与投资裁决 adopted/rejected 是不同层；unadjudicated_material_claims_adopt 并不等于“经理采纳未核实 claim”，其 adopt 指确定性证据层。
- B1 只应修明确否定/不确定的误报；不要用 broad regex 让“未充分定价，但利好已充分定价”等真实断言漏过。
- 对真实批次7条 double_count 对应关系与 E-04真拦/误拦不要以偏概全；报告逐条核验范围。
- 复核本卡场景覆盖，结果给唯一实现者和调度助手，后续候选由代码审核员同 SHA 正式审查，不以本次替代。

只写本卡只读诊断证据，可存 work/abstain-readonly-coverage-20260918.md，0产品修改。

## 固定基线与边界
- 远端 origin=https://github.com/Guuuu233/tradingagents-ashare-fork.git，主线 codex/dav-4-p2a-trunk，已回读 10c4c0a3f59448de3bfbb73b1e87d1c470e392f1。生产 4b540b0c9b08d77a12ce7d08cfdf0095288cd350；只差部署证据文档。
- 解释器 /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python（实测 Python 3.10.20），env -u PYTHONPATH。所有测试 DATABASE_URL 指隔离临时库，不得碰生产写入。
- 生产只读库 /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db（SQLite mode=ro, PRAGMA query_only=ON）。
- 不调用真实模型/数据商，不追加分析、不改用户配置/模型/提示词正文/3:1轮次，不开 credit_weighting，不改历史报告，不合入、不部署、不停现有服务或批次。
- Hermes 复现脚本 /tmp/ta_abstain_repro_20260918.py，运行命令 env -u PYTHONPATH DATABASE_URL=sqlite:////tmp/isolated-abstain.db /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python /tmp/ta_abstain_repro_20260918.py；这是诊断脚本，不是发布门禁。脚本执行过、生产零写入。
- 交付后精确 mention 项目调度助手；调度不得自己 mention 自己。只推进已授权范围，不新开其他实现者争写同一文件。
