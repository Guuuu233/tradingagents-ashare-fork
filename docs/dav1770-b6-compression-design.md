# DAV-1770 B-6a：`reports.result_data` 压缩存储设计（方案甲：压缩列 + 统一解码层）

口径声明：本文档只做设计，不改代码。测量与清点基于 `/private/tmp/ta-snapshot/current.db`（2026-10-09 快照，5,834,317,824 B）与主干 `codex/dav-4-p2a-trunk`@`082be04`。解释器 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20，zstandard 0.23.0，`env -u PYTHONPATH`）。内存口径 ≤4 GiB。

上游事实（DAV-1769 评估，全文复述要点）：

- `reports` 2,410 行；`result_data` 非空 1,625 行合计 **5.28 GiB = 库 97.2%**；中位 1.53 MiB、p95 10.2 MiB、max 13.0 MiB。
- zstd L9 无字典：中位压缩比 **27.08×**（p95 58.68），压缩耗时中位 **7.22 ms**（dual-horizon 13.65 ms / p95 35.67 ms），解压中位 **0.46 ms**（p95 1.29）。
- 字典在 MiB 级大 JSON 上边际收益仅 +1–2 点（L9 27.08→29.08），本设计**不用字典**（避免字典丢失导致历史数据不可解、版本管理负担）。
- 热路径 `json_extract`×35（`load_post_gate_fragments`）压缩后失效，必须改造（本文 §3）。

---

## 1. 目标与非目标

目标：把 `result_data` 的物理存储改为 zstd-9 压缩形态，库文件 ~5.8 GB → 预期 0.3–0.6 GiB；月增量 ~14 GB → ~0.5 GB。所有读路径经同一解码入口。可一键回退。

非目标：不动 `reports` 表外任何表；不动 API 出参 schema；不改 `result_data` 的 JSON 语义（callers 拿到的仍是 dict）；不做冷热分层归档（那是方案乙，已被总控 10-10 令排除）。

## 2. Schema 变更

### 2.1 新列

| 列名                  | 类型                                                         | 说明                                                  |
| --------------------- | ------------------------------------------------------------ | ----------------------------------------------------- |
| `result_data_zst`     | `BLOB`（SQLite `LargeBinary`；PG `BYTEA`；MySQL `LONGBLOB`） | zstd-9 压缩后的 UTF-8 JSON 字节流，无字典、无外层信封 |
| `result_data_zst_len` | `INTEGER`                                                    | 压缩前字节数（解压校验 + 审计用，可省一次解压）       |

- 压缩帧内**不嵌自定义 magic/版本头**：zstd 帧本身有 magic `0x28B52FFD`，解码端用「`result_data_zst IS NOT NULL` → 解压」判定；版本演进靠列存在性 + `PRAGMA user_version`（见 §6）。
- 旧列 `result_data`（`JSON`）**保留不删**，迁移期双列并存（见 §4）。
- `result_data='null'`（4 B）行**不迁移**——压缩反而变大；统一由解码层把 `NULL/空` 视作无数据。

### 2.2 列表页直读列（`load_post_gate_fragments` 35 字段的物理化）

压缩后 SQL 层 `json_extract` 不可用。方案：把 35 个 json_extract 路径在**写入时同步物化为普通列**，列表页直接 `SELECT` 这些列，不解压、不读 `result_data_zst`。

全部新增列放 `reports` 表本身（不建旁表——旁表 join 会把 1 次主键查询变 2 次，且 35 列全是小标量/小 JSON 片段，行宽增加 <2 KiB，远小于原 `result_data` 均 2 MiB）。

| 新列名                  | SQL 类型      | 来源 json 路径                                                                                                             | 落列形态                     |
| ----------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| `pg_st_trade_action`    | `VARCHAR(32)` | `$.short_term.trade_action`                                                                                                | 标量直存                     |
| `pg_st_analysis_status` | `VARCHAR(32)` | `$.short_term.analysis_status`                                                                                             | 标量                         |
| `pg_st_risk_status`     | `VARCHAR(32)` | `$.short_term.risk_status`                                                                                                 | 标量                         |
| `pg_st_direction`       | `VARCHAR(16)` | `$.short_term.direction`                                                                                                   | 标量                         |
| `pg_st_decision_status` | `JSON`        | `$.short_term.decision_status`                                                                                             | 小 dict，json 直存           |
| `pg_st_reason_codes`    | `JSON`        | `$.short_term.reason_codes`                                                                                                | list，json                   |
| `pg_st_gate_status`     | `VARCHAR(32)` | `$.short_term.price_basis_gate.status`                                                                                     | 标量（gate 对象只取 status） |
| `pg_st_status`          | `VARCHAR(20)` | `$.short_term.status`                                                                                                      | 标量                         |
| `pg_st_confidence`      | `INTEGER`     | `$.short_term.confidence`                                                                                                  | 标量                         |
| `pg_st_target_price`    | `FLOAT`       | `$.short_term.target_price`                                                                                                | 标量                         |
| `pg_st_stop_loss_price` | `FLOAT`       | `$.short_term.stop_loss_price`                                                                                             | 标量                         |
| `pg_st_pre_gate_action` | `VARCHAR(32)` | `$.short_term.pre_gate_trade_action`                                                                                       | 标量                         |
| `pg_st_manager_action`  | `VARCHAR(32)` | coalesce(`$.short_term.manager_verdict.trade_action`, `$.short_term.investment_debate_state.manager_verdict.trade_action`) | 写入时已解析的单一标量       |
| `pg_mt_*`（同上 13 列） | 同上          | `$.medium_term.*` 同构                                                                                                     | 同上                         |
| `pg_top_reason_codes`   | `JSON`        | coalesce(`$.decision_status.reason_codes`, `$.reason_codes`) 取先非空                                                      | json list                    |
| `pg_confidence`         | `INTEGER`     | `$.confidence`                                                                                                             | 标量                         |
| `pg_probability`        | `FLOAT`       | `$.probability`                                                                                                            | 标量                         |
| `pg_target_price`       | `FLOAT`       | `$.target_price`                                                                                                           | 标量                         |
| `pg_stop_loss_price`    | `FLOAT`       | `$.stop_loss_price`                                                                                                        | 标量                         |

合计 **31 列**（st 13 + mt 13 + top 5；`manager_action` 双侧已在写入时合并为单列，`top_reason_codes` 两个路径合并为单列——比逐路径 35 列少 4 列且语义等价，见下）。

设计说明：

- `_POST_GATE_FRAGMENT_PATHS` 里 `st_ids_manager_action` / `mt_ids_manager_action` 在 fragment 组装时本就 `or` 合并进 `manager_verdict.trade_action`（`report_service.py:3017-3020`），物化为单列无损。
- `top_ds_reason_codes` / `top_reason_codes` 在 fragment 组装时也是「先非空胜出」（`:3062-3067`），单列 `pg_top_reason_codes` 等价。
- 类型按现有列对齐：`confidence` 现列 `Integer`（`api/database.py:486`）、`probability/target/stop` `Float`；`decision_status`/`reason_codes` 在 `json_extract` 下本就返回 JSON 字符串、调用端 `_js()` 再 `json.loads`——物化列用 `JSON` 类型让 SQLAlchemy 直接给 dict/list，`_js()` 分支可删。
- **写入点**：`create_report`（`report_service.py:2530+` 更新分支 `:2631` 新建分支 `:2783`）与 `update_report_partial`（`:2351+`）在 `db_report.result_data = canonical_result_data` 同一事务内，调用同一函数 `_populate_post_gate_columns(db_report, canonical_result_data)` 一次性赋值 31 列；`finalize_orphan_report`（`:2443`）不写 result_data，无需同步。
- 历史存量行的物化列在迁移回填时一并补（见 §5）。

### 2.3 查询重写（列表页不解压）

- `load_post_gate_fragments` 改为 `SELECT id, pg_st_*, pg_mt_*, pg_top_*, pg_confidence, ...` 直取 31 列，fragment 组装逻辑不变（列名→frag 键一一映射，去掉 `_js()`）。
- `get_reports_by_user` / `get_latest_reports_by_symbols` 不变——`REPORT_SUMMARY_COLUMNS`（`:33-57`）本就不含 `result_data`；`_report_matches_horizon`（`:3091`）在 `horizon` 过滤时才读整份 `result_data`，该路径走解码层（§3）不走进物化列。

### 2.4 `_ensure_report_schema` 挂载

31+2 个新列全部加进 `_ensure_report_schema`（`api/database.py:107+`）的 `ordered_columns`/`_column_ddl` 映射，启动时幂等 `ALTER TABLE ADD COLUMN`——与现有 `analysis_status`/`trade_action` 等列同一迁移机制，不引入 Alembic。

## 3. 解码层唯一入口

### 3.1 形态：ORM `TypeDecorator`

新建 `tradingagents/storage/compressed_json.py`：

```python
class ZstdJSON(TypeDecorator):
    impl = LargeBinary            # SQLite: BLOB
    cache_ok = True
    _CTX = zstandard.ZstdCompressor(level=9)   # module-level, 复用
    _DCTX = zstandard.ZstdDecompressor()

    def process_bind_param(self, value, dialect):   # dict -> bytes
        if value is None: return None
        return self._CTX.compress(json.dumps(value).encode("utf-8"))   # 必须 ensure_ascii=True 默认参数——与 SQLite Column(JSON) 落库字节一致，否则 daily_snapshot_ledger 已封 sha256 全链失配

    def process_result_value(self, value, dialect): # bytes -> dict
        if value is None: return None
        return json.loads(self._DCTX.decompress(value, max_output_size=…))
```

- `ReportDB.result_data` 的列定义从 `Column(JSON)` 改为「绑定到物理列 `result_data_zst` 的 `ZstdJSON`」——**对外属性名 `result_data` 不变**，所有 ORM 读点（calibration×7、email×1、main.py:6181-6199、report_service get/update/finalize、backfill_report_industry:325-328）**零改动**，`db_report.result_data` 拿到的仍是 dict。SQLAlchemy 支持 `Column('物理名', type, key='属性名')`；明文 `result_data` 物理列同时保留为 `result_data_legacy` 只读影子（见 §4）。

### 3.2 读路径调用面（全部经 ZstdJSON 透明解压）

```
报告详情 /v1/reports/{id}          api/main.py:6168-6199
列表页 post-gate fallback          api/main.py:6131,6154,7915 → load_post_gate_fragments (改走 §2.2 物化列, 不解压)
horizon 过滤 latest-by-symbols     report_service.py:3124 (整列读 → ORM 解压)
get_report (single)                report_service.py:2845 (ORM → 解压, detached shadow 逻辑不变)
create/update/finalize 写路径       report_service.py:2336,2351,2443,2530 (bind_param 压缩)
calibration_service ×7             ORM 属性读 → 解压
email_report_service:141           ORM 属性读 → 解压
scripts/* 原生 SQL                 统一加 decode helper (见 §3.3)
```

### 3.3 原生 SQL / json_extract 读点的处理

`tradingagents/storage/compressed_json.py` 同时导出**模块级 helper**（同一 `.py`，同一 `_DCTX`）：

```python
def decode_result_data(raw: bytes | str | None) -> dict | None: ...
def encode_result_data(value: dict | None) -> bytes | None: ...
```

原生 SQL 点全部改为 `SELECT result_data_zst` + `decode_result_data(row[0])`：

| 文件                                                                                                                                                     | 现状                                                                           | 改造                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/backfill_tplus5_shadow.py`（533,555,666,842,861）                                                                                               | `SELECT result_data` 5 处                                                      | 换列名 + `decode_result_data()`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `scripts/dav1312_pricegate_audit.py:297`                                                                                                                 | `select … result_data`                                                         | 同上                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `scripts/dav1507_exit_progress.py:121`                                                                                                                   | `json_valid(result_data)`                                                      | 压缩后无 SQL 层等价物；改拉 `result_data_zst` 后 `decode_result_data() is not None` 判定                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `scripts/phase2/daily_snapshot_ledger.py`（643 `SELECT result_data`、680 `build_record(row,row["result_data"])`、768 `SELECT result_data … WHERE id=?`） | 逐行 `json.loads(result_data_raw)` + `sha256(result_data_raw.encode("utf-8"))` | 换 `SELECT result_data_zst` + `decode_result_data()`。sha256 口径：`result_data_sha256_at_seal` 是对**解压后的 UTF-8 JSON 字节**取 hash——SQLite `Column(JSON)` 落库字节 = `json.dumps` 序列化文本，与 `encode_result_data` 内部 `json.dumps(value).encode()` **不是同一序列化路径**（ensure_ascii/separators 可能有差）。**设计规定 `encode_result_data` 必须与 SQLAlchemy JSON 序列化器逐字节一致**（用 `dialect.json_serializer` 同一参数：SQLite 默认 `json.dumps` 全默认参数、ensure_ascii=True、分隔符含空格），使「解压字节 == 原 result_data 文本字节」成立——已封账的 `result_data_sha256_at_seal` 与 `baseline` 校对在压缩切换后依然可核对，账目链不断。B-6b 必须以实测验证该等式（抽 ≥100 行断言 `decompress(zst)==SELECT result_data` 原字节） |
| `scripts/replay_dav1192_semantic_coverage.py`（41-51）                                                                                                   | `json_extract`×7                                                               | 改 `decode_result_data` + Python dict 路径                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `scripts/replay_dav1338/1343/1351`、`diagnose_h1b_v1_field_coverage.py`、`backfill_report_industry.py`（ORM 读写）、`tradingagents/eval/v03_return_measure.py:1934`（原生 `SELECT … result_data`）                                       | `SELECT result_data` / ORM                                                     | ORM 点零改；原生 SQL 换 decode helper                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `work/` ~25 个一次性脚本                                                                                                                                 | `SELECT result_data`                                                           | **不改**：压缩切换后这批脚本对明文库仍可用；在 README/脚本头标注「仅适配明文 result_data 库」；需要跑新库时补 decode（每个 <20 行机械替换）                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

`result_data`（明文）列在迁移期继续被 `load_post_gate_fragments` 之外的 legacy SQL 读——迁移完成后这些点已全部改走 decode helper，明文列只作回退保险。

## 4. 迁移期读写策略（双列并存窗口）

四个阶段，每阶段可独立进/退：

| 阶段                    | 写路径                                                                                                | 读路径                                     | `result_data`（明文） | `result_data_zst` |
| ----------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------ | --------------------- | ----------------- |
| P0 现状                 | 只写明文                                                                                              | 只读明文                                   | ✓                     | —                 |
| P1 加列+双写（B-6b）    | 明文+压缩列同时写；31 物化列同步写                                                                    | 仍读明文（TypeDecorator 尚未切换或走明文） | ✓ 权威                | ✓ 镜像            |
| P2 迁移回填（B-6c）     | 后台脚本按 `id` 分批把存量明文压入 `result_data_zst` + 补物化列；幂等 `WHERE result_data_zst IS NULL` | 仍读明文                                   | ✓ 权威                | ✓ 补齐中          |
| P3 切读（B-6b 收尾）    | 写压缩列为主；明文列**继续写**（保险）                                                                | TypeDecorator 生效，读压缩列               | ✓ 影子                | ✓ 权威            |
| P4 停写明文（观察期后） | 只写压缩列+物化列；明文列置 NULL                                                                      | 读压缩列                                   | 只读保留              | ✓ 权威            |

- **P1→P3 窗口**内读路径仍以明文为权威，压缩列只是「写上去但还没人读」——任何压缩 bug 在 P3 前都不会炸读面。
- **P3 切读**是单点切换：`ZstdJSON` 绑定到 `result_data_zst` 的 migration 随 `_ensure_report_schema` 上线 + 一个 `REPORT_STORAGE_MODE` env（`plaintext|dual|compressed`）控制读哪列。env=plaintext 可瞬间回退。
- **停写明文**（P4）在至少 7 天观察、且 `SELECT count(*) FROM reports WHERE result_data IS NOT NULL AND result_data_zst IS NULL = 0` 持续为 0 后才执行；执行后明文列数据**保留不删**（见 §6 回退）。
- 读路径在 P3 后遇到「`result_data_zst IS NULL AND result_data IS NOT NULL`」的边缘行（P4 后新写不再出现；迁移期漏网）——TypeDecorator 的 `process_result_value` 只在绑定的压缩列上工作；这种行由迁移脚本兜底补压，或由 helper 提供 `SELECT COALESCE 明文` 的 fallback 查询，设计上规定：**P3 切换前必须把 `result_data IS NOT NULL AND result_data_zst IS NULL` 的行数降到 0**。

## 5. 迁移脚本要点（B-6c 输入）

- 分批：`SELECT id, result_data FROM reports WHERE result_data_zst IS NULL AND result_data IS NOT NULL ORDER BY created_at LIMIT N`，每批 N=50 提交一次；对 1,625 行 ≈ 33 批。
- 每行：`zst = encode_result_data(json.loads(result_data))` → `UPDATE reports SET result_data_zst=?, result_data_zst_len=?, <31 物化列>=… WHERE id=?`。物化列回填复用 §2.2 的 `_populate_post_gate_columns` 逻辑（同一函数，输入 dict 输出列值 dict）。
- 校验：`decompress(zst)==原文 utf-8` 逐行比对后再 commit；失败行记入 `b6c_migration_failures` 台账，不阻塞批。
- 内存口径：流式 LIMIT 分页 + 每行解压后即弃，峰值 = 单行 max 13 MiB 明文 + ~0.5 MiB 压缩 + interpreter ≪ 4 GiB。

## 6. 回退方式

| 触发点              | 动作                                                                                                                                 | 数据状态                                                     |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| P1/P2 期间出问题    | `REPORT_STORAGE_MODE=plaintext`（或回退代码版本）→ 读回明文列；压缩列停止写/可 `ALTER TABLE DROP COLUMN result_data_zst` + 31 物化列 | 明文列始终权威，零数据损失                                   |
| P3 已切读后发现 bug | `REPORT_STORAGE_MODE=plaintext` 立即回退——明文列因仍在双写而与新列一致                                                               | 双写窗口内明文=压缩内容，回退无缺口                          |
| P4 停写明文后要回退 | 代码回退到 P2 行为（重新打开明文双写）→ 补跑迁移脚本把 `result_data IS NULL AND result_data_zst IS NOT NULL` 的行解压回明文          | 明文列在停写期间为空缺的行由迁移脚本补回，RPO=脚本补跑完成点 |
| 压缩格式整体废弃    | `ALTER TABLE reports DROP COLUMN result_data_zst, result_data_zst_len, <31 物化列>`                                                  | 回到纯明文，物化列随压缩方案一并删除                         |

`PRAGMA user_version` 记录 schema 版本（如 `2026101001`）作审计标记；**列存在性判定仍以 `_ensure_report_schema` 的 `insp.get_columns` 为准**（现有幂等加列路径不变，user_version 不驱动 DDL、只作只读审计戳）。回退代码版本时 schema 列已存在但不被使用，无副作用。

## 7. 月增量核实（本卡要求第 5 项）

口径：近 30 个自然日（`created_at >= 2026-09-10`）`reports` 行，按 `status='completed'` 且有 `result_data` 计。**快照库无独立的 job/run 来源表**（`reports` 表无 `request_source` 列，`scheduled_analyses.last_report_id` 只记最近一次且历史为 0 命中），来源拆分只能依据 `created_at` 与 `trade_date`/`analysis_baseline_date` 的滞后差 + `horizon_run_metadata.resolution_source` 推断：

| 来源（推断口径） | 判定规则                                                                                                                                   | 份数    | result_data 字节 | 占比  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------- | ---------------- | ----- |
| 回填/历史重放    | `created_at − trade_date ≥ 3 天`                                                                                                           | **651** | **3.98 GiB**     | 78.3% |
| 每日批/当日手动  | `created_at − trade_date ≤ 2 天`（无法再细分定时 vs 手动——`reports` 无 `request_source`，`custom_prompt_snapshot.enabled` 在两类里都出现） | **181** | **1.10 GiB**     | 21.7% |
| 失败/未产数据    | `status != completed`                                                                                                                      | 170     | ~0               | —     |

近 30 天合计 **5.08 GiB**，折合 **~5.1 GiB/月（≈5.5 GB/月）**。

与 DAV-1769「月增 ~14 GB」口径不同的原因：1769 用的是「**最近 14 个有数据的自然日**均值外推」（460 MiB/日 × 30 ≈ 13.8 GB/月），而该窗口恰撞上 09-26、10-01~04 的大规模回填洪峰（单日最高 1.06 GB）。按同一 14 日外推口径在本快照复算：近 14 天（09-26 起）4.49 GiB/14 天 ≈ **9.6 GiB/月**；按 30 天全窗口则 **5.1 GiB/月**。

**结论（两组数并呈）**：稳态（刨去 09-18 集中回填与 10-01~04 洪峰）每日批+手动 ≈ 1.10 GiB/30 天 ≈ **1.1 GiB/月**是常态下限；含回填的真实增长在 **5–14 GB/月**区间，取决于回填作业强度——14 GB 是「回填洪峰月」口径，5 GB 是「近 30 天实际均值」口径。压缩后（L9, ~27×）月增量分别 ≈ **0.2–0.5 GiB/月**。

## 8. 风险与开放项

1. **物化列与 result_data 的写一致性**：31 列必须在 `create_report`/`update_report_partial` 与 `result_data` 同事务写入；已有 `ensure_*_persisted` 系列函数在 `result_data` 落库**之后**再回填 JSON 内部字段（`:2395-2405`），物化列必须在所有 ensure 完成后最后一步填充，否则取到旧值。B-6b 实现时把 `_populate_post_gate_columns` 放在 commit 前最后一行。
2. **`update_report_partial(result_data=...)` 路径**：`main.py` 多处（2592,2631,4794）以 `result_data=failed_result` 更新——物化列也要同步清/写，不能只改 create。
3. **回填期间的双写放大**：P1–P4 窗口每份报告写 ≈2× 字节（明文+压缩），日写 ~460 MiB → ~470 MiB，可忽略；但回填脚本本身写 5.28 GiB→~200 MiB 压缩列，属一次性。
4. **`daily_snapshot_ledger` 的 sha256 口径**：`result_data_sha256_at_seal` 锁定的是「明文 JSON 字节」的 hash。`encode_result_data` 必须与 SQLAlchemy SQLite JSON 序列化器逐字节一致（`json.dumps` 默认参数），否则已封账 hash 全部失配——B-6b 交付时必须附 `decompress(zst)==原文` 抽样证据（见 §3.3 表内注）。
5. **来源拆分精度**：`reports` 无 `request_source`/`job_id` 列，定时 vs 手动只能推断；若总控要求精确拆分，需要在 B-6b 顺带新增 `request_source VARCHAR(32)` 列（成本 ~10 行 schema + 2 写点），属本设计外增量，已在 §7 标注推断口径。

6. **复审意见落实**（DAV-1774 ✅通过，🟢×5）：①§3.1 伪码 `ensure_ascii` 已改为默认参数（与 §3.3/§8.4 sha256 一致性要求对齐）；②`v03_return_measure.py:1934` 原生 `SELECT result_data` 已补入 §3.3 改造表；③`PRAGMA user_version` 降级为只读审计戳、列存在性仍以 `_ensure_report_schema` 现有 `insp.get_columns` 幂等路径为准（§6）；④`work/` 一次性脚本建议在 `work/README` 或脚本头统一加一行指向 `compressed_json.decode_result_data`（B-6b 顺手加，不阻塞）。

## 9. 交付与衔接

- 本文档：`docs/dav1770-b6-compression-design.md`（分支 `agent/1/dav1770-b6a-design`，只推分支不碰主干）。
- 批准后：**B-6b**（写路径双写 + TypeDecorator + 物化列 + `_ensure_report_schema` + env 开关）与 **B-6c**（迁移回填脚本）可并行开工——B-6c 依赖 B-6b 的 `compressed_json.encode/decode` 与 `_populate_post_gate_columns` 函数签名，接口即 §3.1/§2.2；**B-6d** 等 B-6b 接口冻结。
