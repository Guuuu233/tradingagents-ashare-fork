## 精简返修任务（不要加载父任务历史）

请直接在现有工作分支 `agent/1/f365b88c` 上从 `4570d1d9e5dc56ebcad56d2551d1964e0fae7c72` 继续修复，仅处理以下两个问题：

### 1) 手动 trigger 失败 job 持久化
文件：`api/main.py`

当 `_run_manual_trigger()` 二次调用 `_resolve_scheduled_trade_date()` 抛出异常时，入口之前已经创建了 `pending` job。请在异常分支：
- 调用 `_set_job(job_id, status="failed", error=..., finished_at=...)`；
- 发出 `job.failed` 终态事件；
- 保留 job 供 `GET /v1/jobs/{id}` 查询；
- 不改变显式日期成功路径和已有 scheduled-service failed 记录。

### 2) Fuyao 涨停池统一日期元数据
文件：`tradingagents/dataflows/providers/cn_fuyao_provider.py`

`get_zt_pool(date)` 无论请求日期直取成功还是回退成功，都必须输出统一的：
- `【请求日期】YYYY-MM-DD`
- `【实际数据日期】YYYY-MM-DD`
- 必要时输出 `【回退尝试】...`

直取成功时实际日期就是请求日期；不得只在 fallback 分支输出元数据。

### 交付要求
- 只改上述行为及对应测试；不要改模型、provider 配置、API Key、数据库或主干。
- 补 2 组回归测试：手动 trigger 二次日历失败后 job 为 failed；Fuyao 直取成功包含请求/实际日期。
- 运行对应定向测试；如环境允许再运行全量。
- 在同一分支提交并推送新 commit 到 `agent/1/f365b88c`。
- 最终只回报：commit、远端 HEAD、定向测试统计、全量测试统计（若运行）。
- 不要读取或展开 DAV-105/DAV-114/DAV-115 的全部历史上下文；这是精简执行单。
