# P0：双周期全部失败不得伪 completed

**基线：`66cd7bf0238fae07d206d028915c7278d93dc466`。独立分支，不合主干。**

## 可复现缺陷

全面审计定位 `api/main.py` 双周期路径：short/medium 全部异常时，内部 result 标 partial/failed_horizons，但顶层 `_set_job` 仍可能无条件 `status="completed"`，前端收到伪成功。

## 契约

1. 双周期 all_failed（有效 horizon 结果=0）时：JobStore status=`failed`，ReportDB status=`failed`，error 非空且列出失败周期/原因；不得 completed/partial 伪成功。
2. 一成功一失败：顶层任务可 completed，但 result.mode=dual_horizon、horizon_status 精确记录 success/failed、failed_horizons 列表正确、data_gaps保留；不能把成功周期丢掉。
3. 两周期成功：保持 completed。
4. 同步、后台执行器、job查询、报告落库/list/detail 状态一致。
5. 不改 LLM/provider/user config，不吞原始 typed error，不打印敏感信息。
6. 回归覆盖：all fail / short only fail / medium only fail / all pass；模拟真实 graph exception，不只手写最终 result。

## 白名单
- `api/main.py`
- `api/services/report_service.py`（仅必要状态落库）
- `tests/test_dual_horizon_e2e.py`
- `tests/test_dual_horizon_bugs.py`
- 可新增单一生命周期测试文件

## 验收
`.venv310` 定向 pytest、compileall、diff-check；推精确SHA。禁止 @项目调度助手。
