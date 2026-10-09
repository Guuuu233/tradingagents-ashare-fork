# DAV-1770 B-6a：`reports.result_data` 压缩存储设计（方案甲：压缩列 + 统一解码层）

口径声明：本文档只做设计，不改代码。测量与清点基于 `/private/tmp/ta-snapshot/current.db`（2026-10-09 快照，5,834,317,824 B）与主干 `codex/dav-4-p2a-trunk`@`082be04`。解释器 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20，zstandard 0.23.0，`env -u PYTHONPATH`）。内存口径 ≤4 GiB。

上游事实（DAV-1769 评估，全文复述要点）：

- `reports` 2,410 行；`result_data` 非空 1,625 行合计 **5.28 GiB = 库 97.2%**；中位 1.53 MiB、p95 10.2 MiB、max 13.0 MiB。
- zstd L9 无字典：中位压缩比 **27.08×**（p95 58.68），压缩耗时中位 **7.22 ms**（dual-horizon 13.65 ms / p95 35.67 ms），解压中位 **0.46 ms**（p95 1.29）。
- 字典在 MiB 级大 JSON 上边际收益仅 +1–2 点（L9 27.08→29.08），本设计**不用字典**（避免字典丢失导致历史数据不可解、版本管理负担）。
- 热路径 `json_extract`×35（`load_post_gate_fragments`）压缩后失效，必须改造（本文 §3）。

---

## 1. 目标与非目标

目标：把 `result_data` 的物理存储改为 zstd-9 压缩形态，**逻辑占用** 5.28 GiB → ~0.2 GiB；**库文件**在 P5 `VACUUM` 空间回收（见 §4 P5 / §5 末）后 ~0.4–0.7 GiB（SQLite 下 UPDATE 置 NULL / DROP COLUMN 只进 freelist，不回收文件大小——不经 VACUUM 库文件仍 ~5.4 GiB）。月增量 ~14 GB → ~0.5 GB。所有读路径经同一解码入口。可一键回退。

非目标：不动 `reports` 表外任何表；不动 API 出参 schema；不改 `result_data` 的 JSON 语义（callers 拿到的仍是 dict）；不做冷热分层归档（那是方案乙，已被总控 10-10 令排除）。

## 2. Schema 变更

### 2.1 新列

| 列名                  | 类型                                                         | 说明                                                  |
| --------------------- | ------------------------------------------------------------ | ----------------------------------------------------- |
| `result_data_zst`     | `BLOB`（SQLite `LargeBinary`；PG `BYTEA`；MySQL `LONGBLOB`） | zstd-9 压缩后的 UTF-8 JSON 字节流，无字典、无外层信封 |
| `result_data_zst_len` | `INTEGER`                                                    | 压缩前字节数（解压校验 + 审计用，可省一次解压）       |

- 压缩帧内**不嵌自定义 magic/版本头**：zstd 帧本身有 magic `0x28B52FFD`，解码端用「`result_data_zst IS NOT NULL` → 解压」判定；版本演进靠列存在性 + `PRAGMA user_version`（见 §6）。
- 旧列 `result_data`（`JSON`）**保留不删**，迁移期双列并存（见 §4）。
- `result_data='null'`（4 B，实测 318 行）**不迁移**——压缩反而变大；统一由解码层把 `NULL/空` 视作无数据。**迁移/校验三处口径统一为可执行判据 `result_data IS NOT NULL AND result_data <> 'null'`**（§4 P3 前置条件、§5 批次 WHERE、本排除规则同口径——`<> 'null'` 精确等值排除已足够覆盖这 318 行，不再叠加 `length() > 4` 以免与两处 SQL 分叉），否则这 318 行恒满足 `result_data IS NOT NULL AND result_data_zst IS NULL`、P3 前置条件永远不可满足。**语义等价性**：P3 后这 318 行经解码层按 `None` 处理——与现状 `json.loads('null') is None` 完全等价，不得写成 `{}`。

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
| `pg_top_reason_codes`   | `JSON`        | `$.decision_status.reason_codes` 与 `$.reason_codes` 两路径合并为一列承载（合并依据见设计说明第 2 条）                            | json list                    |
| `pg_confidence`         | `INTEGER`     | `$.confidence`                                                                                                             | 标量                         |
| `pg_probability`        | `FLOAT`       | `$.probability`                                                                                                            | 标量                         |
| `pg_target_price`       | `FLOAT`       | `$.target_price`                                                                                                           | 标量                         |
| `pg_stop_loss_price`    | `FLOAT`       | `$.stop_loss_price`                                                                                                        | 标量                         |

合计 **31 列**（st 13 + mt 13 + top 5；`manager_action` 双侧已在写入时合并为单列，`top_reason_codes` 两个路径合并为单列——比逐路径 35 列少 4 列；两组合并的等价性论证与重建规则见设计说明，manager_action 为 `or` 合并无损，top_reason_codes 为并列双槽写入、单列承载+双槽重建）。

设计说明：

- `_POST_GATE_FRAGMENT_PATHS` 里 `st_ids_manager_action` / `mt_ids_manager_action` 在 fragment 组装时本就 `or` 合并进 `manager_verdict.trade_action`（`report_service.py:3030-3033`（st）/`:3051-3054`（mt）），物化为单列无损。
- `top_ds_reason_codes` / `top_reason_codes` 两路径在 fragment 组装时**并列写入两个不同的输出槽**（`report_service.py:3062-3067`：`frag["decision_status"] = {"reason_codes": top_ds_rc}` 与 `frag["reason_codes"] = top_rc` 是两个并列 `if`，非 `or`/if-else 先非空胜出）。实测快照库两路径同时非空 893 行、值全等（diff=0），`ds_only`/`top_only` 各 0 行——**值层面单列可承载**。方案 A（本文档采用）：保留单列 `pg_top_reason_codes`，但重建规则必须逐字对齐现状——**对 `pg_top_reason_codes` 非空的行，同时填 `frag["decision_status"] = {"reason_codes": v}` 与 `frag["reason_codes"] = v` 两个槽**（见 §2.3）。不能只建一个槽，否则 frag 结构与现状分叉（下游 `apply_post_gate_read_fallback` `:2880-2883` 的 `or` 短路只是碰巧一致的实现细节，不是设计保证）。
- 类型按现有列对齐：`confidence` 现列 `Integer`（`api/database.py:486`）、`probability/target/stop` `Float`；`decision_status`/`reason_codes` 在 `json_extract` 下本就返回 JSON 字符串、调用端 `_js()` 再 `json.loads`——物化列用 `JSON` 类型让 SQLAlchemy 直接给 dict/list，`_js()` 分支可删。**`_js()` 是 `load_post_gate_fragments` 内的嵌套函数，定义在 `report_service.py:3006`，调用点共 6 处：`:3020`/`:3021`/`:3041`/`:3042`/`:3062`/`:3065`**——删除时连同定义共 7 处一并清理（JSON 列给出的 dict 再进 `_js` 不会报错，属静默绕弯残留，不易被发现）。
- **写入点**：`create_report`（`report_service.py:2530+` 更新分支 `:2631` 新建分支 `:2783`）与 `update_report_partial`（`:2351+`）在 `db_report.result_data = canonical_result_data` 同一事务内，调用同一函数 `_populate_post_gate_columns(db_report, canonical_result_data)` 一次性赋值 31 列；`finalize_orphan_report`（`:2443`）不写 result_data，无需同步。
- 历史存量行的物化列在迁移回填时一并补（见 §5）。

### 2.3 查询重写（列表页不解压）

- `load_post_gate_fragments` 改为 `SELECT id, pg_st_*, pg_mt_*, pg_top_*, pg_confidence, ...` 直取 31 列，fragment 组装逻辑不变（列名→frag 键一一映射，`_js()` 定义 `report_service.py:3006` 及 6 处调用点 `:3020/:3021/:3041/:3042/:3062/:3065` 一并删除）。**重建规则（方案 A，逐字对齐 `:3062-3067` 现状）**：`pg_top_reason_codes` 非空的行须同时填 `frag["decision_status"] = {"reason_codes": v}` 与 `frag["reason_codes"] = v` 两槽——现状代码是两槽并列写入、非取其一，少建一槽即改变输出结构。
- `get_reports_by_user` / `get_latest_reports_by_symbols` 不变——`REPORT_SUMMARY_COLUMNS`（`:33-57`）本就不含 `result_data`；`_report_matches_horizon`（`:3091`）在 `horizon` 过滤时才读整份 `result_data`，该路径走解码层（§3）不走进物化列。

### 2.4 `_ensure_report_schema` 挂载

31+2 个新列全部加进 `_ensure_report_schema`（`api/database.py:107+`）的 `ordered_columns`/`_column_ddl` 映射，启动时幂等 `ALTER TABLE ADD COLUMN`——与现有 `analysis_status`/`trade_action` 等列同一迁移机制，不引入 Alembic。

## 3. 解码层唯一入口

### 3.1 形态：ORM `TypeDecorator`

新建 `tradingagents/storage/compressed_json.py`：

```python
import threading
import zstandard

_tls = threading.local()

def _cctx() -> zstandard.ZstdCompressor:          # 每线程一个 compressor（创建成本 ≪ 压缩成本）
    ctx = getattr(_tls, "cctx", None)
    if ctx is None:
        ctx = zstandard.ZstdCompressor(level=9)
        _tls.cctx = ctx
    return ctx

def _dctx() -> zstandard.ZstdDecompressor:        # 每线程一个 decompressor
    d = getattr(_tls, "dctx", None)
    if d is None:
        d = zstandard.ZstdDecompressor()
        _tls.dctx = d
    return d

class ZstdJSON(TypeDecorator):
    impl = LargeBinary            # SQLite: BLOB
    cache_ok = True

    def process_bind_param(self, value, dialect):   # dict -> bytes
        if value is None: return None
        return _cctx().compress(json.dumps(value).encode("utf-8"))   # 必须 ensure_ascii=True 默认参数——与 SQLite Column(JSON) 落库字节一致，否则 daily_snapshot_ledger 已封 sha256 全链失配

    def process_result_value(self, value, dialect): # bytes -> dict
        if value is None: return None
        return json.loads(_dctx().decompress(value, max_output_size=_max_out(value)))   # 上界：行级 result_data_zst_len（与压缩列同行的元数据列）；列缺省/为 NULL 时回退全局 MAX_RESULT_DATA_BYTES（设计值 64 MiB，> p100 13 MiB 留 4× 余量）
```

**线程安全（设计硬约束，DAV-1776 复审 🔴-1）**：`zstandard` 官方明确 `ZstdCompressor`/`ZstdDecompressor` 实例**非线程安全**（`backend_cffi.py:1776/3688`："assume instances are not thread safe unless stated otherwise"）。实测（锁定解释器，zstandard 0.23.0/cext）：8 线程×200 次共享 ctx **全部报错**（`Src size is incorrect`/`Destination buffer is too small`/静默 `MISMATCH`），重复运行出现 **`SIGSEGV`(139)/`Bus error`(138)**；改 `threading.local()` 后 8 线程×50 次 **0 错误**。服务是多线程的（`api/main.py` `ThreadPoolExecutor`/`run_in_executor`），报告读写会并发进入 `process_bind_param`/`process_result_value`——共享 ctx 轻则写事务回滚，重则解压**静默返回错误字节**落库造成数据污染，或直接进程崩溃。**因此压缩/解压上下文一律走模块级 `threading.local()` 每线程各持一份，严禁类级/模块级单例**；`_cctx()`/`_dctx()` 即唯一获取入口。

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

`tradingagents/storage/compressed_json.py` 同时导出**模块级 helper**，内部使用 §3.1 的 `_dctx()`/`_cctx()` **同一组 thread-local 上下文**（**不得**另建模块级单例 `ZstdDecompressor`/`ZstdCompressor`——线程安全理由同 §3.1）：

```python
def decode_result_data(raw: bytes | str | None) -> dict | None: ...
def encode_result_data(value: dict | None) -> bytes | None: ...
```

原生 SQL 点全部改为 `SELECT result_data_zst` + `decode_result_data(row[0])`：

| 文件                                                                                                                                                     | 现状                                                                           | 改造                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/backfill_tplus5_shadow.py`                                                                                                                                                      | `SELECT result_data` 5 处（**542, 564, 675, 851, 870**）+ `UPDATE reports SET result_data=?` 2 处（**563, 867**） | SELECT 换列名 + `decode_result_data()`；**两处 UPDATE 必须一并切到压缩列写 `result_data_zst=encode_result_data(...)` 或改走 ORM 属性赋值**——P3 后明文 UPDATE 对读路径不可见（影子写，静默失效）；`non_tplus5_bytes` 等校验函数输入改传解码后字节 |
| `scripts/dav1312_pricegate_audit.py:297`                                                                                                                 | `select … result_data`                                                         | 同上                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `scripts/dav1507_exit_progress.py:121`                                                                                                                   | `json_valid(result_data)`                                                      | 压缩后无 SQL 层等价物；改拉 `result_data_zst` 后 `decode_result_data() is not None` 判定                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `scripts/phase2/daily_snapshot_ledger.py`（643 `SELECT result_data`、680 `build_record(row,row["result_data"])`、768 `SELECT result_data … WHERE id=?`、**对账点 773**） | 逐行 `json.loads(result_data_raw)` + `sha256(result_data_raw.encode("utf-8"))` | 换 `SELECT result_data_zst` + `decode_result_data()`；**对账点 `:773` 的 `_sha256_bytes(row[0].encode("utf-8"))` 同步改为对 `decode_result_data` 后再序列化（`json.dumps` 默认参数）的字节取 hash**，否则对账全红。sha256 口径：`result_data_sha256_at_seal` 是对**解压后的 UTF-8 JSON 字节**取 hash——SQLite `Column(JSON)` 落库字节 = `json.dumps` 序列化文本，与 `encode_result_data` 内部 `json.dumps(value).encode()` **不是同一序列化路径**（ensure_ascii/separators 可能有差）。**设计规定 `encode_result_data` 必须与 SQLAlchemy JSON 序列化器逐字节一致**（用 `dialect.json_serializer` 同一参数：SQLite 默认 `json.dumps` 全默认参数、ensure_ascii=True、分隔符含空格），使「解压字节 == 原 result_data 文本字节」成立——已封账的 `result_data_sha256_at_seal` 与 `baseline` 校对在压缩切换后依然可核对，账目链不断。B-6b 必须以实测验证该等式（抽 ≥100 行断言 `decompress(zst)==SELECT result_data` 原字节） |
| `scripts/replay_dav1192_semantic_coverage.py`（41-51）                                                                                                   | `json_extract`×7                                                               | 改 `decode_result_data` + Python dict 路径                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `scripts/replay_dav1338/1343/1351`、`diagnose_h1b_v1_field_coverage.py`、`backfill_report_industry.py`（ORM 读写）、`tradingagents/eval/v03_return_measure.py:1934`（原生 `SELECT … result_data`）                                       | `SELECT result_data` / ORM                                                     | ORM 点零改；原生 SQL 换 decode helper                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `work/` 一次性脚本（口径说明：实测 `work/` 下含 `result_data` 的文件 **129 个，其中 `.py` 32 个**；本次修订前版本写「~25 个」是按「需改造的 .py 脚本」口径的约数，与 129 的总文件数口径不同，特此说明，不阻塞）                                                                                                                                 | `SELECT result_data`                                                           | **不改**：压缩切换后这批脚本对明文库仍可用；在 README/脚本头标注「仅适配明文 result_data 库」；需要跑新库时补 decode（每个 <20 行机械替换）                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

`result_data`（明文）列在迁移期继续被 `load_post_gate_fragments` 之外的 legacy SQL 读——迁移完成后这些点已全部改走 decode helper，明文列只作回退保险。

## 4. 迁移期读写策略（双列并存窗口）

四个阶段，每阶段可独立进/退（后补第五阶段 P5 空间回收，仅物理收缩、不改读写语义）：

| 阶段                    | 写路径                                                                                                | 读路径                                     | `result_data`（明文） | `result_data_zst` |
| ----------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------ | --------------------- | ----------------- |
| P0 现状                 | 只写明文                                                                                              | 只读明文                                   | ✓                     | —                 |
| P1 加列+双写（B-6b）    | 明文+压缩列同时写；31 物化列同步写                                                                    | 仍读明文（TypeDecorator 尚未切换或走明文） | ✓ 权威                | ✓ 镜像            |
| P2 迁移回填（B-6c）     | 后台脚本按 `id` 分批把存量明文压入 `result_data_zst` + 补物化列；幂等 `WHERE result_data_zst IS NULL` | 仍读明文                                   | ✓ 权威                | ✓ 补齐中          |
| P3 切读（B-6b 收尾）    | 写压缩列为主；明文列**继续写**（保险）                                                                | TypeDecorator 生效，读压缩列               | ✓ 影子                | ✓ 权威            |
| P4 停写明文（观察期后） | 只写压缩列+物化列；**新写/更新行的明文列置 NULL**（历史行明文**不批量清空**，永久保留作回退保险——见下行准入与 §6）                                                                 | 读压缩列                                   | 只读保留              | ✓ 权威            |
| P5 空间回收（B-6c 收尾） | 不变                                                                                                  | 不变                                       | 只读保留（逻辑仍占 ~5.3 GiB，物理回收见 §5 末） | ✓ 权威 |

- **P1→P3 窗口**内读路径仍以明文为权威，压缩列只是「写上去但还没人读」——任何压缩 bug 在 P3 前都不会炸读面。
- **P3 切读**是单点切换：`ZstdJSON` 绑定到 `result_data_zst` 的 migration 随 `_ensure_report_schema` 上线 + 一个 `REPORT_STORAGE_MODE` env（`plaintext|dual|compressed`）控制读哪列。env=plaintext 可瞬间回退。
- **停写明文**（P4）在至少 7 天观察、且残留计数
  ```sql
  SELECT count(*) FROM reports
   WHERE result_data_zst IS NULL
     AND result_data IS NOT NULL
     AND result_data <> 'null';
  ```
  持续返回 0 后才执行（口径与 §2.1/§5 对齐，排除 318 行不迁移的 `'null'` 行）。**写法硬约束：`IS NULL` 判定条件严禁与 `= 0` 连用**——`... IS NULL = 0` 被 SQLite 解析为 `IS (NULL = 0)` ≡ `IS NULL`，语义完全反转（迁移未开始时返回 0 会立即放行、完成后返回千级计数反而永不放行）；本节与 §5 全部准入/残留判据一律采用「残留计数 = 0」写法。执行后明文列数据**保留不删**（见 §6 回退）。
- **P4 准入硬门槛**：`b6c_migration_failures` 台账非空时**禁止进入 P4**，更禁止对明文列做任何批量清空/置 NULL——失败行若明文被清即形成不可恢复丢失路径（压缩列无数据、明文已删）。台账清零是 P4 前置条件之一。
- **P5 空间回收**（新增阶段，见 §5 末「空间回收」节）：P4 之后执行 `VACUUM`（或 `VACUUM INTO` + 原子换名）把 freelist 中 ~5.3 GiB 真正归还文件系统；不执行则库文件物理大小永远停在 ~5.4 GiB，§1 目标不可达。
- 读路径在 P3 后遇到「`result_data_zst IS NULL AND result_data IS NOT NULL AND result_data <> 'null'`」的边缘行（P4 后新写不再出现；迁移期漏网）——TypeDecorator 的 `process_result_value` 只在绑定的压缩列上工作；这种行由迁移脚本兜底补压，或由 helper 提供 `SELECT COALESCE 明文` 的 fallback 查询，设计上规定：**P3 切换前必须把
```sql
SELECT count(*) FROM reports
 WHERE result_data_zst IS NULL
   AND result_data IS NOT NULL
   AND result_data <> 'null';
```
降到 0**（`'null'` 行不计入——它们解码后语义即 `None`，见 §2.1；`IS NULL` 不得与 `= 0` 连用，理由同 §4 P4 注）。

## 5. 迁移脚本要点（B-6c 输入）

- 分批：`SELECT id, result_data FROM reports WHERE result_data_zst IS NULL AND result_data IS NOT NULL AND result_data <> 'null' ORDER BY created_at LIMIT N`（口径同 §2.1），每批 N=50 提交一次；对 1,625 行口径下实际待迁移 ~1,307 行 ≈ 27 批（318 行 `'null'` 不迁）。
- 每行：`zst = encode_result_data(json.loads(result_data))` → `UPDATE reports SET result_data_zst=?, result_data_zst_len=?, <31 物化列>=… WHERE id=?`。物化列回填复用 §2.2 的 `_populate_post_gate_columns` 逻辑（同一函数，输入 dict 输出列值 dict）。
- 校验：`decompress(zst)==原文 utf-8` 逐行比对后再 commit；失败行记入 `b6c_migration_failures` 台账，不阻塞批。**硬约束：`b6c_migration_failures` 非空时禁止进入 P4，更禁止对明文列做任何批量清空**——失败行明文是仅剩的权威副本，清空即不可恢复丢失（与 §4 P4 准入同一条约束的两侧）。
- 内存口径：流式 LIMIT 分页 + 每行解压后即弃，峰值 = 单行 max 13 MiB 明文 + ~0.5 MiB 压缩 + interpreter ≪ 4 GiB。

### 空间回收（P5，B-6c 交付物之一）

SQLite 下 UPDATE 置 NULL / `DROP COLUMN` 只把页归还 freelist，**库文件物理大小不变**（实测压缩+置空后文件仍 ~5.4 GiB）。§1 的库文件目标必须经显式回收才可达：

- **执行点**：P4 完成、观察期确认读面稳定、`b6c_migration_failures` 台账清零后，作为 B-6c 的收尾步骤执行。
- **方式（二选一，推荐后者）**：
  - `VACUUM;`——原地重建，简单，但执行期需要 ≈ 库大小的工作空间 + 新库本体（**前置要求可用磁盘 ≥ 2× 当前库大小**，即 ≥ ~12 GiB）。
  - `VACUUM INTO 'reports_compacted.db'` + 校验通过后 `os.replace` 原子换名——峰值占用同样 ≈2×，但源库在换名前保持只读可用、失败不留半截文件。
- **停写窗口**：VACUUM 全程持有写锁且重写整库（~5.8 GB → ~0.5 GiB），预估分钟级；须在服务停写/维护窗口执行（服务重启间隙或与 `REPORT_STORAGE_MODE` 切换同一窗口），执行期间禁止分析写入。
- **失败语义**：`VACUUM INTO` 失败只留下不完整的目标文件，源库零影响——无数据风险；但回收后明文列物理空间才真正释放，此后回退只能依赖「明文列仍在」（§6），故 P5 必须在 P4 观察期通过、台账清零之后。
- **产物**：库文件 ~5.4 GiB → ~0.4–0.7 GiB（31 物化列 + 索引 + freelist 残余）。

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
6. **写路径并发内存上界（新增，DAV-1779 🟢-5）**：压缩写放大在 P1–P3 双写窗口叠加 asyncio default executor（`api/main.py:361`，`ASYNCIO_DEFAULT_EXECUTOR_WORKERS` 默认 **64**）的并发写，峰值 ≈ 64 × (13 MiB 明文 + json.dumps 副本 + ~0.5 MiB 压缩) ≈ **1.7 GiB**——接近但未超 4 GiB 口径，单行压缩 CPU 耗时中位 7.22 ms 也远低于 executor 排队时长，**当前配置可接受，不设额外信号量**；若后续把 executor workers 调大或单行数据上界增大（p100 已 13 MiB），须重算此上界或引入写信号量（≤4 并发压缩）。解压侧同理：64 并发 × 13 MiB ≈ 0.8 GiB 瞬时明文，可接受。

7. **复审意见落实**（DAV-1774 ✅通过，🟢×5）：①§3.1 伪码 `ensure_ascii` 已改为默认参数（与 §3.3/§8.4 sha256 一致性要求对齐）；②`v03_return_measure.py:1934` 原生 `SELECT result_data` 已补入 §3.3 改造表；③`PRAGMA user_version` 降级为只读审计戳、列存在性仍以 `_ensure_report_schema` 现有 `insp.get_columns` 幂等路径为准（§6）；④`work/` 一次性脚本建议在 `work/README` 或脚本头统一加一行指向 `compressed_json.decode_result_data`（B-6b 顺手加，不阻塞）；⑤`_js()` 行号指引——定义 `report_service.py:3006`（`load_post_gate_fragments` 内嵌套函数）+ 6 处调用点 `:3020/:3021/:3041/:3042/:3062/:3065`，物化列切换后连同调用一并删除（JSON 列给 dict 再进 `_js` 不报错，属静默残留）。
8. **zstd 上下文线程安全（DAV-1776 🔴-1，B-6b 交付门槛）**：`ZstdCompressor`/`ZstdDecompressor` 实例非线程安全，禁止类级/模块级单例，必须经 §3.1 `threading.local()` 的 `_cctx()`/`_dctx()` 每线程各持一份。**B-6b 交付时必须附「≥8 线程并发 round-trip 1000 次零错误（无异常、无静默字节 MISMATCH、无进程崩溃）」的实测证据**，缺失视为未完成。反例证据（复审实测）：共享 ctx 8×200 全报错，含 `SIGSEGV`(139)/`Bus error`(138) 与静默 MISMATCH。

### 本次修订落实清单（DAV-1779 打回项）

- 🔴 §4 P4 准入 SQL 语义反转 → 改为残留计数写法，并加「`IS NULL` 不得与 `= 0` 连用」硬约束（§4、§5、本处 SQL 同口径）。
- 🟡 补 P5 空间回收节（§4 表尾行 + §5 末「空间回收」小节），§1 目标措辞同步修正为「逻辑占用 5.28 GiB→~0.2 GiB；库文件 P5 VACUUM 后 ~0.4–0.7 GiB」。
- 🟡 P4 行改为「新写/更新行的明文列置 NULL（历史行明文不批量清空）」；§4/§5 补 `b6c_migration_failures` 非空禁止进 P4、禁明文批量清空的硬约束。
- 🟢① `_js()` 调用点 4→6 处补全（`:3062`/`:3065`）；🟢② manager_action 行号修正为 `:3030-3033`/`:3051-3054`；🟢③ §2.1 判据 `length>4` 与 §4/§5 SQL 统一为 `result_data IS NOT NULL AND result_data <> 'null'`（移除 length 项）；🟢④ `max_output_size` 省略号明确为行级 `result_data_zst_len`、缺省回退全局 `MAX_RESULT_DATA_BYTES`（64 MiB）；🟢⑤ §8 第 6 条补生产写路径并发内存上界核算（executor 64 workers 口径，当前可接受）。

## 9. 交付与衔接

- 本文档：`docs/dav1770-b6-compression-design.md`（分支 `agent/1/dav1770-b6a-design`，只推分支不碰主干）。
- 批准后：**B-6b**（写路径双写 + TypeDecorator + 物化列 + `_ensure_report_schema` + env 开关）与 **B-6c**（迁移回填脚本）可并行开工——B-6c 依赖 B-6b 的 `compressed_json.encode/decode` 与 `_populate_post_gate_columns` 函数签名，接口即 §3.1/§2.2；**B-6d** 等 B-6b 接口冻结。
