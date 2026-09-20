# D-02-1 / C-04-3：价格口径标签映射（仅枚举，不接线）

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `12455e9b26d433c35a17b20097b0cc3c61a92edb`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 冻结 `backtest_service` 短标签与 `report_service` / cohort 三元里 `price_basis_version` 的**兼容映射**与单元测试。不接 collector、不接 Tushare `daily`/`dividend` 业务流、不实现 PIT 复权引擎、不改回测缺省 `vendor_qfq`。  
**禁止：** 改 `role_bindings`/`providers` 表；改分析师 / frontend；C-09-3；H1b 补样本；FF/部署；push 主干；打印 token；真实打 `api.tushare.pro`。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **D-02 第一刀**：先核对实际枚举并给出映射测试，不宣称 `pit_raw` 已是统一在用值。D-01 读取契约已在主干 `12455e9`；本卡**不得**把 raw 通道接到行情消费者。

评估稿 `work/2026-09-05-c04-pit-raw-dividend-eval.md` §4.2（标签层，非数据已落地）：

| 业务短标签 (`price_basis`) | cohort `price_basis_version` |
|---|---|
| `vendor_qfq` | `price_basis.vendor_qfq` |
| `unspecified` | `price_basis.unspecified` |
| `raw` | `price_basis.raw` |
| `pit_raw` | `price_basis.pit_raw` |
| `pit_adjusted` | `price_basis.pit_adjusted` |

现网事实（必须用测试钉住，禁止“统一成一个字符串”）：

- `backtest_service.PRICE_BASIS_VENDOR_QFQ == "vendor_qfq"`
- `backtest_service.PRICE_BASIS_UNSPECIFIED == "unspecified"`
- `report_service.PRICE_BASIS_UNSPECIFIED == "price_basis.unspecified"`
- `shadow_credit.PRICE_BASIS_UNSPECIFIED == "price_basis.unspecified"`
- `_run_single_analysis` 缺省仍是 `vendor_qfq`，**不得**改成 `raw` / `pit_raw`
- 已有 H1b / cohort 测试使用 `price_basis.pit_adjusted`；它与 `pit_raw` **不是同一口径**，禁止互相别名

## 允许改

- 新建 `api/services/price_basis_labels.py`（推荐：唯一映射表 + 双向转换；未知标签失败闭合，禁止默默落到 `vendor_qfq`）
- 可选：在 `backtest_service.py` / `report_service.py` **只增加具名常量或 re-export**，不得改缺省行情路径、不得把传入的 `raw`/`pit_raw` 仍按本卡要求去接数据源
- 新建 `tests/test_price_basis_pipeline.py`
- 如需保持 DAV-606 断言同步，可**小幅**扩展 `tests/test_backtest_calibration_isolation.py`（仍只断言标签/常量，不接 collector）

不要改：`data_collector.py`、`cn_akshare_provider.py`、`calibration_service.py` 的默认 `price_basis` 写入逻辑（除非只加 import/re-export 且测试证明行为不变）。不要改 `shadow_credit.py` 的缺省 cohort 字符串。

## 契约

1. 双向映射覆盖上表五行；短标签与 `price_basis.*` 互转。
2. 未知 / 空 / `raw` 被误写成前复权时：映射失败必须显式错误类型，禁止当成 `vendor_qfq`。
3. `pit_raw` ≠ `pit_adjusted` ≠ `raw` ≠ `vendor_qfq`。
4. 本卡**不得**宣称 PIT 数据或 raw 日线已进入回测/报告；映射存在 ≠ 通道可用。
5. 回归：缺省单次分析仍 `price_basis == "vendor_qfq"` 且 `!= "raw"`（可复用现有 isolation 用例）。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_price_basis_pipeline.py tests/test_backtest_calibration_isolation.py tests/test_cohort_metadata_persistence.py
```

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
