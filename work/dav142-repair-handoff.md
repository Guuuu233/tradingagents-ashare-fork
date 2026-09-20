[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc) [@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

## DAV-142 精确返修 handoff

只基于远端 `agent/1/32b5180c` @ `ebe301889a5e99a579bc6b3c937c67103c2f11a0` 返修；基线仍为 `codex/dav-4-p2a-trunk` @ `f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`。不要读取 DAV-118 父任务历史，不复用旧 SHA，不修改 target 主干、配置、providers、模型绑定、API Key、个人设置或凭据。

### 只修两个可授权文件

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tests/test_cn_akshare_backup_sources.py`

### 必须修复的两个阻塞

1. **THS 日期必须来自真实来源，禁止伪造 as-of**
   - 删除 THS 来源日期缺失时把 `curr_date` 写入 evidence/meta 的回填路径；无来源日期只能 typed unavailable/gap，`date`/`measurement_date`/`as_of` 保持不可验证状态，不能作为带有效日期的 final success。
   - 解析 THS 返回的真实来源日期；来源日期晚于分析截止日、今天或请求允许范围时，拒绝该记录并保留脱敏 typed failure，不能继续作为 final source。
   - 对未来分析日期 fail-closed；不得让 EM/THS 返回未来请求的正常 evidence。
   - 保持 THS `净额` 为 `netamount`，不得改写成 EM 的 `r0_net`。

2. **`em_typed_gap` 必须经过白名单/递归脱敏**
   - 不得把 formatter 的任意 metadata 原样复制到 `em_typed_gap` 或最终 `fund_flow_evidence_meta`；只保留可审计的结构化字段。
   - 对嵌套 reason/gap/url 等值统一脱敏，不能出现 cookie、token、key、api_key、Authorization、签名查询串或其原文值。

### 回归与交付

- 新增具体回归：THS 无来源日期不伪造 as-of；THS 未来来源日期被拒绝；未来分析日期 fail-closed；typed gap 嵌套敏感字段在 `em_typed_gap`、`fallback_errors`、最终 metadata 中均不可泄露。
- 先确认新增回归在旧实现上能复现失败，再在修复后运行通过。
- 使用目标工作区真实 `.venv310/bin/python` 运行精确测试（至少 `tests/test_cn_akshare_backup_sources.py`、`tests/test_sina_historical_fund_flow.py`、`tests/test_take_latest_ordering.py`）；若 `.venv310` 不存在，立即如实报告环境阻塞，不用系统 Python 冒充。
- 用同一 `.venv310` 执行 changed-module `compileall` 与 `git diff --check`。
- 从该精确基线产生并推送新的非主干 branch/SHA，交付改动文件、精确命令和结果、脱敏 metadata 断言；不要合入、重启或解锁 DAV-140/DAV-119 后续。

本轮只解决上述两个复审阻塞；其余问题不要扩 scope。
