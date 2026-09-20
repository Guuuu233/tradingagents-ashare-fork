# 多空辩论与研究总监静态/数据流审计（只读）

基线 `1eb280b`。审计 bull/bear/research_manager：
- 实际轮次计数与状态累计；每轮是否收到对方最新观点、七分析师报告、完整历史；
- claim_id/responded_claim_ids/证据引用是否结构化且机器可检查；
- 是否存在模型重复空话仍可通过、只靠Prompt无后处理；
- Research Manager 是否能看到逐轮历史、七报告、数据 provenance/gaps，能否验证事实；
- 是否有确定性证据真实性检查、诡辩/虚构数字守卫、矛盾权衡与中立裁决；
- 状态持久化到 result_data 是否完整，前端是否展示。

输出 `work/audit-debate-manager-static.md`：文件:行号、现状、阻塞缺口、真实E2E必须采集的字段、建议机器评分器。0代码改动，提交文档 SHA。
