## 固定审核对象

- 基线：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 远端分支：`agent/1/d39965d412f0`
- 组合 SHA：`50e115347b49bcb9e767c593296045a356099006`
- 只读审核，禁止修改代码、测试、配置、DB、主干或服务。

## 必审项

1. 远端 SHA、ancestry 和三提交线性链：`7babfc5 -> 2dc0262 -> 50e1153`，基线必须是 `23e09e5`。
2. 总范围必须恰好 5 文件：
   - `tradingagents/graph/data_collector.py`
   - `tests/test_data_collector.py`
   - `api/services/report_service.py`
   - `tests/test_verdict_extraction.py`
   - `tests/test_dav37_stage16_regressions.py`
3. `test_dav37` 只能改三条展示预期，无其他变化。
4. typed gap 和 probability note 语义分别保持 DAV-361、DAV-362 已审核契约；无 nested structured、无 probability 默认值、无配置变化。
5. 独立复跑宿主 `.venv310`：五文件定向矩阵；核验原始全量输出 `1852 passed, 1 skipped, 0 failed`、replay、diff-check、compileall。
6. 检查无 `.env`、API main、providers、DB schema、用户设置变化。
7. 给出 PASS/BLOCK、文件:行号、测试证据、0 code changes；明确未合入/未重启/未上线。

不要 mention 项目调度助手。