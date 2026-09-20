# DAV-119 微任务：只修 EM fallback 截断

## 基线

远端 target 分支：`agent/2/dav119-fallback-ledger`

基线 SHA：`69077988c4e3514a4cae7e77ade87e477ce0a4fb`

禁止读取 DAV-118 父任务历史，禁止复用旧 run，禁止修改主干、用户配置、providers、模型绑定、API Key 或凭据。

## 只做一个问题

在 `tradingagents/dataflows/providers/cn_akshare_provider.py` 中修复：东财结果在日期截断后无可用结构化 evidence 时，不能用普通非空字符串结束流程并截断新浪/同花顺 fallback。

要求：

1. 只有日期/范围有效且包含非空结构化 fund-flow evidence 时，东财才算成功并允许结束 fallback。
2. 东财无效、空、超范围、非结构化结果必须转为 typed failure/可识别失败状态，记录失败原因，继续已有适用 fallback。
3. 不改变 EM `r0_net`、THS `netamount`、新浪 Web legacy、App manual gap 的字段资格规则。
4. 保留最终来源和尝试链；不在本微任务修改累计窗口逻辑、collector 传播或其他文件，除非为这个单一 fallback bug 必须的最小调用点。
5. 只补/运行该路径所需最小测试。

## 交付

必须推送新的远端 branch/SHA，并报告：实际文件、精确测试结果、changed modules compileall、git diff --check、脱敏 fallback 尝试链。`.venv310` 若不存在如实说明，不得用其他 Python 冒充。

不要只回复分析；必须实际修改、测试并推送 commit。若环境或模型错误阻塞，明确记录，不要重复重放同一任务。
