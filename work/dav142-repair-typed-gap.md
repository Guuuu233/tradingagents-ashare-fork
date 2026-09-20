# DAV-142 精确返修：THS gap 显式失败与 Authorization/Bearer 脱敏

## 精确基线

远端分支：`agent/2/6d0c5f27`

SHA：`9a86ba92a1373629283bc0ee30b7b4c2c3fd9b28`

只基于该精确 SHA 返修；不要读取父任务历史，不复用旧 run，不复审旧 SHA `ebe301889a5e99a579bc6b3c937c67103c2f11a0`，不修改配置、providers、模型绑定、API Key、主干或凭据。

## 代码审核已复现的两个阻塞

1. `CnAkshareProvider.get_individual_fund_flow` 的 THS 行有净额但缺少可验证来源日期时，metadata 是 `final_source=unavailable_gap`、evidence 为空，但展示文本仍以“同花顺即时资金流净额快照”开头，没有 `【数据获取失败】`，下游只看文本可能误判为成功。
2. `em_typed_gap` 的递归字符串清洗未覆盖自由文本中的 `Authorization Bearer <secret>` 或裸 `Bearer <secret>`；嵌套 `detail` 可能把凭据原文写入最终 metadata。

## 只做这两个修复

只允许修改：

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tests/test_cn_akshare_backup_sources.py`

要求：

- 来源日期缺失的 THS unavailable gap 必须返回明确的 `【数据获取失败】` 文本，同时保持空 evidence、`as_of=None`、`final_source=unavailable_gap` 和原有字段语义；
- 统一递归 sanitizer 必须清除自由文本中的 `Authorization`、`Bearer` 及已有 cookie/token/key/api_key/signature 形式，禁止凭据原文进入 `em_typed_gap`、`fallback_errors` 或最终 metadata；
- 补最小回归：断言缺日期路径包含失败标记；嵌套自由文本中的多个敏感原值不会出现在序列化 metadata 中；
- 不改变 EM `r0_net`、THS `netamount`、Sina legacy/App manual、fallback 顺序、来源日期 guard 或其他算法语义；不修改 `fund_flow_evidence.py`、collector、analyst。

## 验收

必须从上述 SHA 推送新的非主干 branch/SHA，报告实际改动文件、定向测试、compileall、`git diff --check` 和脱敏结构化证据。

目标项目 `.venv310` 若在执行工作区不存在，必须明确记录，不得把系统 Python 结果写成 `.venv310` 通过；新 SHA 到达后由 Hermes 在宿主精确 checkout 用项目 `.venv310` 独立复跑。

新 SHA 经远端核验、精确只读复审和宿主 `.venv310` 回归前，不合入、不重启、不推进 DAV-140、不解锁 DAV-119 后续。若再次 context-window 400，停止本任务并切换执行者，不重放同一上下文。 

真实 EM/THS 可比性仍是独立质量门，本任务不得宣称已形成新算法共识。

义务：仅修上述两个审核阻塞。

参考入口：`tradingagents/dataflows/providers/cn_akshare_provider.py` 约 1380-1460、1760-1790；`tests/test_cn_akshare_backup_sources.py` 对应 THS 日期和 typed gap fixtures。