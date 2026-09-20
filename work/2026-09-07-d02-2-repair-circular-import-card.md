# D-02-2 返修：provider 不得逆向导入 api.services

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** 从被打回分支 `origin/agent/1/1cc4b806cf96` @ `6ee6272b00f1ce6015f4524cada07ba400eeeed0` 继续（第一父仍须能线性回到 `fbdc598ef527ea01fba7f6072f600bd557762715`）。脏宿主禁止当工作树。  
**一个关注点：** 删掉 `cn_akshare_provider.py` 对 `api.services.price_basis_labels`（及任何 `api.services*`）的导入，消除循环依赖，使 `tests/test_h1b_gates.py` CLI 子进程用例转绿。保留 D-02-2 已实现的显式 `price_basis=raw` 通道与缺省 qfq。  
**禁止：** 把映射模块下沉到新文件（那是另一关注点）；改 collector / 回测缺省 / dividend 接线；改 `role_bindings`/`providers`；FF/部署；push 主干；打印 token；真实打网关。本卡评论禁止 @独立代码审核员。不要自建审核卡。

被打回 SHA：`6ee6272b00f1ce6015f4524cada07ba400eeeed0`（DAV-707/708）。独立审核 HIGH：`cn_akshare_provider.py:80-89` 顶层 import `api.services.price_basis_labels` → `api.services.__init__` → `tracking_board_service` → `tradingagents.dataflows.interface.route_to_vendor`，interface 尚未初始化完。Cursor 隔离：**1 failed, 169 passed**，失败用例 `TestH1bVerifyGatesDbPath.test_cli_subprocess_execution_with_db_path`。

原 DAV-707 卡写「可 import api.services.price_basis_labels」作废。provider 内只用短标签字符串（`"raw"` / `"vendor_qfq"` 等）和本文件局部校验/异常。

## 允许改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tests/test_price_basis_raw_provider.py`（若断言依赖了从 `api.services` 导入的异常类名，改为 provider 局部异常或字符串）

不要改：`api/services/price_basis_labels.py`、`data_collector.py`、`backtest_service.py`、`report_service.py`。

## 契约

1. `tradingagents/dataflows/providers/cn_akshare_provider.py` 源码中不得出现 `api.services`。
2. 缺省仍 qfq/`vendor_qfq`；仅显式 `raw` 走 `_fetch_tushare_raw_daily`；raw 失败不得回退 qfq。
3. `pit_raw` / `pit_adjusted` 本卡仍不可用；未知标签失败闭合。
4. `tests/test_h1b_gates.py` 必须全绿（含 CLI 子进程）。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_price_basis_raw_provider.py tests/test_tushare_raw_daily_contract.py tests/test_price_basis_pipeline.py tests/test_h1b_gates.py tests/test_shadow_credit.py
```

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
