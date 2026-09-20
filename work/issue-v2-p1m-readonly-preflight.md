# v2 Phase P1-M 只读架构/兼容预检

## 固定基线
`target/codex/dav-4-p2a-trunk@50e115347b49bcb9e767c593296045a356099006`

严格只读，禁止修改代码、测试、DB、配置、服务和用户设置；禁止运行全量测试。

## 任务

1. 追踪当前 state 类型、investment_debate_state、report result_data 序列化/反序列化、API response、历史报告抽屉数据入口。
2. 给出 P1-M 最小文件范围，判断能否完全避免 `api/main.py` 和数据库扩列。
3. 查明旧 result_data 字段缺失时当前读取行为，指出兼容落点。
4. 盘点现有可复用的 claim/evidence/七报告/字段完整性数据结构，避免指标计算器重复解析自由文本或猜字段。
5. 确认 feature flag 默认关闭时，哪些函数/测试能证明现网 6 条辩论行为零改变。
6. 对离线 A/B harness 给出不调用线上模型的最小入口和 fixture 方案。
7. 输出文件:行号、调用图、风险、推荐范围；0 code changes / 0 tests，未合入/未重启/未上线。

不得扩写到 P1-B、前端或 shadow credit 存储。不要 mention 项目调度助手。