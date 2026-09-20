# DAV-289 P3：非交易日测试环境隔离（统一日期 fixture）

**基线父提交：`cfc1e22ce8b060a18017acbfa8f4d92144df9cd5`。独立分支，不合主干。**

## 目标（团队终审遗留技术债）

历史资金流等测试在周末/节假日运行会出现假阳性（依赖真实日历打桩不统一）。

## 契约

1. 新增统一 pytest fixture（如 `tests/conftest.py` 中 `frozen_trade_date`）：把测试内所有「今天/trade_date」打桩到固定已知交易日（选一个历史周一，写死日期）。
2. 改造受影响测试改用该 fixture；禁止改生产代码、禁止改 `historical_cases` 的 T+1 语义。
3. 验证方式：fixture 下测试在任意自然日运行结果一致（可本地改系统日期语义模拟，或以 monkeypatch 时间源证明）。
4. 禁止碰 `.env`、providers、role_bindings。

## 验收

- `.venv310` 全量 `pytest tests/ -q` 在本卡分支上跑一遍贴结果；`compileall`；推送独立分支精确 SHA。
- 不得 @项目调度助手。
