# DAV-164 精确 SHA 只读代码复审

## 目标

复审 BrokenPipe 修复的精确远端提交，不修改代码、不重写分支、不改用户模型/provider/API Key/数据库 schema。

## 被审对象

- target 仓库：`https://github.com/Guuuu233/1.git`
- 基线：`codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- 分支：`agent/1/e838fd66`
- 精确 SHA：`d3b0610911315464d86a490051ea4228a590ee13`
- 允许关注文件：`tradingagents/dataflows/interface.py`、`tradingagents/llm_clients/openai_client.py`、`tradingagents/graph/trading_graph.py`、`tests/test_broken_pipe_resilience.py`

## 复审要求

1. 在干净 checkout 核验 SHA、父提交、diff 范围；不得相信 issue 自报。
2. 检查所有分析路径裸 `print` 是否被正确替换，是否引入 logger 配置/导入错误；确认没有只修测试、不修实际调用路径。
3. 检查 BrokenPipe 的最小复现：关闭 stdout/stderr 时日志不会把任务误标失败；SSE consumer 断开时后台 job 仍按真实结果落库；真正业务异常仍保持 failed。
4. 使用当前宿主机 `.venv310`，从精确 checkout 运行实际存在的定向测试（先发现路径，不得猜不存在的文件），至少覆盖新增测试与 job lifecycle/job store 相关测试；运行 compileall 和 diff-check。
5. 输出 PASS/FAIL、可复现阻塞、测试命令与结果、精确 SHA。发现问题只评论，不修改代码；如 PASS，将 DAV-164 标为可进入集成门，但不合入主干、不重启服务。

## 交付格式

评论必须包含：审查 SHA、变更文件、测试结果、发现/未发现问题、未合入/未重启/未上线。仅在有真实审查结果时使用：[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

禁止修改个人配置和主干。
