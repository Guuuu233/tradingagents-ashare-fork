# BrokenPipeError 分析失败：窄范围根因调查与修复

## 用户现象

分析 `601398.SH`、分析日期 `2026-08-14` 时，报告失败：

```text
BrokenPipeError: [Errno 32] Broken pipe
NoneType: None
```

数据库已确认最近至少 3 条同样失败记录：

- `c217b5b57ca24c1e8d0e0755968ae0b9`
- `48a6aaf21bf040b39bc8ab56fe885dce`
- `d76ccb9ab703487c98c6a590f2122dec`

## 固定基线

- target trunk：`codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- 当前服务 PID：`41593`
- 监听：`127.0.0.1:8000`
- 数据库：`data/tradingagents.db`
- 当前服务 stdout/stderr：均为 pipe；启动方式未保留日志文件
- `api/main.py` 流式入口：`/v1/chat/completions` → `_stream_job_events` → `InMemoryJobStore.subscribe`
- 图执行：`async for chunk in graph.graph.astream(...)`
- 相关底层输出：`tradingagents/dataflows/interface.py:_trace`、`openai_client.py`、`trading_graph.py` 使用 `print`

## 任务边界

只处理 BrokenPipe 根因，不修改用户个人模型绑定、providers、API Key、数据库 schema，不碰资金流业务逻辑，不合入主干。

## 必须完成

1. 读取并追踪完整 traceback 的实际来源；区分：
   - 服务 stdout/stderr pipe 被父进程关闭；
   - SSE 客户端断开导致后端误把客户端断开当分析失败；
   - LLM/子进程内部 BrokenPipe；
   - 其他网络/网关异常。
2. 建立最小可复现测试，先在当前基线证明失败或至少用等价关闭 stdout/SSE consumer 的测试复现。
3. 修复必须保留后台分析结果：浏览器/SSE 断开不得把已提交的分析误标为失败；真正 LLM/数据失败仍要落库并发出 `job.failed`。
4. 不允许用 `except BrokenPipeError: pass` 静默吞错；要有明确日志和终态语义。
5. 运行 `.venv310` 定向测试、compileall、`git diff --check`。
6. 推送远端新分支并返回完整 SHA、改动文件、测试结果、未合入/未重启事项。

## 交付协议

评论必须包含精确远端 branch/SHA、实际测试和根因结论；不能只报告“已修复”。最后仅在有新交付证据时使用：[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

禁止修改个人模型/provider/API Key/主干/服务。
