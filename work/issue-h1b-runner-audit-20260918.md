# H1b 批次执行器与样本统计只读审计（不跑真实分析）

当前问题：用户所贴19ABSTAIN+1VALID被当5%有效，Hermes正式代码实测当时20 completed→20v2→0D009eligible；唯一VALID cfd0ffe9116b47d79e684ffe181358b5 canonical trade_action=WAIT。全库817completed→155v2→9eligible；legacy cohort7，当前decision_model.v1:evidence_contract.v1:price_basis.unspecified cohort2。动态数字须注明截点。

只读任务：
1. 审计实际进程 /tmp/gen_h1b_detached.py：symbols×dates按symbol优先只截前130；日期含周末，already_done在日期归一化前检查且并发非原子，已出现相同规范股票/日期重复运行；DONE只表示_run_job返回并不表示VALID/eligible。核查去重与样本分布，不改现运行脚本、不kill、不暂停服务、不写报告状态。
2. 给一个冻结采样manifest与停发/排空方案，不执行：预先交易日归一化、stock/date/horizon/cohort/account去重、按行业/日期分层；不能按收益/胜者结果挑选样本，不能将历史补跑算前向，保留所有失败/弃权分母。避免把只含7只标的的130项叫跨60标的分散。
3. 只读复核日报 failed：UTC→上海日期分层，当前已有117条no_proxy fail-closed，不能都丢在ABSTAIN分母外后声称全库无异常；新runner已含目标地址，旧错误不能写成当前未修。
4. 核查新报告trade_action/risk_status数据库列与result_data/short_term落库差异，仅报字段链路证据和最小复现建议，不越权修。
5. 正式筛选请直接mode=ro读库并调用纯filter_v2_completed_reports/filter_reports_by_cohort；verify_h1b_gates.py有_ensure_report_schema可能写库，本卡不要直接对生产使用它。

只写审计报告与设计，不生产改动，不新开写同文件实现者。与研究经理修复独立，不阻塞其窄修。

## 固定基线与边界
- 远端 origin=https://github.com/Guuuu233/tradingagents-ashare-fork.git，主线 codex/dav-4-p2a-trunk，已回读 10c4c0a3f59448de3bfbb73b1e87d1c470e392f1。生产 4b540b0c9b08d77a12ce7d08cfdf0095288cd350；只差部署证据文档。
- 解释器 /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python（实测 Python 3.10.20），env -u PYTHONPATH。所有测试 DATABASE_URL 指隔离临时库，不得碰生产写入。
- 生产只读库 /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db（SQLite mode=ro, PRAGMA query_only=ON）。
- 不调用真实模型/数据商，不追加分析、不改用户配置/模型/提示词正文/3:1轮次，不开 credit_weighting，不改历史报告，不合入、不部署、不停现有服务或批次。
- Hermes 复现脚本 /tmp/ta_abstain_repro_20260918.py，运行命令 env -u PYTHONPATH DATABASE_URL=sqlite:////tmp/isolated-abstain.db /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python /tmp/ta_abstain_repro_20260918.py；这是诊断脚本，不是发布门禁。脚本执行过、生产零写入。
- 交付后精确 mention 项目调度助手；调度不得自己 mention 自己。只推进已授权范围，不新开其他实现者争写同一文件。
