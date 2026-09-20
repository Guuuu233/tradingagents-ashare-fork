# Track B-1：社交不可用语义 + `social_data_context` 留痕 + 真实报告回归

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk`（开工再核 tip；C-05 可能前进，本卡不改巨潮挂载文件）。  
**父说明：** `work/2026-09-05-track-b-remaining.md`。  
**三个关注点、三个 commit、禁止 squash。** 禁止 push 主干。禁止部署 / active / 采集 / 改 `.env`。

`TA_SOCIAL_MODE=disabled` 不是本卡要改的开关。本卡修的是：分析端在不可用时把缺口写成市场事实，以及 API 丢掉 `social_data_context`。

## 允许改（按 commit 收窄）

Commit A — 不可用 ≠ 市场冷淡  
- `tradingagents/dataflows/social/prompt_formatter.py`  
- `tradingagents/prompts/zh.py` / `en.py`（仅社交分析师相关段落）  
- `tradingagents/agents/analysts/social_media_analyst.py`（仅必要）  
- `tradingagents/graph/report_quality_gate.py`（仅社交缺口门）  
- 对应测试  

Commit B — API 单/双周期 + 落库 + 读取  
- `api/main.py`（`_build_result_payload` 必须带上 `social_data_context`）  
- `api/services/report_service.py`（若读取路径丢字段）  
- `tests/test_report_social_context.py` 或新建 API 级测试：走组装/落库/读回，**不能只测 Graph 内部 dict**  

Commit C — 脱敏真实报告回归  
- `tests/fixtures/social/reports/`（隆基 601012、爱尔 300015 脱敏摘录，去掉用户/密钥）  
- 新测试：`not_applicable` + `direction_allowed=false` 时，正文不得把缺口当成「市场没有讨论」；社交不得成为方向性证据。  
- **禁止**只禁用「真空」等关键词；要锁语义（不可用/未采集/样本不足）。

## 契约

1. 不可用、未采集、样本不足：不得推导市场冷淡、无人关注、或「经分析得出的中性」。  
2. 市场关注度（涨停池/雪球等）有独立数据时仍可写，必须分栏并注明来源，不得冒充社交正文。  
3. 旧方案允许机器 verdict 保留「中性」：若兼容保留，必须同时 **明确不可用** 且 `direction_allowed=false`，并证明不进入有效中性票 / 校准样本。规范与测试一并改到一致，禁止文档写 A、代码写 B。  
4. 单周期、双周期 payload、落库 `result_data`、再读取，都要有 `social_data_context`（可为空结构，但键必须在）。  

## 禁止

`news_event_evidence.py`、`cn_akshare_provider.py`、`data_collector.py` 非社交必要改动、加权 flag、role_bindings、真网关、打印 token。

完成后：功能分支 + 三个 40 位 SHA（或说明三 commit 的 parent 链）+ pytest 精确数字。状态改 `in_review`。不要自行 FF / 部署。不要 @项目调度助手。独立审查另开卡，等 Cursor 填完整 SHA。

建议测试（实现后按实际文件名调整，交付写精确数字）：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_analyst_separation.py \
  tests/test_report_social_context.py \
  tests/test_social_api_main_wiring.py \
  tests/test_analyst_prompts_deep_reasoning.py
```

解释器：`env -u PYTHONPATH .venv310/bin/python`。禁止 `@pytest.mark.asyncio`（需要则 `asyncio.run`）。
