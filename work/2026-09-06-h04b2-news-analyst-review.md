# 独立审核（只读）：H-04b-2 新闻分析师运行档/观察窗/trace

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（合入用 SHA）

- **审核/合入 tip：** `904dc0eccc542baf14c682c8b9136c0142d33773`
- **分支：** `origin/agent/1/01141a205112`
- **第一父 / 基线 tip：** `645cbb5a07c6a1605009fbbd435473b5fd7a1576`（`origin/codex/dav-4-p2a-trunk`，单亲线性）
- **关联开发卡：** [DAV-687](mention://issue/01a07776-3996-7767-bd20-1a33f0359e0d)
- **Commit：** `feat(news): 运行档/观察窗/trace解耦 (H-04b-2, DAV-687)`

## 白名单（`git diff --stat 645cbb5..904dc0e`）

```text
 tests/test_news_analyst.py                    | 220 ++++++++++++++++++++++++++
 tradingagents/agents/analysts/news_analyst.py | 110 +++++++++----
 2 files changed, 298 insertions(+), 32 deletions(-)
```

仅允许：`news_analyst.py`、`tests/test_news_analyst.py`、以及 zh/en 的 `news_system_message`。超出即打回：其它分析师、`data_collector.py`、`intent_parser.py`、`horizon_context_block`、frontend、`role_bindings`。

## 调度对照（不代替你的独立结论）

Cursor 隔离 `/tmp/ta-iso-904dc0e-cursor` HEAD=`904dc0eccc542baf14c682c8b9136c0142d33773`：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_news_analyst.py tests/test_horizon_analyst_context.py
# 15 passed in 0.59s
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_market_analyst.py tests/test_analyst_prompts_deep_reasoning.py tests/test_trading_graph_multi_horizon.py
# 58 passed in 41.66s
```

实现卡自报：验收 15 passed；未改 collector。`_fetch_direct` 在观察窗 short 时仍 14 天。

## 复核要点

1. 中期任务 trace 不得把整次运行写成 short；14 天观察窗保留。
2. 未把回看天数改成 T+10。
3. trace 字段与 H-04b-1 冻结方案一致：`horizon`=研究档，另有 `research_horizon` / `observation_horizon`。
4. `pytest -q tests/test_news_analyst.py tests/test_horizon_analyst_context.py` 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
