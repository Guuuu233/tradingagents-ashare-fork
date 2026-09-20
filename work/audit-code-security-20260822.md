# 全面只读代码/安全审计任务

基线/线上 `1eb280b`。对照 `/Users/davidliu/Downloads/项目完成度评估.md` 与 `/Users/davidliu/Downloads/项目加强方案`，只读审计全仓，禁止改代码/配置。

输出需包括：
1. 方案每项（Prompt、3轮辩论、5→27行业、DataCollector、两阶段、RAG、历史案例、质量闸、外盘）当前代码证据、测试证据、线上报告证据、状态：完全/部分/未完成。
2. Bug：异常吞噬、伪completed、空值/active、日期/防前视、并发/缓存、失败台账、上下文持久化、前端状态。
3. 安全：认证授权/IDOR、SSRF、SQL注入、命令注入、路径遍历、秘密泄漏、CORS、JWT/验证码、文件上传、pickle/eval/exec、依赖风险。
4. 每项必须给文件:行号、可复现命令或测试。不得把旧报告当现状。
5. 输出 `work/audit-code-security-final.md`，只提交文档分支/SHA，0生产代码改动。
