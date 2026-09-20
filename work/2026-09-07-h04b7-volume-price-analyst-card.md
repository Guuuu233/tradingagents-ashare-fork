# H-04b-7 量价分析师：运行档 / 观察窗 / trace

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `a822a866c3301cc3acd474d073b3ef823d5f6916`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 只改量价分析师节点：专业观察窗仍是短期量价证据（现网 `horizon = "short"`，`get_window(..., "short")`，`data_window` 约 `"14天"`）；本次研究档进入 prompt/trace，中期任务不得被 trace 写成 short。  
**禁止：** 改其它分析师 / researchers / `research_manager.py` / `data_collector.py` 的 `get_window` 实现；H-04c、H-04d、H-05；把 14 天换成 T+10；改 `role_bindings`/`providers`；C-04/C-09-3/Track B/H1b/PDF；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-04b-7**（七分析师最后一包）。H-04b-1…6 已合入。量价节点仍 `horizon = "short"` 并把它传给 `get_window`，`build_horizon_context` 未传 `research_horizon`，中期切片的 `analyst_traces[0]["horizon"]` 会假成 short。本卡对齐同一套 trace 冻结方案。

## 允许改

- `tradingagents/agents/analysts/volume_price_analyst.py`
- 仅 `tradingagents/prompts/zh.py` 与 `en.py` 的 **`volume_price_system_message`**（不要动 `horizon_context_block` 或其它角色模板）
- `tests/test_volume_price_analyst.py`（本卡主验收；可新建该文件）

## 契约

1. 专业观察窗保持短期：`get_window` / 缺省窗口仍走 short / `"14天"`。禁止把 14 改成 10。持有/评价步长不是回看天数。调用 `get_window` 时传入观察窗 `"short"`，不要传入研究档。
2. 研究档从 `state["horizon"]` 和/或 `state["horizon_run_metadata"]` / H-04a 绑定读取。`build_horizon_context` 第一参数仍是专业窗 `"short"`；传入 `research_horizon=`。
3. `analyst_traces` 必须与 H-04b-1 **同一冻结方案**：`horizon` = 本次研究档；另存 `research_horizon` 与 `observation_horizon`（观察窗为 `"short"`）。中期切片 + 14 天窗口时不得只留一个 `horizon: "short"`。
4. `data_window` 中期任务仍为 14 天量级（现网 `"14天"`）。
5. 数据失败显式上报，不编造量价。本卡不改 collector 共享池。
6. 不改其它分析师文件。本卡完成后不要自行开 H-04c。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_volume_price_analyst.py tests/test_horizon_analyst_context.py
```

至少覆盖：medium 研究档时 trace 研究档=medium、观察窗=short、`data_window=="14天"`；short 研究档时研究档=short、观察窗=short。一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
