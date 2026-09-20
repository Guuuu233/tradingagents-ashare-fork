# 独立审核（只读）：D-02-1 价格口径标签映射

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `12455e9b26d433c35a17b20097b0cc3c61a92edb` 或其线性后代
- **关联开发卡：** [DAV-705](mention://issue/01a07a87-b445-710d-88b3-5607954a184d)
- **Commit：** （待填）

## 白名单

允许：`api/services/price_basis_labels.py`（新建）、`tests/test_price_basis_pipeline.py`（新建）；以及仅常量/re-export 的 `api/services/backtest_service.py`、`api/services/report_service.py`；可选小幅扩展 `tests/test_backtest_calibration_isolation.py`。

超出即打回：`data_collector.py`、`cn_akshare_provider.py`、PIT 引擎、把缺省改成 `raw`/`pit_raw`、C-09-3、frontend、`role_bindings`、真实打网关。

## 复核要点

1. 短标签与 `price_basis.*` 双向映射覆盖评估稿 §4.2；`pit_raw` 与 `pit_adjusted` 不得互为别名。
2. 未知标签失败闭合，不得默默 `vendor_qfq`。
3. `_run_single_analysis` 缺省仍 `vendor_qfq`。未接 collector / Tushare 业务流。
4. 定向 pytest 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
