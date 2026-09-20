# DAV-119 微任务：东财无效/超范围结果不得截断 fallback

## 固定基线

- 只读远端 `target` 分支 `agent/2/dav119-fallback-ledger`
- 基线 SHA：`69077988c4e3514a4cae7e77ade87e477ce0a4fb`
- 从该精确 SHA 创建新的远端实现分支和新 commit；不要复用旧 SHA，不要读取 DAV-118/DAV-119 历史评论。

## 唯一缺陷范围

只处理 `CnAkshareProvider.get_individual_fund_flow` 的东方财富路径：当请求日期超出东财可用范围、日期过滤后无行、金额字段缺失/全无效，formatter 产生普通非空失败文本时，调用链不得把它当成功，也不得在适用 fallback 之前结束。必须保留 typed failure/失败原因和最终实际来源；有可用 fallback 时继续尝试，全部失败时返回明确缺口。不要改共识算法、累计窗口、雪球、用户配置或其他数据源语义。

## 允许改动

- `tradingagents/dataflows/providers/cn_akshare_provider.py`：实际调用链所需的最小实现修改。
- 一个现有资金流 provider 测试文件：只补复现该缺陷的最小测试；不得新增依赖或新测试框架。

## 验收标准

1. 用 mock 复现东财日期超范围/无有效金额：普通失败字符串不能成为成功命中，适用 fallback 会被调用。
2. 测试断言最终结果的 evidence/status/来源或 typed gap 可区分“东财失败”和“fallback 成功/全部失败”，不能只断言非空。
3. 不改变 `r0_net`、`netamount`、legacy Web、Sina App manual gap 的字段和算法组语义。
4. 定向测试精确报告；对改动模块执行 `compileall`；执行 `git diff --check`。必须从当前远端基线产生并推送新 SHA。
5. 禁止修改用户模型绑定、providers 配置、API Key、个人设置、凭据、主干或 DAV-120 以后任务。

## 交付格式

只需回报：远端 branch/SHA、实际改动文件、定向测试结果、compileall/diff-check 结果、脱敏 fallback/typed failure 证据和未覆盖限制。若再次 context-window 400，停止重试该 run，由项目主管改派给 `资深开发2`，不要复用本任务上下文。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
