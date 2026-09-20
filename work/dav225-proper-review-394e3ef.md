# DAV-225 精确SHA独立复审：394e3ef资金流窗口校验

## 背景

DAV-224首次审核无效：审核run在`multica repo checkout`后没有`cd`进入返回的`workdir/1`，`git`命令报`not a git repository`，未读取代码、未运行测试就直接打回。不得沿用该结论。

## 精确审查对象

- 仓库：`https://github.com/Guuuu233/1.git`
- SHA：`394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 父SHA：`6a8a896bfaa00e0a42d5ef3543aeb4b1d58e88d2`
- 变更：`api/services/report_service.py`、`tests/test_report_service_fund_flow.py`
- 生产原错误报告：`a4f8533f6f80479185833ae8622649bd`
- 修复后报告：`dd2d5cf259db490d946f18ed38704718`已completed，但辩论状态为空（DAV-221独立处理）。

## 强制执行步骤

1. `multica repo checkout ... --ref 394e3ef...`
2. 工具返回路径后，必须`cd <返回路径>`，执行`git rev-parse HEAD`确认精确SHA；不得在父workdir运行git。
3. 通读完整`api/services/report_service.py`、新增测试文件，并搜索所有`canonicalize_report_result_data`/fund-flow校验调用点。
4. 核验：
   - 1d是否严格匹配selected_as_of当天；
   - 5d是否截至selected_as_of选择最近5个不同有效日期；
   - 窗口不足是否应拒绝；
   - 重复同日多记录是否会重复求和；
   - 非ISO/无效日期是否仅靠字符串排序而绕过；
   - selected_as_of缺失、非法window、非有限值是否fail-closed；
   - direction是否按最终选定窗口值校验。
5. 独立运行：
   - `env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_report_service_fund_flow.py -q`（若checkout没有venv，用宿主`.venv310`明确记录解释器路径）
   - 资金流相关测试
   - `TUSHARE_TOKEN='' ... pytest tests -q`
   - compileall、git diff --check
6. 输出PASS或打回，必须包含真实命令结果、文件:行号与可复现测试。不能因任务描述写了某风险就直接复述为问题。

只读，不改代码、不提交、不部署、不重启。立即执行。