# DAV-1506 (存储 B-1) 读取点清单 —— result_data 读取入口审计

范围：自 `db9c690`（B-1 开工基线）之后进入主干 `codex/dav-4-p2a-trunk`、或已建候选待合入、且会读 `result_data` 的脚本 / 模块。逐条核：是否读 `horizons`、是否读顶层 `market_data_context`、是否经兼容层 `tradingagents/storage/result_data_compat.py`。

裁定（D-072 附注）：`horizons.<h>` 与顶层 `market_data_context` 为**读取层虚拟键**——canonical（`storage_schema_version == result_data.storage.v1`）行不物理存这两个键，由 `expand_compat_view` 从权威 `short_term` / `medium_term` 切片重建。任何直接读这两个键、又不经兼容层的读取入口，在 canonical 行上会拿到空值。

## 一、主干内读取入口（已过 B-1 接线 / 或本就读权威档）

| # | 入口 | 读 `horizons` | 读顶层 `market_data_context` | 经兼容层 | 结论 |
|---|------|--------------|------------------------------|---------|------|
| 1 | `api/services/report_service.py::get_report` | 重建 | 重建 | `result_data_compat_view` | 已接（`expunge`+`deepcopy` 后 expand，detached shadow） |
| 2 | `tradingagents/knowledge/historical_cases.py::extract_claims_from_report` | 是 | 是 | `result_data_compat_view`(L630) | 已接 |
| 3 | `scheduler/main.py` 通知装载 | 重建 | 重建 | `result_data_compat_view`(L172) | 已接（`expunge` 后） |
| 4 | `scripts/phase2/daily_snapshot_ledger.py` `_horizon_judgement` / mdc provenance | 是 | 是 | `result_data_compat_view`(L390) | 已接（B-1 接线） |
| 5 | `scripts/verify_h1b_gates.py` | 否 | 否 | — | 读权威档：`split_report_into_units` 只取 `*_term`；`extract_sample_cohort` 只取 version 键。无需兼容层 |
| 6 | `scripts/phase2/cache_acceptance.py`（DAV-1546，新入主干） | 否 | 否 | 不适用 | 只审计 pkl 缓存目录，**不读 reports 表**，无关 |
| 7 | `scripts/dav1507_exit_progress.py` | 否（读 `short_term`/`medium_term`，L608） | 否 | 权威档 | 读 `*_term` 即权威档，无需兼容层 |

## 二、shadow_credit 单元拆分链（verify_h1b_gates 上游）

| 函数 | 读 `horizons` | 读顶层 `market_data_context` | 经兼容层 | 结论 |
|------|--------------|------------------------------|---------|------|
| `split_report_into_units` (L661) | 否 | 否 | 权威档 | 只读 `short_term`/`medium_term`，canonical 行天然兼容 |
| `extract_sample_cohort` (L1216) | 否 | 否 | — | 只取 `decision_model_version`/`evidence_contract_version`/`price_basis_version`/`generated_by_commit_sha`，不触虚拟键 |
| `_extract_market_data_context_map` (L889) | 否 | **是**（兜底链第 1 环） | 否 | 顶层 mdc 缺失时回退 `short_term`/`medium_term`/`instrument_context`/`data_collection_provenance` 等分支——功能不丢，仅兜底链少一环；canonical 行语义可接受，**不改** |
| `extract_report_industry` mdc 分支 (L584) | 否 | **是**（第 4 候选） | 否 | 同上——mdc 是行业判定的 7 候选之一，缺失时走 `instrument_context`/quadrant/provenance 等 |

## 三、待合入候选分支（非本卡文件，标注合入前置条件）

| 候选 | 所在分支 | 读 `horizons` | 读顶层 mdc | 经兼容层 | 处置 |
|------|---------|--------------|-----------|---------|------|
| `scripts/phase2/weekly_report.py`（DAV-1481 P5 周报） | `agent/agent/818d0fa66181` / `a077cd3473f5` | **是**（L514 `r["horizons"][MEDIUM]`） | 间接 | 否 | **合入前必须接 `result_data_compat_view`**，否则 canonical 行拿不到 `horizons.MEDIUM` → 周报错判。已在对应卡标注 |
| `scripts/dav1507_exit_progress.py`（DAV-1507，同 #7） | `agent/agent/f474b4c79293-v6` | 否 | 否 | 权威档 | 无需改 |
| DAV-1531 偏空诊断 | — | — | — | — | 只读诊断分析，结论落 DECISIONS.md，**未交付可执行脚本**，无读取点 |

## 四、写路径（不在本卡读取点审计范围，归 B-3）

| 入口 | 说明 |
|------|------|
| `scripts/backfill_tplus5_shadow.py` `backfill_report` 镜像 stamp | 读 `target["horizons"]` 是为了写镜像——写路径，已在 **DAV-1572 (B-3)** 删除镜像写入，只写 `*_term` |

## 结论

- 主干内**所有**读 `result_data` 的入口已覆盖：要么读权威 `*_term`（天然兼容 canonical），要么经 `result_data_compat_view` 重建虚拟键。**无遗漏的直接读 `horizons`/顶层 mdc 且不经兼容层的主干入口**。
- 唯一需要"改"的是 `weekly_report.py`（DAV-1481 候选，他卡分支）——合入前接兼容层，已在该卡标注，不属于本卡文件白名单。
- `shadow_credit` 的 mdc 兜底链在 canonical 行少一环属可接受语义（有 `*_term`/instrument/quadrant 等同级兜底），不引入兼容层以保持测量层对权威档的直接读取。

审计人：资深开发1 · 基线 `fbdbb8e5` · 2026-10-07
