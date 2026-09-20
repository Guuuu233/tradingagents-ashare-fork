# H-04b-1 市场分析师：运行档 / 观察窗 / trace

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `f03466c6a9d7525671749e856c0cfc03bc2a0190`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 只改市场分析师节点：专业观察窗仍是短期技术证据；本次研究档进入 prompt/trace，中期任务不得被 trace 写成 short。  
**禁止：** 改其它分析师 / researchers / `research_manager.py` / `data_collector.py` 的 `get_window` 实现；H-04b-2…7、H-04c、H-04d、H-05；把 14 天换成 T+10；改 `role_bindings`/`providers`；C-04/C-09-3/Track B/H1b/PDF；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-04b-1**。H-04a 已合入：`build_horizon_context` 能同时写研究档与专业窗；市场节点仍 `horizon = "short"`，`get_window(..., "short")`，`analyst_traces[0]["horizon"] == "short"`。现有 `tests/test_market_analyst.py::test_market_analyst_uses_short_window_for_medium_request` 把中期请求的 trace.horizon 断言为 short，这正是本卡要拆开的语义。

## 允许改

- `tradingagents/agents/analysts/market_analyst.py`
- 仅 `tradingagents/prompts/zh.py` 与 `en.py` 的 **`market_system_message`**（不要动 `horizon_context_block` 或其它角色模板）
- `tests/test_market_analyst.py`（本卡主验收；可按选择器跑单文件）

## 契约

1. 专业观察窗保持短期：K 线/指标回看仍走现有 short/14 天路径；禁止把 14 改成 10。持有/评价步长不是回看天数。
2. 研究档从 `state["horizon"]` 和/或 `state["horizon_run_metadata"]` / H-04a 绑定读取。`build_horizon_context` 第一参数仍是专业窗 `"short"`，不要改调用槽位语义。
3. `analyst_traces` 必须能区分：本次研究档 vs 专业观察窗。中期切片 + 14 天窗口时，不得只留一个 `horizon: "short"` 让下游以为整次任务是 short。可保留 `horizon` 为观察窗并**新增**明确字段（如 `research_horizon` / `observation_horizon`），或把 `horizon` 改为研究档并另存观察窗——须在测试里写死一种，禁止两套并存。
4. `data_window` 中期任务仍为 14 天量级（现网 `"14天"`）。
5. 不足历史标缺失，不编造 MA。本卡不改 collector 共享池。
6. 不改 news/fundamentals/其它分析师文件。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_market_analyst.py tests/test_horizon_analyst_context.py
```

至少覆盖：medium 研究档时 trace 研究档=medium、观察窗=short、`data_window=="14天"`；short 研究档时研究档=short、观察窗=short。一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
