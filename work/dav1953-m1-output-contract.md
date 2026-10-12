# DAV-1953 M1 输出契约 b2.v1 实现说明

依据：`docs/plans/phase2-midterm/m1-output-contract-spec.md`（DAV-1949 审定版
+ 总控四项裁定，转贴于 1949 评论）。只推 agent 分支，第一阶段验收前不合入。

## 改动清单

| 文件 | 变更 |
|---|---|
| `tradingagents/eval/b2_contract.py` | 新。契约核心：严格整数判定、五态互斥分类、重复块/重复键检测、as_of 时点裁定、基率三联（值/状态/元数据）、账本 v1/v2 读取助手、三处读数核对、批次计数器 |
| `tradingagents/eval/b2_base_rate_provider.py` | 新。`p_base_rel_t40.parquet`（m1_base_rate 产物）惰性加载 + sha256 指纹；env `TA_M1_BASE_RATE_PARQUET` 指路径、`TA_M1_BASE_RATE_DISABLE=1` 强制 `source_unavailable` |
| `tradingagents/agents/managers/research_manager.py` | `capture_manager_forecast_input` 增 medium 分支（contract 键、`raw_output_sha256`、`manager_prompt_sha256`、基率载荷）；`_blocked_manager_payload` 中线守卫拦截写 `node_failed`；spec §2.2 镜像键落到 `manager_verdict` 顶层 |
| `tradingagents/prompts/zh.py` / `en.py` | 新增 `_MANAGER_MEDIUM_PROBABILITY_CONTRACT` 文本块；VERDICT 示例未加概率数字（守规格「示例不出现具体概率数字」） |
| `api/services/report_service.py` | `parse_horizon_forecast` 增 b2 读数：状态信任节点分类但本地复校整数域；预契约中线档显式 `not_sealed` |
| `scripts/phase2/daily_snapshot_ledger.py` | `ledger_version=2`；`horizons.medium` 显式提取 8 个契约键；`versions.manager_prompt_sha256_by_horizon.medium` / `probability_contract_version_by_horizon.medium`；历史 v1 不回填 |
| `scripts/phase2/m1_eval.py` | `load_frame` 直接吃 P4 账本 JSONL（v1/v2 混合）；v2 行 `p_rel_t40`→`q`、`p_base_rel_t40`→`q_base`；新增 `paired_base_eval`（D-075 配对增量 IC_diff + NW60/120），run() 有 q_base 时自动输出 `base_increment` |
| `scripts/phase2/m2_periodic_report.py` | `_b2_medium_prob`：v2 只认 `p_rel_t40`（valid 才取值，不回退旧 probability），v1 照旧；D3/M4 两条截面路径统一走它 |
| `scripts/phase2/weekly_report.py` | `b2_medium_fields` + 概率来源/封存状态披露；`_m_prob` 优先 `p_rel_t40` |
| `tradingagents/eval/shadow_portfolio.py` | `ledger_probability` v2 档优先 `p_rel_t40`，v2 非 valid 不回退旧 probability（口径不同不混用） |
| `tests/test_dav1953_m1_contract.py` | 60 项：严格边界/错误类型、重复块与重复键、node_failed/not_run、as_of 判定表、基率单位/缺失、混合账本哈希链、三处读数、压缩读回、shadow/m1_eval/报告读端兼容、批次计数 |

## 关键裁定落点

- **as_of 时点**：`maturity_cutoff()` — as_of ≥ T 15:00（Asia/Shanghai）→ 成熟截止 T；否则 T−1；as_of 缺失 → `pit_unverified`。裸日期一律当 T 日盘前。
- **单位**：`p_base_rel_t40` 写百分点 0–100（×100 只换这一次，不取整）；模型 `p_rel_t40` 仍是严格整数 1–99；评价侧 `q_base = p_base / 100`。
- **失败分类**：缺字段逐次 fail-close 计 missing（批次按签收上限 40 次 ≤1）；结构性掉字段（报告/账本读数不通）由 `verify_three_readings` 和 `report_readback`/`ledger_readback` 口径暴露为工程硬失败，不改成模型 missing。
- **不兜底**：非 valid 一律 null；不补 50、不补方向映射、不从短线/顶层借值；v1 档标记 `not_sealed` 不伪造模型 missing。
- **重复块/键**：`_get_marker_parse_status` 拒绝多块；块内任何层级重复键 → malformed `duplicate_key`。
- **1450 重底**：契约文本为独立 `_MANAGER_MEDIUM_PROBABILITY_CONTRACT` 前缀块，重底时核对 `forecast_input.manager_prompt_sha256` 并重做 20×2（不沿用两边各自结果）——本卡不执行实测。

## 未做（边界内）

不部署、不升级 P4 定时任务检出、不调模型、不碰生产库。`TA_M1_BASE_RATE_PARQUET`
未配置时中线档照常运行，基率记 `source_unavailable`（显式状态，非伪造值）。
20×2 重复测量与两种代理 × 三种存储模式复审在验收阶段进行。
