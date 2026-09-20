# 独立审核（只读）：H-02a 跑次元数据进入 state

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（合入用 SHA）

- **审核/合入 tip：** `664a76b1b8ad9bbb8aba45a812e0c286928b1f21`
- **分支：** `origin/agent/1/af1169b371b9`
- **第一父 / 基线 tip：** `872d94e9477809a236b09139afe145b3343e3856`（`git rev-parse HEAD^` 已核为直接第一父）
- **关联开发卡：** [DAV-671](mention://issue/01a072ac-b748-7330-b563-5d5fbdf71040)
- **Commit：** `feat(graph): 跑次元数据进入 AgentState (H-02a, DAV-671)`

## 白名单（`git diff --stat 872d94e..664a76b`）

```
 tests/test_horizon_run_metadata.py         | 286 +++++++++++++++++++++++++++++
 tradingagents/agents/utils/agent_states.py |  15 ++
 tradingagents/graph/propagation.py         |  70 ++++++-
 3 files changed, 370 insertions(+), 1 deletion(-)
```

允许范围内。未改 `api/main.py` / `trading_graph.py` / `report_service.py`。超出即打回。

## Cursor 隔离复跑（供对照，不代替你的独立结论）

干净 checkout `/tmp/ta-iso-664a76b` HEAD=`664a76b`：

```
env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_horizon_run_metadata.py tests/test_trading_graph_multi_horizon.py tests/test_report_social_context.py tests/test_agent_states.py -q
→ 51 passed in 4.18s
```

## 复核要点

1. 未传 `horizon_resolution` 不得因 `horizon=` 变成 explicit；`requested`/`resolved`/`resolution_source`/`profile_id` 进 state。
2. 持有意图与研究档分离；T+10/T+40 只来自 `HORIZON_PROFILE_V1`；无收益计算；构造 dict 不得写 `evaluation_eligible: true`（TypedDict 可有该键，缺省省略即可）。
3. 社交默认 `social_data_context` 仍在。
4. Mapping 分支 `resolved or ["short"]` 是否会把空列表静默成 short——请点名行号判定是否打回或可随 H-02b 收口。
5. 在**本候选 SHA 干净 checkout**上复跑上述 pytest。书面 ✅ / ⚠️ / ❌，含路径行号。勿 FF。
