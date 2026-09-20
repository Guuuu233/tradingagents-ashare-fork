# H-01 期限配置与唯一解析

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `c83881809da88686c30f097b1c3872187a5733ca`（或其线性后代）。脏宿主 `/Users/davidliu/Documents/TradingAgents-AShare` 禁止当工作树、禁止 reset/clean。  
**一个关注点：** 唯一期限解析。显式合法列表采用；未提供字段 → `short` 且 `resolution_source=default`；双档只能显式合法列表；文字不能改 `resolved`；非法值／空列表／显式 null 验证错误。删除或内联替换 `_normalize_analysis_horizons`，禁止两套解析并存。  
**禁止：** 改 frontend；改 `report_service` 落库/缓存键；改分析师节点、collector、校准/回测、`DEFAULT_HOLD_DAYS`、shadow_credit；生成收益标签或 `evaluation_eligible=true`；C-04/C-09-3/Track B/H1b/PDF；改 `role_bindings`/`providers`/token；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。

计划：Codex `.../2026-09-06_整合施工计划-v1.1.md` 的 H-01。产品：短 5–20 交易日主评价 T+10；中 21–60 主评价 T+40。本卡只把这些写进 profile 常量，不算已计算出评价日。

## 允许改

- `tradingagents/graph/horizon_profile.py`（新建，唯一公共定义）
- `api/main.py`（原 `_normalize_analysis_horizons` 及全部调用点；`AnalyzeRequest.horizons` 须能区分未提供 vs 显式 `["short"]`）
- `tradingagents/graph/intent_parser.py`（写死的 `horizons: ["short"]` 与 LLM 抽取的 horizons **不算**用户显式列表）
- `tests/test_horizon_profile_contract.py`（新建）
- `tests/test_report_dual_horizon.py`（必须改开头锁测）
- 若调用点迫使最小改动：`tests/test_intent_parser.py`、`tests/test_api_smoke.py`（仅 schema/缺省）

## 现网必须推翻的行为（`c838818`）

- `api/main.py:1093-1117`：query 命中短+中正则时，即使显式 `["short"]` 也扩成双档；unknown 被丢掉后默认 short。
- 调用点：`api/main.py` 约 2861、2885、4828（chat 流式）、4936（chat 非流式）。chat 把 LLM 抽到的 horizons 再加 `query=text` 归一化，等于用自然语言当显式列表。
- 锁测：`tests/test_report_dual_horizon.py:18-22` 把上述扩档写成正确行为，本卡必须先改测试让旧实现失败，再修代码。
- `intent_parser.py:52,61` 与 `parse_intent` 返回的 `["short"]` 不是 HTTP 显式字段。
- Pydantic `horizons: List[str] = Field(default_factory=lambda: ["short"])` 会把「未提供」伪装成显式 short。

## 契约

1. 显式合法列表（仅 `short`/`medium`，可双档去重保序）→ 采用，`resolution_source=explicit`。
2. 字段未提供 → `resolved=["short"]`，`resolution_source=default`。JSON 缺省与 Python 构造不传该字段同类。
3. 显式 `null`、`[]`、含非法值（如 `[short,bogus]`）→ 验证错误（API 4xx），不得静默 short。
4. 显式单档 + 文本「短中都看看」→ 仍只该档，可非阻断提示；**不得**改 `resolved`。
5. 未提供 + 文本明确双档 → 仍 `short`/`default`，可提示「可改选双档」。
6. chat / `_parse_intent` / `_ai_extract_symbol_and_date*` 的 horizons **不是**显式列表；在 H-03 入口发显式字段之前，chat 必须按未提供处理（default short）。禁止把抽到的 `["short","medium"]` 塞进 `AnalyzeRequest.horizons` 冒充用户勾选。
7. 二次归一化不得把 default 翻成 explicit，也不得再用 query 重写 `resolved`。
8. profile 可含 `horizon_profile_v1` 与 T+10/T+40 常量；禁止计算收益、写评价资格、改 hold_days。
9. `intent_parser.parse_intent` 现有测试「LLM 返回双档仍变 short」保持：那是 parser 输出，不是 HTTP 显式选档。

## 测试

先改 `test_report_dual_horizon.py` 锁测并确认旧代码失败，再实现。命令（实施树根，解释器固定）：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_horizon_profile_contract.py tests/test_report_dual_horizon.py tests/test_dual_horizon_bugs.py tests/test_intent_parser.py -q
```

新增契约测至少覆盖：缺省、显式 short、显式 medium、显式双档、空列表、null、非法混合、query 冲突不扩档、chat 路径不把 LLM 双档当 explicit。`test_dual_horizon_e2e` 等显式传 `horizons=["short","medium"]` 的用例应仍绿。不要跑全仓当本卡验收。

一个 commit，push 功能分支，评论完整 40 位 SHA、父提交、`git diff --stat`、测试命令与真实 passed/failed/skipped。不要自建审核卡。
