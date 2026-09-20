# DAV-127 微任务：保留成功 fallback 的尝试链

## 精确基线

远端 target 分支：`agent/1/5dc25f22`

SHA：`ac975af51c424076f970909a7aee898ad2022cc3`

只读该精确 SHA 作为基线，不审旧版本，不读取 DAV-118 父任务历史，不修改主干、用户配置、providers、模型绑定、API Key 或凭据。

## 唯一阻塞

代码审核已确认：东财无效/超范围后，新浪或同花顺 fallback 成功时，最终 `FundFlowText` 只保留最终来源，丢失东财失败原因和完整尝试链。需要修复审计元数据传播。

## 要求

1. 初始化结构化 `attempted_sources` / `fallback_errors`（或项目已有等价字段），记录 EM 的状态与脱敏原因。
2. 新浪成功返回和同花顺成功返回时，把前序 EM gap、尝试链和最终来源合并进 `fund_flow_evidence_meta`；不能只拼展示文本。
3. 全部失败路径也使用同一结构化尝试链，保证 `data_failure_ledger`/provenance 有可查询证据。
4. 不改变 EM `r0_net`、THS `netamount`、新浪 legacy、App manual gap 的字段资格和方向守卫；不扩大本任务范围。
5. 增加最小回归断言：EM typed gap→Sina 成功、EM typed gap→THS 成功时，最终结果同时保留最终来源与 EM 失败原因/attempted_sources。

## 交付门槛

- 新远端 branch/SHA，必须基于上述精确 SHA；
- 实际改动文件清单；
- `.venv310` 定向测试精确结果；
- changed modules `compileall`；
- `git diff --check`；
- 脱敏的成功 fallback provenance 证据。

不得只回复分析，必须实际修改、测试并推送。若 `.venv310` 不存在如实报告，不得用系统 Python 冒充通过。新 SHA 和审核通过前不合入、不重启。
