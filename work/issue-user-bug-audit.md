# BrokenPipe 与报告名称问题：独立只读审计

## 目标

对两个用户现象做只读代码/运行时审计，不修改代码：

1. 分析 `601398.SH` / `2026-08-14` 多次落库 `BrokenPipeError: [Errno 32] Broken pipe`；
2. 历史报告列表/详情 `name` 回退为代码。

## 固定证据

- target trunk：`codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- 服务 PID：`41593`，端口：`127.0.0.1:8000`
- 数据库：`data/tradingagents.db`
- BrokenPipe 失败报告：`c217b5b57ca24c1e8d0e0755968ae0b9`、`48a6aaf21bf040b39bc8ab56fe885dce`、`d76ccb9ab703487c98c6a590f2122dec`
- 服务 stdout/stderr 当前均为 pipe；没有持久日志文件。
- `/v1/reports` 列表使用 `_get_reverse_stock_map_cached_only()`；冷缓存返回空映射。
- 独立名称源探针曾因深交所 SSL EOF 失败，后续成功加载 `601398.SH → 工商银行`。

## 必须回答

- BrokenPipe 的具体抛出层和调用链；是否由后台服务 stdout/stderr pipe 关闭、SSE 断开、LLM 网关或子进程造成；证据不足处必须明确。
- 当前代码是否有“客户端断开→任务失败”的错误耦合；是否有“print 写入已关闭 pipe→整条分析失败”的风险。
- 名称缺失是缓存策略、服务实例状态、源接口失败、数据库字段缺失还是前端展示；分别给出证据。
- 给出最小修复建议和必测回归清单，不改代码、不改个人模型/provider/API Key。

## 交付

在 issue 评论发布事实→证据→结论→风险→建议，附精确文件/行号；0 tests（只读）、无 branch/SHA。最后使用：[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
