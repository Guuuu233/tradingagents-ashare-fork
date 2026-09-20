# 卡 4：今日投资（cn_investoday）降费调用审计（只审计，不降级）

背景与证据：`work/2026-09-19-global-indices-data-source-audit.md` §4.3（今日投资用量）与「卡 4」节。
派工边界：`work/2026-09-19-global-indices-dispatch-brief.md` §3.3、§6、§7。

**为什么不是直接删**：日志「零成功命中」只证明样本中没有成功命中，不能证明没有发起过尝试、
故障时不会作为兜底被触发、或 Tushare 有等价能力。全仓 28 份日志中今日投资成功命中实为
76 次（「命中 11 次」是只扫 3 份日志的作废结论）。

## 任务（只读审计，本卡不执行任何配置变更）

1. 统计 `realtime_data` / `fundamental_data` 链上今日投资的**调用尝试次数**与失败分类
   （非仅成功命中），确认它是否在其他源失败时实际承担过兜底。
2. 核对等价能力：`get_realtime_quotes` 与今日投资实时接口**不是** `daily`/`daily_basic`
   的等价替换（后者非实时快照），不得按等价处理。
3. 产出降级/移除建议与风险评估，写入 `work/` 下新报告文件。

## 绝对不能动的

- `news_data` 链中的今日投资：`interface.py:190` 的
  `_HISTORICAL_NEAR_WINDOW_NEWS_PROVIDER_ALLOWLIST` 把 `get_global_news` 历史路径
  **硬限定只允许 `cn_investoday`**，移除将直接造成历史报告缺口。
- `AUUSDO` 相关路径：12 次成功命中全靠今日投资。

## 环境铁律

- 解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，贴 `-V`。
- 只读生产库用 `file:...?immutable=1`（`mode=ro` 会因缺 `-wal/-shm` 报 unable to open (14)）。
  **不得对生产库开可写连接。**
- 不改代码、不改配置、不重启服务（服务当前停机，属已知状态）。

## 交回时请报告

调用尝试次数统计口径与结果、失败分类、兜底实证（有/无）、等价能力核对结论、降级建议与风险。
完成后**主动精确 mention `项目调度助手`** 推进收口评审。
