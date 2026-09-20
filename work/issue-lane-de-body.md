# 泳道 D+E（P2）：可观测性 + 密钥脱敏（同分支串行）

**执行纪律：**
- 基线：`final-dav336` @ `b26d040`。开工前 `git fetch target && git checkout -B fix/ta-audit-de target/codex/dav-4-p2a-trunk`。
- **D2 与 E1 都动 api/main.py，必须同分支内先做 D 再做 E，禁止开两个分支互相踩。**
- 只改本泳道文件：llm_call_logs 写入链路、api/main.py（/api/health 路由 + role-bindings 端点 :5614 附近）、前端如确需、新增测试。禁改 interface.py、data_collector.py、evidence_verifier.py、report_service.py、prompts/zh.py。
- 环境铁律：宿主 `.venv310`，`env -u PYTHONPATH .venv310/bin/python -m pytest`。

## D1. llm_call_logs 补 report_id（先做）
事实基线：全表 2962 条全部 NULL（2026-07-29 起，含辩论/总监/交易员/风控调用）。**只向前修复，历史行不回填。**

1. 失败测试：模拟一轮含辩论的短分析（可 mock LLM），断言新落库行的 report_id 非空。
2. 实现：在 graph 执行链注入 report_id 上下文（推荐 contextvar 或扩展现有 trace 字段，避免改每个 agent 的函数签名）。
3. 验收：本地 mock 跑一轮，断言新增行零 NULL；历史 2962 行原样不动。

## D2. /api/health 返回健康 JSON
现状：/api/health 被 SPA 兜底接住返回 HTML（/healthz 正常返回 JSON status+commit_sha）。
1. 失败测试：GET /api/health 断言 content-type=application/json 且含 status/commit_sha 字段。
2. 修复 api/main.py 路由注册顺序/SPA fallback 逻辑：/api/* 前缀不落入 SPA 兜底。
3. 验收：curl /api/health 得 JSON；curl / 返回 HTML 页面（SPA 不受影响）。

## E1. role-bindings 响应脱敏
现状：GET /v1/role-bindings/resolved 返回明文 api_key（main.py:5614 附近端点）。
0. **前置核查**：先查前端设置页代码是否依赖完整 key 回显——若依赖，改为"仅显示 masked（sk-***last4），编辑留空=不修改"，改动扩大到前端的要在 PR 单列说明。
1. 失败测试：构造含 key 的绑定，断言响应 JSON 中无任何完整 key 明文。
2. 序列化层统一 mask；确认其他引用该端点的调用方不受影响。
3. 验收：登录态 curl 该接口无明文 key；设置页保存/回显手工冒烟通过。

## 验收
1. D1/D2/E1 测试全绿 + 宿主树既有测试不回归；
2. 三项验收输出贴 PR（mock 分析新行零 NULL、/api/health JSON、masked 响应样例——样例里不得出现真实 key）；
3. 分支推送远端回报精确 SHA；等 Hermes 精确 SHA 复审，禁止自行合主干。
