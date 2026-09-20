# P2-T12：报告 / ledger 映射 / 下游闸 / 只读 status API

## 目标

把社交证据接到报告持久化与下游确定性裁决：`merge_data_gaps` 扫描 social ledger；research_manager / evidence_verifier / report_quality_gate 尊重 `direction_allowed` 与不足态；新增认证只读 `GET /v1/social-data/status`（无帖子正文）。

本卡**不**做 Task 13 采集脚本、不删 `legacy_proxy`（Gate 4）、不改辩论轮次、不部署。默认 `TA_SOCIAL_MODE` 仍 disabled。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `68ae241bdf9c148654f551fb67b7e5f2ec56dba4`
- **新建**隔离分支，例如 `agent/dev2/p2-t12-social-report-gates`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 12 + §5.5 ledger→data_gaps + §八 SocialDataContext
- D-009 / D-010
- T9 已把 `social_data_context` 写入 state/horizon；T11 已分离 analyst 输入。本卡补**报告侧 / 下游闸 / status API**。

## 现状（基线 tip，须先在 tip 上核实行号）

- `api/services/report_service.py` 的 `_iter_failure_ledger_entries` **只扫** `market_data_context.data_failure_ledger`，不扫 `social_data_context`
- `research_manager.py` 注入 `sentiment_report` 正文，但**未**结构化注入 social status / reason_codes / direction_allowed 禁令
- `evidence_verifier.py` / `tradingagents/graph/report_quality_gate.py` 尚未把 social ledger / 不足态纳入确定性闸
- `api/services/social_data_service.py`、`GET /v1/social-data/status`、`tests/test_social_data_api.py`、`tests/test_social_downstream_gates.py` **尚不存在**

## 文件白名单

1. `api/services/report_service.py`（扩展 `_iter_failure_ledger_entries` / 相关合并；保持 empty/insufficient **不**进失败 gaps）
2. `tradingagents/agents/managers/research_manager.py`（注入 social 结构化状态；`direction_allowed=false` 时明确禁止把社交分数当多空证据）
3. `tradingagents/agents/utils/evidence_verifier.py`（social ledger → unavailable sources；禁止 insufficient 分数匹配为已验证事实）
4. `tradingagents/graph/report_quality_gate.py`（社交失败/不足 → 要求正文「不可判断」类标记，**不**要求方向量化）
5. `api/services/social_data_service.py`（新建：status 聚合，只读元数据）
6. `api/main.py`（仅注册 `GET /v1/social-data/status` + 必要依赖注入；禁止顺手大重构）
7. `tests/test_report_data_gaps.py`（扩展 social ledger 用例）
8. `tests/test_social_data_api.py`（新建）
9. `tests/test_social_downstream_gates.py`（新建：manager / verifier / quality gate）

禁止：改 `social_media_analyst.py` / adapter 大改、删 legacy、改 `data_collector` 采集逻辑、改辩论轮次 / 开加权、部署、返回帖子正文。

## 行为契约

### A. `_iter_failure_ledger_entries`

- 扫描顶层与单/双 horizon 的 `social_data_context.data_failure_ledger`（与 market 对称、去重）
- 仅 status ∈ `{failed, timeout, unavailable, refused, error}` 进入 `merge_data_gaps`
- **empty / insufficient / not_applicable / partial 不得**写成 failed gaps（§5.5）
- `source` 使用 `social_archive` 或 `social.<platform>`；失败 gap 文案前缀 `【数据获取失败】`

### B. research_manager

- 从 state 读 `social_data_context`（及 trace 若已有）：注入 mode/status/reason_codes/`direction_allowed`/`bundle_id`（紧凑文本，非 raw JSON 大表）
- `direction_allowed=false`（含 disabled legacy_proxy / shadow / empty / insufficient）：prompt **明确禁止**把社交分数/热度当多空方向证据
- 不得把 social sentinel / 帖子正文当新闻

### C. evidence_verifier

- social ledger 失败态纳入 unavailable sources
- insufficient / empty / direction_allowed=false 的分数 **不得**匹配为已验证事实

### D. report_quality_gate

- 社交失败或覆盖不足时：要求相关正文出现不可判断 / 不可用标记
- **不**因社交不足而强制要求方向量化字段

### E. `GET /v1/social-data/status`

- 需认证；只读
- 返回：当前 mode、schema/version、最近成功 run 摘要、平台覆盖、错误码/reason_codes
- **禁止**返回帖子正文、评论正文、Cookie、密钥
- 实现可放 `api/services/social_data_service.py`；路由最小挂到 `api/main.py`

## 测试命令（建议）

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_report_data_gaps.py \
  tests/test_social_data_api.py \
  tests/test_social_downstream_gates.py \
  tests/test_report_social_context.py \
  tests/test_social_api_main_wiring.py \
  tests/test_social_analyst_separation.py
```

禁止 `@pytest.mark.asyncio`（需要则 `asyncio.run`）。离线无网。TDD：先写失败用例再实现。

## 交付

1. 单 commit：`feat(report): persist social context and enforce directional guard`
2. 推送隔离分支；完整 40 位 SHA + `git diff --stat` + pytest 精确数字
3. `in_review` 后 **必须** mention [@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)（或等 Cursor 开只读审核卡）；不要 @调度助手
4. 不要自行 FF / 部署。独立审核员 PASS ≠ 准予合入（D-010）

## 质量闸（D-010 补强，缺一不可）

1. 开发 `in_review` + 证据
2. **独立代码审核员**对精确 SHA 只读书面评级
3. **Cursor** 同 SHA 隔离复测后才可写「准予合入」并开运维 FF

## Cursor 验收标准

- social ledger 进入 gaps 且 empty 不进
- manager / verifier / quality gate 尊重 direction_allowed
- status API 认证只读且无正文
- **独立审核员书面通过之后**，Cursor 隔离全绿才「准予合入」；**不准予部署**
