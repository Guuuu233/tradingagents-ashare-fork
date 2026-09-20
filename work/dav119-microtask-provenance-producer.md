# DAV-119 微任务：provider 失败尝试链结构化留痕

## 精确基线

远端 target 主干：`codex/dav-4-p2a-trunk`

SHA：`f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`

只读该精确 SHA；不要读取父任务历史，不复用旧 run，不改配置、providers、模型绑定、API Key、主干或凭据。

## 当前可复现阻塞

DAV-140 的临时组合验证已确认：真实 provider fallback 路径仍可能丢失以下结构化字段：

- `attempted_sources`
- `fallback_errors`
- `em_typed_gap`
- `final_source`

原因集中在 `CnAkshareProvider.get_individual_fund_flow`：局部 `errors` 已存在，但不同 early return（EM 成功、Sina legacy fallback 成功、THS 成功、全部失败）没有统一把尝试链写入最终 `FundFlowText.fund_flow_evidence_meta`。

## 只做这一件事

只允许修改：

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- 一个已有资金流 provider 测试文件

实现要求：

1. 为 `get_individual_fund_flow` 的所有终态统一生成结构化尝试链：按实际调用顺序记录来源、状态和脱敏失败原因；不得从展示文本反推。
2. EM 无效/超范围/formatter failure 时，把原始 typed gap metadata 保存到 `em_typed_gap`，继续适用 fallback；成功 fallback 不能覆盖或删除 EM 失败原因。
3. 每个返回对象的 metadata 写入真实 `final_source`：EM、Sina historical、THS instant，或明确的 unavailable gap；不得把 legacy Web 写成新算法成功。
4. `fallback_errors` 只保留可审计的来源/错误类型/结构化 reason，不写 token、cookie、URL 签名或其他凭据。
5. 不改变 EM `r0_net`、THS `netamount`、Sina legacy/App manual 的字段资格、算法组和方向 guard；不改 collector、累计窗口或报告层。
6. 补最小回归：至少覆盖“EM formatter failure → Sina/THS fallback 成功仍保留完整尝试链”和“全部失败保留 em_typed_gap/fallback_errors/final_source”。

## 验收

必须从上述 SHA 产生并推送新的非主干 branch/SHA，报告：

- 实际改动文件；
- `.venv310` 定向测试精确结果；
- changed module compileall；
- `git diff --check`；
- 脱敏 metadata 断言，证明尝试顺序、EM typed gap、fallback 错误和最终来源均可查询。

若再次 context-window 400，立即停止，不重放父任务；由项目主管改派资深开发2以外的短任务执行者。新 SHA 经远端核验、只读代码审核和 `.venv310` 回归前，不合入、不重启、不解锁 DAV-119 后续。

注意：本任务不修 DAV-140 的周末 fixture，也不处理 CLS；那两项另行处理，避免再次把多个问题塞进一个长 run。

参考入口：`tradingagents/dataflows/providers/cn_akshare_provider.py:1337-1550`。
苛刻边界：不要修改 `fund_flow_evidence.py`、collector、analyst 或用户配置。
