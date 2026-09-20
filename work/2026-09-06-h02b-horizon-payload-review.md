# 独立审核（只读）：H-02b 跑次元数据回显落库

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（合入用 SHA）

- **审核/合入 tip：** `afd89503b9f93fc4b4a5b6b5a868bca686de030f`
- **分支：** `origin/agent/2/d9133b8b58b8`
- **第一父 / 基线 tip：** `664a76b1b8ad9bbb8aba45a812e0c286928b1f21`（`git rev-parse HEAD^` 已核为直接第一父）
- **关联开发卡：** [DAV-673](mention://issue/01a072f2-a7b1-7ae9-b34c-800124d62f8e)
- **Commit：** `feat(report): 跑次元数据回显落库与回显 (H-02b, DAV-673)`

## 白名单（`git diff --stat 664a76b..afd8950`）

```
 api/main.py                          | 113 +++++++++++-
 api/services/report_service.py       | 192 +++++++++++++++++++-
 tests/test_horizon_run_metadata.py   | 340 +++++++++++++++++++++++++++++++++++
 tests/test_report_dual_horizon.py    |  61 ++++++-
 tradingagents/graph/trading_graph.py |   4 +
 5 files changed, 700 insertions(+), 10 deletions(-)
```

未改 `tests/test_report_social_context.py` 可接受（回归仍须跑）。超出即打回：`propagation.py`、frontend、缓存键、collector、分析师、校准。

## Cursor 隔离复跑（供对照，不代替你的独立结论）

`/tmp/ta-iso-afd8950` HEAD=`afd8950`：

```
env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_horizon_run_metadata.py tests/test_report_dual_horizon.py tests/test_report_social_context.py tests/test_trading_graph_multi_horizon.py -q
→ 59 passed in 12.94s
```

## 复核要点

1. 全部 `create_initial_state` 传入 `horizon_resolution`；二次归一化不把 default 翻 explicit；`propagate_async` 不得用 `user_intent.horizons` 冒充 explicit。
2. `_build_horizon_result` / `_build_result_payload` / `ensure_report_horizon_metadata_persisted` / 读路径 legacy 不回填 T+40。
3. 未改 `make_cache_key`；构造/落库不得保留 `evaluation_eligible: true`。
4. 在本候选 SHA 干净 checkout 复跑上述 pytest。书面 ✅ / ⚠️ / ❌，含路径行号。勿 FF。
