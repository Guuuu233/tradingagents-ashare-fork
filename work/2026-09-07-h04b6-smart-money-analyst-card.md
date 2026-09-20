# H-04b-6 资金分析师：运行档 / 观察窗 / trace

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `d4f8bef0d0339128df53ef9286d4e38faa57b6e6`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 只改资金分析师节点：专业观察窗仍是短期资金证据（现网 `horizon = "short"`，`data_window=="近期可用"`）；本次研究档进入 prompt/trace，中期任务不得被 trace 写成 short。  
**禁止：** 改其它分析师 / researchers / `research_manager.py` / `data_collector.py` 的 `get_window` 实现；H-04b-7、H-04c、H-04d、H-05；把「近期可用」改成 T+10 或改成具体天数冒充评价步长；改资金流字段语义 / `scale_metrics` / C-09-3；改 `role_bindings`/`providers`；Track B/H1b/PDF；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-04b-6**。H-04b-1…5 已合入市场/新闻/社交/基本面/宏观。资金节点仍 `horizon = "short"`，`build_horizon_context` 未传 `research_horizon`，中期切片的 `analyst_traces[0]["horizon"]` 会假成 short。本卡对齐同一套 trace 冻结方案，不要发明第二套字段名。

## 允许改

- `tradingagents/agents/analysts/smart_money_analyst.py`
- 仅 `tradingagents/prompts/zh.py` 与 `en.py` 的 **`smart_money_system_message`**（不要动 `horizon_context_block` 或其它角色模板；en 可能没有该键则不要硬加无关模板）
- `tests/test_smart_money_analyst.py`（本卡主验收；可新建该文件。不要改 `tests/test_smart_money_fund_flow_semantics.py` 除非为了本卡断言必须引用既有辅助函数——默认不要动）

## 契约

1. 专业观察窗保持短期：资金流仍走现有 short / `data_window=="近期可用"` 路径。禁止把观察窗改成 medium，禁止把「近期可用」改成 T+10 或 14/10 天。
2. 研究档从 `state["horizon"]` 和/或 `state["horizon_run_metadata"]` / H-04a 绑定读取。`build_horizon_context` 第一参数仍是专业窗 `"short"`，不要改调用槽位语义；传入 `research_horizon=`。
3. `analyst_traces` 必须与 H-04b-1 **同一冻结方案**：`horizon` = 本次研究档；另存 `research_horizon` 与 `observation_horizon`（观察窗为 `"short"`）。中期切片时不得只留一个 `horizon: "short"`。禁止两套字段语义并存。保留既有资金流机读字段（如 `source_mode` / `direction_allowed` 等），不要改语义。
4. `data_window` 中期任务仍为 `"近期可用"`。
5. 数据失败显式上报，不编造资金流。本卡不改 collector 共享池，不改 provider 资金流实现。
6. 不改 market/news/social/fundamentals/macro/volume_price 文件。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_smart_money_analyst.py tests/test_horizon_analyst_context.py
```

至少覆盖：medium 研究档时 trace 研究档=medium、观察窗=short、`data_window=="近期可用"`；short 研究档时研究档=short、观察窗=short。一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
