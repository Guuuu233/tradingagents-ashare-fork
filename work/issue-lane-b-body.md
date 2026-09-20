# 泳道 B（P1）：置信度/字段提取硬约束

**执行纪律：**
- 基线：`final-dav336` @ `b26d040`。开工前 `git fetch target && git checkout -B fix/ta-audit-b target/codex/dav-4-p2a-trunk`。
- 只改本泳道文件：VERDICT 解析模块（先 grep `VERDICT:` 定位）、`api/services/report_service.py`、新增测试。禁改 interface.py、data_collector.py、evidence_verifier.py、prompts/zh.py、api/main.py。
- 金标准已入仓 `tests/golden/audit_20260823/`，B 泳道回放夹具用三份 `_result_data.json`（DB 全量直导，含 trader_investment_plan 与 investment_debate_state.judge_decision 完整字段）。
- 环境铁律：宿主 `.venv310`，`env -u PYTHONPATH .venv310/bin/python -m pytest`。

## 事实基线（8-23 DB 直查定谳，勿再复核走样）
000333（report_id 3c09051e7e364d859dfbe5f1af7cc2c9）：reports 表 confidence/target_price 顶层为 null；终稿 VERDICT 块只有 direction+reason（无置信度）；但 `trader_investment_plan`（4548 字）有两处"置信度"文本（"**置信度：65**"、"不得超过70"）；`investment_debate_state.judge_decision`（2742 字）有"短线上涨概率预估为 **0.65**"（probability 语义）。600900→confidence 60 正常、600276→70 正常但 target_price=null（观望语义）。

## B1. 提取回退链（TDD）
失败测试 `tests/test_verdict_extraction.py`：
1. VERDICT JSON 含 confidence → 直接提取；
2. 缺 confidence → 回退 trader_investment_plan 正则（用 000333 真实 result_data 夹具，断言提取出 65）；
3. trader_plan 也缺 → 回退 debate_state.judge_decision 提取 probability（注意语义映射：judge 文的"上涨概率 0.65"→ `probability` 字段，**不得混入 confidence**）；
4. 全部缺失 → 报告标记 `extraction_warning` 字段而非静默 null。

实现回退链：VERDICT JSON → trader_plan 正则(confidence) → judge_decision 正则(probability) → warning。复用项目既有"置信度全格式正则穿透三层回退"模式。

生成端硬约束（治本）：终稿/交易员提示词要求 confidence 必填数值——改提示词前先 grep 确认模板位置，改动最小化并在 PR 单列说明。

target_price/stop_loss 同步纳入：HOLD 观望允许 null 但必须写 `extraction_note="观望不设目标价"`，区分"提取失败"与"语义为空"。

## B2. key_metrics 结构化丢字段修复
现象：6 次 `unknown structured fields ignored for key metric: evaluation`、4 次 `risk item: statement`。在 report_service.py 结构化解析白名单补 evaluation/statement 字段；失败测试先行（构造含该字段的输入，断言不再丢弃且正常落库）。

## 验收
1. 离线回放三轮 `_result_data.json`：600900→60、000333→65（trader_plan 回退成功）、600276→70+note；
2. `tests/test_verdict_extraction.py` + B2 测试全绿，宿主树既有测试不回归；
3. 分支推送远端回报精确 SHA；等 Hermes 精确 SHA 复审，禁止自行合主干。
