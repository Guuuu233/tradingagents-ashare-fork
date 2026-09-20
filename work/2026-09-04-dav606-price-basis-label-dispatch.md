# DAV-606：回测/校准 price_basis 正名（禁止把前复权标成 raw）

**父 tip：** `0dfb5c5e8507cf994e05000c954ccc57538d01c1`  
**依据：** DAV-597 只读审计；Cursor 已验收「全链路 qfq + 回测默认贴 raw」。

## 一个关注点

行情实际是**当前前复权**（`adjust="qfq"`），回测/校准却默认 `price_basis = "raw"`。这不是 PIT 修复。本卡只改正名，**不接新数据源、不做 PIT 引擎、不写 adj_factor、不改证据匹配、不补 H1b 样本、不部署、不开加权。**

## 语义

允许的 `price_basis` 取值（本卡只处理缺省误标）：

- `vendor_qfq`：现网东财/新浪/腾讯等通道返回的今日基准前复权序列
- `unspecified`：调用方没声明口径
- **禁止**在未接入不复权序列时默认或声称 `raw`
- **禁止**声称 `PIT_ADJUSTED` / `TOTAL_RETURN`

缺省：`analysis.get("price_basis")` 为空时用 `vendor_qfq`，不要用 `"raw"`。校准汇总里硬编码 `"raw"` 同样改掉。

## 允许改

- `api/services/backtest_service.py`
- `api/services/calibration_service.py`
- `tests/test_backtest_calibration_isolation.py`（及仅因本改动失败的既有断言）

魔法值提成具名常量。原路径改，禁止 `_v2`。

## RED

现有 `assert record["price_basis"] == "raw"` / `assert res["price_basis"] == "raw"` 在修复前应继续表达「误标」；修复后改为断言 `vendor_qfq`（或显式传入的值）。另加：未声明口径时不得写出 `"raw"`。

## 禁止

- `tradingagents/dataflows/providers/**` 新接口
- `evidence_verifier.py` / `decision_status.py` / `verify_h1b_gates.py`
- 回填旧 121、改 DB 生产数据、自动 FF、部署、`credit_weighting_enabled`

## 验收

一个 commit；隔离 worktree；完整 pytest 命令/退出码/数量；40 字符 SHA；changed files；`git status`；`git diff --check`。然后独立审核 exact SHA。
