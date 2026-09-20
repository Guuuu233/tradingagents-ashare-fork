# DAV-129 实现微任务：保留 early typed-gap 的累计失败原因

## 精确基线

远端 target 分支：`agent/1/dav129-em-fallback`

基线 SHA：`28751944161ba3d74d31f49a09f964a0811553ba`

只读该精确 SHA 作为实现基线。不要读取 DAV-118/DAV-119 父任务历史，不要复用旧 run，不要修改主干、用户模型绑定、providers、API Key、个人设置或凭据。

## 唯一问题

代码审核已复现：`CnAkshareProvider.get_individual_fund_flow` 的部分 early typed-gap 返回路径虽然局部 `errors` 已累计东财 formatter failure，但传给 `build_provider_text` 的 `fund_flow_evidence_meta.reason/gap` 仍使用泛化原因，导致东财超范围/无效金额/日期过滤无记录等具体失败原因只出现在展示文本，不在结构化 evidence 中。

## 只做这个实现修复

1. 在 `tradingagents/dataflows/providers/cn_akshare_provider.py` 内统一构造 early-gap reason：必须合并当前累计 `errors`，并让 `fund_flow_evidence_meta["reason"]` 与 `["gap"]` 使用同一脱敏、可查询的原因。
2. 覆盖历史无新算法结果、THS 无记录/缺字段/金额无效等 early-gap 分支；全失败终态也保持累计错误。
3. 不改变 fallback 顺序、EM `r0_net`、THS `netamount`、新浪 legacy、App manual gap 资格，不修改 collector、累计逻辑或其他数据源。
4. 将已有回归测试基线/断言随实现分支保留或最小合入，使真实实现变化被测试验证；不要新增测试框架或无关测试。
5. 测试必须使用精确 checkout 中实际存在的路径，不能报告未执行的命令。

## 交付闸门

必须实际修改并推送新的远端 branch/SHA，报告：

- 实际改动文件；
- `.venv310` 定向测试精确结果；
- changed modules `compileall`；
- `git diff --check`；
- 脱敏 metadata 证据：`reason/gap` 同时包含东财 formatter failure 与后续 fallback 错误。

若再次 context-window 400，立即停止该 run，改派资深开发2，不要重放同一上下文。新 SHA 经远端 `git ls-remote target` 核验并通过只读复审前，不合入、不重启、不解锁后续阶段。

参考回归测试交付：`cc0f9a2e29615cc9833406868ed6f953dbbd2dd4` 仅证明测试能捕获缺陷，不能视为实现已修复；实现必须基于 `2875194` 产生新的 feature SHA。
