# DAV-245 精确SHA独立终审：统一组合树c956e43

## 对象

- 基线主干：`target/codex/dav-4-p2a-trunk@394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 统一分支：`target/integration/dav233-dav237-v2@c956e43db0fea1fea88b85103d10218734b2b1c8`
- 父链：`394e3ef → 825b0cc → be009f3 → c956e43`
- 预期文件仅：`api/main.py`、`api/services/report_service.py`、`tests/test_debate_state_persistence.py`、`tests/test_report_service_fund_flow.py`。

## 只读终审

1. checkout精确`c956e43`，确认远端SHA、父链、4文件范围、工作树干净。
2. 审查single-horizon累计逻辑：空debate不覆盖已有非空、真实非空可覆盖、普通字段不错误深合并；双horizon与invoke路径未改。
3. 审查资金窗口逻辑：窗口类型/date/future/重复/不足/方向fail-closed，以及`datetime.date`导入存在。
4. 运行import smoke。
5. 独立运行：
   - 9个DAV-244列明分文件测试；
   - `TUSHARE_TOKEN='' env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests -q`；
   - compileall；
   - `git diff --check 394e3ef..c956e43`。
6. 核对DAV-244历史四文件合跑挂起：单文件与全量已均通过，因此不得将其误判为代码失败；若终审自行复现超时则报告。
7. 输出PASS/打回，附命令、cwd、解释器、测试数、文件:行号。禁止改代码、合入、部署、运行真实LLM。

只有本终审PASS后才允许创建主干fast-forward卡。