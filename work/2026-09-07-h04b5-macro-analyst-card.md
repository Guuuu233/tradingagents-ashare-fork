# H-04b-5 宏观分析师：运行档 / 观察窗 / trace

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `3899fe82b6b698c44608ee38010094ddca2e0c1b`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 只改宏观分析师节点：专业观察窗仍是中长期板块/宏观证据（现网 `horizon = "medium"`，`data_window=="板块数据"`）；本次研究档进入 prompt/trace，短期任务不得被 trace 写成 medium。  
**禁止：** 改其它分析师 / researchers / `research_manager.py` / `data_collector.py` 的 `get_window` 实现；H-04b-6…7、H-04c、H-04d、H-05；把「板块数据」改成 T+40 或改成 14/10 天；改 `role_bindings`/`providers`；C-04/C-09-3/Track B/H1b/PDF；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-04b-5**。H-04b-4 已合入基本面：`horizon` 字段为研究档，观察窗 medium /「财报周期」。宏观节点仍把专业窗和研究档都写成 `"medium"`，`build_horizon_context` 未传 `research_horizon`，短期切片的 `analyst_traces[0]["horizon"]` 会假成 medium。本卡对齐同一套 trace 冻结方案，不要发明第二套字段名。

## 允许改

- `tradingagents/agents/analysts/macro_analyst.py`
- 仅 `tradingagents/prompts/zh.py` 与 `en.py` 的 **`macro_system_message`**（不要动 `horizon_context_block` 或其它角色模板；en 可能没有该键则不要硬加无关模板）
- `tests/test_macro_analyst.py`（本卡主验收；可新建该文件）

## 契约

1. 专业观察窗保持中长期：宏观/板块仍走现有 medium / `data_window=="板块数据"` 路径。禁止把观察窗改成 short，禁止把「板块数据」改成 T+40 或具体天数冒充评价步长。
2. 研究档从 `state["horizon"]` 和/或 `state["horizon_run_metadata"]` / H-04a 绑定读取。`build_horizon_context` 第一参数仍是专业窗 `"medium"`，不要改调用槽位语义；传入 `research_horizon=`。
3. `analyst_traces` 必须与 H-04b-1 **同一冻结方案**：`horizon` = 本次研究档；另存 `research_horizon` 与 `observation_horizon`（观察窗为 `"medium"`）。短期切片时不得只留一个 `horizon: "medium"`。禁止两套字段语义并存。
4. `data_window` 短期/中期任务均仍为 `"板块数据"`。
5. 数据失败显式上报，不编造宏观。本卡不改 collector 共享池。
6. 不改 market/news/social/fundamentals/smart_money/volume_price 文件。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_macro_analyst.py tests/test_horizon_analyst_context.py
```

至少覆盖：short 研究档时 trace 研究档=short、观察窗=medium、`data_window=="板块数据"`；medium 研究档时研究档=medium、观察窗=medium。一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
