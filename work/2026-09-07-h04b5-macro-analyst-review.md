# 独立审核（只读）：H-04b-5 宏观分析师运行档/观察窗/trace

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（合入用 SHA）

- **审核/合入 tip：** `d4f8bef0d0339128df53ef9286d4e38faa57b6e6`
- **分支：** `origin/agent/2/b2f143b43e4c`
- **第一父 / 基线 tip：** `3899fe82b6b698c44608ee38010094ddca2e0c1b`（`origin/codex/dav-4-p2a-trunk`，单亲线性）
- **关联开发卡：** [DAV-693](mention://issue/01a07804-365e-71ab-9b41-157b3e8afc15)
- **Commit：** `feat(macro): 运行档/观察窗/trace解耦 (H-04b-5, DAV-693)`

## 白名单（`git diff --stat 3899fe8..d4f8bef`）

```text
 tests/test_macro_analyst.py                    | 274 +++++++++++++++++++++++++
 tradingagents/agents/analysts/macro_analyst.py |  50 ++++-
 2 files changed, 319 insertions(+), 5 deletions(-)
```

仅允许：`macro_analyst.py`、`tests/test_macro_analyst.py`、以及 zh/en 的 `macro_system_message`。超出即打回：其它分析师、`data_collector.py`、`intent_parser.py`、`horizon_context_block`、frontend、`role_bindings`。

## 调度对照（不代替你的独立结论）

Cursor 隔离 `/tmp/ta-iso-d4f8bef-cursor` HEAD=`d4f8bef0d0339128df53ef9286d4e38faa57b6e6`：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_macro_analyst.py tests/test_horizon_analyst_context.py
# 16 passed in 0.48s
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_macro_analyst.py tests/test_fundamentals_analyst.py tests/test_market_analyst.py tests/test_news_analyst.py tests/test_social_media_analyst.py tests/test_analyst_prompts_deep_reasoning.py tests/test_trading_graph_multi_horizon.py
# 84 passed in 33.20s
```

实现卡自报：验收 16 passed；关联回归 40 passed。观察窗 medium，`data_window` 仍为「板块数据」。调度助手在 DAV-693 上因模型超时失败，本卡由 Cursor 补派 Path A，实现卡禁止再 @本审核员。

## 复核要点

1. 短期任务 trace 不得把整次运行写成 medium；观察窗保持 medium；`data_window` 仍为「板块数据」。
2. 未把回看/评价步长改成 T+40。
3. trace 字段与 H-04b-1 冻结方案一致：`horizon`=研究档，另有 `research_horizon` / `observation_horizon`。
4. `pytest -q tests/test_macro_analyst.py tests/test_horizon_analyst_context.py` 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
