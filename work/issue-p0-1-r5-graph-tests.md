# P0-1 第五轮：补齐图接线回归测试

## 目标

Cursor 独立复审 `bc2b5b23a04d566c09697d660d8502e93f5ff9c8` 后，第四轮返修合同内 6 套件为 **78 passed**。但宿主在同一 SHA 上跑相邻回归时，有 **2 个既有测试失败**。这不是新功能，不是 P0-2，不是社交，也不是把 Trader→Aggressive 改回无条件边。

禁止宣称 P0-1 已闭环，直到这两个测试转绿。

## 基线

- Repository: `/Users/davidliu/Documents/TradingAgents-AShare`
- 主干（已含 P0-1，勿 revert）：`codex/dav-4-p2a-trunk` @ `bc2b5b23a04d566c09697d660d8502e93f5ff9c8`
- 从该 SHA 开隔离分支，例如 `agent/<you>/p0-1-r5-graph-tests`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干；不要 `git add -A` / reset / clean

## 已复现（Cursor 宿主，`.venv310`，proxies unset）

```
FAILED tests/test_custom_prompt_injection.py::test_T4_other_roles_no_injection
AttributeError: 'types.SimpleNamespace' object has no attribute 'should_continue_after_trader'

FAILED tests/test_arbitration_chain_behavior.py::test_risk_chain_edges_are_wired
assert ('Trader', 'Aggressive Analyst') in compiled['edges']
```

根因：DAV-461 把 `Trader → Aggressive Analyst` 从无条件边改成 `should_continue_after_trader` 条件边（可执行 → Aggressive，非可执行/资金流阻断 → Risk Judge）。产品行为正确，测试仍锁旧拓扑。

## 必须改成的断言

1. `test_T4_other_roles_no_injection`：给 `SimpleNamespace` 补上 `should_continue_after_trader`（以及 setup 实际会读到的其它 routing 方法，若还缺就补）。不要改 injection 白名单契约。
2. `test_risk_chain_edges_are_wired`：
   - **禁止**再断言 `("Trader", "Aggressive Analyst") in compiled["edges"]`
   - 必须断言 `Trader` 的 conditional mapping 含 `Aggressive Analyst` 与 `Risk Judge`
   - 必须断言 **没有** 无条件 `Trader → Aggressive Analyst`
   - Risk Judge → Trader / END 的 revise 回路保持

## 允许修改

- `tests/test_custom_prompt_injection.py`
- `tests/test_arbitration_chain_behavior.py`

## 禁止修改

- 任何产品代码（`tradingagents/`、`api/`、`frontend/`）
- `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 社交 / DAV-460 / P0-2
- 主干快进、部署

## 测试

先确认上述 2 个用例在 `bc2b5b2` 上失败，再改测试。改完：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_custom_prompt_injection.py::test_T4_other_roles_no_injection \
  tests/test_arbitration_chain_behavior.py::test_risk_chain_edges_are_wired \
  tests/test_decision_status.py \
  tests/test_p0_1_review_fixes.py \
  tests/test_research_manager_run_integrity.py \
  -q --tb=short
```

T4 与 wiring 必须绿；既有 7/7 Gate→END、revise→Trader astream≥1 必须仍绿。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `bc2b5b2`
- 精确 pytest 输出
- 不要写「彻底修复」；不要自行合主干；不要部署
- 完工后只 @项目调度助手 一次，并写明 SHA
