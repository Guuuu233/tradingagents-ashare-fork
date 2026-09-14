# P1-F 连板天梯真实上游探测记录

日期：2026-09-14（Australia/Perth）

## 探测边界

- 运行位置：`/private/tmp/ta-release-p1f-6cc4e-20260914` 隔离发布副本。
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，并以
  `env -u PYTHONPATH` 运行。
- 调用：直接实例化 `CnFuyaoProvider`，以中国当前日期 `2026-09-14` 调用
  `get_limit_up_ladder(curr_date="2026-09-14")`。
- 未调用真实分析入口、未启动 LangGraph、未写生产 SQLite、未生成报告、未启用社交或信用加权。

## 实际结果

调用在 provider 发 HTTP 请求之前以 `NotImplementedError` 结束：

```text
cn_fuyao 需要 API Key。请在配置中设置 fuyao_api_key 或环境变量 FUYAO_API_KEY。
```

原因是当前隔离发布环境没有可用的 `fuyao_api_key` 或 `FUYAO_API_KEY`。根据当前实现，
`_request_fuyao()` 先执行 `_require_api_key()`，所以本次请求计数为 0；没有把缺 key 误记为
上游返回空数据或服务失败。

## 结论边界

P1-F 的真实上游响应结构、近 30 日窗口和来源字段仍未得到线上实测证据；代码契约、模拟红队和
RT-FULL 证据仍然有效，但不能替代真实上游探测。若之后补齐受控 Fuyao key，应重新走一次只读
探测，并只记录结构化元数据，不触发真实分析或生产写入。

