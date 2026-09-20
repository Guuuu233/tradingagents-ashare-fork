# DAV-119 微任务：manual_calibration_gap 进入 collector ledger/provenance

## 精确基线

只读远端 target 主干 `codex/dav-4-p2a-trunk`，SHA：`f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`。

这是从 DAV-119 失败的长上下文 run 重新切出的窄任务。不要读取 DAV-119/DAV-118 历史，不复用旧 run，不把未合入的 DAV-135 分支当作基线或已交付代码。不得修改主干、配置、providers、模型绑定、API Key、个人设置或凭据。若再次 context-window 400，立即停止本 run，由项目主管改派另一执行者，不重放本任务。

## 唯一问题

当前 provider 可能在 `FundFlowText.fund_flow_evidence_meta.manual_calibration_gap` 中保留“新浪 App 无已验证公开 endpoint、仅作人工校准”的结构化非阻塞缺口；但 `tradingagents/graph/data_collector.py` 的统一 `data_failure_ledger` / `source_provenance` 传播不稳定，东财 evidence 非空时可能丢失该 gap，导致报告上下文无法审计该限制。

## 严格范围

只允许修改：

- `tradingagents/graph/data_collector.py`
- 一个已有资金流/collector 测试文件（优先现有 `tests/test_data_collector.py` 或仓库中实际覆盖该路径的测试文件）

不得修改 provider、analyst、report service、schema、前端或其他业务文件；不得改变 fallback 顺序、EM `r0_net`、THS `netamount`、新浪 Web `legacy_web_algorithm`、Sina App manual gap 的自动共识资格。

## 实现要求

1. 只有工具返回对象实际携带 `manual_calibration_gap` 时才传播；禁止从展示文本猜测或构造该 gap。
2. 将该结构化 gap 原样保留必要字段（例如 source/status/reason/retrieved_at/as_of 中实际存在的字段），同时写入 `market_data_context.source_provenance` 与统一 `data_failure_ledger`；标注为非阻塞人工校准缺口，不得伪装成 provider 成功或数据可用。
3. 不覆盖已有 EM typed failure、fallback 尝试链、最终来源或其他 ledger 条目；重复传播应去重，失败原因和实际日期字段不可被请求日期替换。
4. 若当前 collector 输入路径没有可访问的 metadata，保持现有语义并留下可查询的限制，不得凭普通字符串补造 evidence。
5. 补最小回归：东财 evidence 非空但带 `manual_calibration_gap` 时，ledger 和 provenance 均保留具体 reason/source；无 gap 的正常结果不新增该条目；重复调用不会重复追加。

## 验收

- 从上述精确 SHA 产生并推送新的远端 feature branch/SHA，不推送主干。
- 报告实际修改文件、远端 branch/SHA、精确测试结果、changed-module compileall、`git diff --check`。
- 用结构化断言证明 `manual_calibration_gap` 的 reason/source/status 没有被通用 gap 覆盖，且该条目明确为非阻塞人工校准缺口。
- `.venv310` 若不存在必须如实记录，不得用其他解释器冒充；新 SHA、独立复审和回归前不解锁 DAV-122 或 DAV-120/121/123/124/125。

## 交付边界

不得宣称 DAV-119 已完成；累计窗口、真实新算法多源可用性、全量回归、真实 provider smoke 和后续合入/部署均不在本微任务验收范围。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
