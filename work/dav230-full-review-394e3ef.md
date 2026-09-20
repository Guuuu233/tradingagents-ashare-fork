# DAV-230 394e3ef完整独立质量门

## 对象
- SHA `394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 父 `6a8a896bfaa00e0a42d5ef3543aeb4b1d58e88d2`
- 变更：`api/services/report_service.py`、`tests/test_report_service_fund_flow.py`

## 强制步骤
1. checkout后必须cd到返回的workdir/1，确认HEAD。
2. 通读变更与所有fund-flow校验调用点。
3. 独立验证并必要时用临时脚本复现（禁止改代码）：
   - 1d只匹配selected_as_of；
   - 5d严格截至as_of最近5个不同有效日；
   - 窗口不足、重复同日、非法日期、非法window、非有限值应fail-closed；
   - direction使用选定窗口值。
4. 必须实际运行：
   - 新测试文件；
   - `test_fund_flow_evidence.py`、`test_smart_money_fund_flow_semantics.py`、`test_cn_akshare_backup_sources.py`、`test_sina_historical_fund_flow.py`；
   - `TUSHARE_TOKEN='' env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests -q`（在checkout目录运行）；
   - compileall、`git diff --check 6a8a896..394e3ef`。
5. 输出真实命令与结果。缺任一命令不得PASS。

只读，不改代码/提交/部署。