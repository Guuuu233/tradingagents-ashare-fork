# DAV-165 精确 SHA 只读代码复审

## 目标

复审历史报告中文股票名称 fallback 修复的精确远端提交，不修改代码、不重写分支、不改用户模型/provider/API Key/数据库 schema。

## 被审对象

- target 仓库：`https://github.com/Guuuu233/1.git`
- 基线：`codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- 分支：`agent/2/485cbfc4`
- 精确 SHA：`32e86b9a09ac4de22877f5f252efa80de90dcaba`
- 允许关注文件：`api/main.py`、`tests/test_api_smoke.py`

## 复审要求

1. 在干净 checkout 核验 SHA、父提交、diff 范围；不得相信 issue 自报。
2. 检查报告列表和报告详情是否走同一套非阻塞名称解析；冷缓存、源失败、失败退避窗口、成功恢复分别验证；不能因报告接口请求而阻塞数十秒，也不能把失败空映射当成功缓存。
3. 检查旧报告是否仍可读，name 缺失时是否安全回退 symbol；成功拿到 `601398.SH → 工商银行` 或 fixture 名称后，列表/详情是否一致返回中文名。
4. 使用当前宿主机 `.venv310`，从精确 checkout 运行实际存在的定向测试（先发现路径，不得猜不存在的文件），至少覆盖名称缓存、API smoke、报告详情相关测试；运行 compileall 和 diff-check。
5. 输出 PASS/FAIL、可复现阻塞、测试命令与结果、精确 SHA。发现问题只评论，不修改代码；如 PASS，将 DAV-165 标为可进入集成门，但不合入主干、不重启服务。

## 交付格式

评论必须包含：审查 SHA、变更文件、测试结果、发现/未发现问题、未合入/未重启/未上线。仅在有真实审查结果时使用：[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

禁止修改个人配置和主干。
