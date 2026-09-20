# P2-T11：重写 social analyst 输入并锁分离

## 目标

social 与 news **输入分离**：active 模式只消费 `social_data_context` + `market_data_context.market_attention`；禁止再用 `pool.get("news")` / 直调 `get_news` 当社交舆情。mode 分支集中在适配层（`legacy_proxy` 仅 Gate 0–3 允许）。fake LLM 测 NEWS/SOCIAL sentinel 互不泄漏。

本卡**不**做 Task 12 报告/API status、不删 legacy（Gate 4）、不部署。默认 `TA_SOCIAL_MODE` 仍 disabled。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `0cc34278c8024680e0b687bd029295876b6e0c98`
- **新建**隔离分支，例如 `agent/dev2/p2-t11-social-analyst-separation`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 11 + §3.1 迁移适配层 + §六 新闻/关注度/社交分离
- D-009 / D-010
- ToolNode 已在 T10 去掉 social 的 `get_news`；本卡修 **analyst 节点自身**仍读 news/zt/hot 的路径

## 现状（基线 tip）

`social_media_analyst.py` 仍：

- `pool.get("news"|"zt_pool"|"hot_stocks")`
- 无 pool 时 `asyncio.gather(get_news, get_zt_pool, get_hot_stocks_xq)`
- HumanMessage 标题「舆情近似资料」并注入 `【get_news】`

`analyst_adapter.py` / `prompt_formatter.py` **尚不存在**，本卡新建（放在 `tradingagents/dataflows/social/`，与 plan 文件清单一致）。

## 文件白名单

1. `tradingagents/dataflows/social/analyst_adapter.py`（新建：`resolve_social_analyst_inputs` 等；**唯一** mode 分支）
2. `tradingagents/dataflows/social/prompt_formatter.py`（新建：把 bundle/attention 格式化为四段文本）
3. `tradingagents/agents/analysts/social_media_analyst.py`
4. `tradingagents/agents/analysts/news_analyst.py`（仅锁分离：不得读 social_data_context / 社交 sentinel；最小改动）
5. `tradingagents/prompts/zh.py` / `tradingagents/prompts/en.py`（social system prompt；禁止改辩论轮次相关）
6. `tradingagents/dataflows/social/__init__.py`（最小导出）
7. `tests/test_social_analyst_separation.py`（新建）
8. `tests/test_analyst_prompts_deep_reasoning.py`（同步 `test_social_system_message_deep_framework` 期望）

禁止：`trading_graph.py` ToolNode 再扩、删 legacy 分支（Gate 4）、`api/main.py` 大改、改 `max_debate_rounds` / 开加权、部署。

## 行为契约

### A. 适配层（唯一 mode 分支）

`resolve_social_analyst_inputs(mode, social_data_context, market_data_context|pool, ...)`（名称可微调，须可测）：

| mode | 行为 |
|---|---|
| `disabled`（Gate 4 前） | 可返回 legacy news/zt/hot；trace `source_mode=legacy_proxy`；**不得**把 archive bundle 当方向证据 |
| `shadow` | 可读/持有 bundle，但分析师正文输入仍走 legacy 字段；bundle 不进方向证据 |
| `active` | **只**返回 bundle + `market_attention`；缺数据 → 明确缺口文案；**禁止**回退 `news` / `get_news` |

mode 从 `social_data_context.mode` 或 config/`TA_SOCIAL_MODE` 读取，与 T7/T8 一致。

### B. social_media_analyst

- 删除（或经适配层后不再直接）`pool.get("news")` 与 fallback `get_news` 直调。
- active：HumanMessage 四段——数据状态、社交观点、社交热度、市场关注度（formatter 产出）。
- 热度 ≠ 看多；score 非校准概率；不足/失败 → 不可判断，不得编造方向。
- 仍可从 state 读 `social_data_context` / `market_data_context`（T9 已接线）。

### C. news_analyst

- 继续只消费公司新闻/全球新闻（及既有 event coverage）。
- 断言：social sentinel / bundle 正文不得进入 news messages。

### D. Prompts + deep_framework 测试

- 更新 social system prompt：对齐四段结构与禁令。
- `test_social_system_message_deep_framework`：**不再**要求涨停池/雪球作为社交**观点**来源；改为四段、热度≠看多、score 非概率、数据不足不可判断。

### E. Sentinel 测试

`tests/test_social_analyst_separation.py`：

1. fake LLM 捕获 messages
2. NEWS sentinel 不得出现在 social prompt/messages
3. SOCIAL sentinel 不得出现在 news prompt/messages
4. active + 有 bundle：无 `【get_news】` / 无直调 news tool
5. disabled：可走 legacy_proxy（测 source_mode）；仍不部署、不改默认 mode

## 测试命令（建议）

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_analyst_separation.py \
  tests/test_analyst_prompts_deep_reasoning.py \
  tests/test_social_toolnode_no_news.py \
  tests/test_report_social_context.py \
  tests/test_social_api_main_wiring.py
```

禁止 `@pytest.mark.asyncio`（需要则 `asyncio.run`）。离线无网。

## 交付

1. 单 commit：`feat(analyst): separate social sentiment from news and attention`
2. 推送隔离分支；完整 40 位 SHA + `git diff --stat` + pytest 精确数字
3. `in_review`；不要 @调度助手；不要自行 FF / 部署

## Cursor 验收标准

- 适配层是唯一 mode 分支；active 无 news 回退
- sentinel 互不泄漏；deep_framework 期望已改
- 隔离全绿后才「准予合入」；**不准予部署**
