# DAV-1772 (B-6c) 压缩迁移脚本 —— 副本演练报告

> **修订 r2（2026-10-10 16:20–16:45）：DAV-1799 复审打回后的重演练。**
> 复审定位到 R1（argparse 全局选项被同名子选项静默覆盖）后，§7 “演练全程
> `--copy-rehearsal`” 的证据链不成立（见 §8），本报告按 r2 重取，§1–§6 保留
> r1 数字并标注哪些在 r2 复现。r2 只重跑受 R1/R2/M1 影响的路径。

演练时间：2026-10-10 14:54–15:20（r1）／2026-10-10 16:20–16:45（r2，Asia/Shanghai）
演练对象：`/private/tmp/ta-snapshot/current.db`（2026-10-09 快照，5,834,317,824 B = 5.43 GiB）
演练副本：`work/dav1772-rehearsal/copy.db`（全程只此 1 份可写副本，演练完已删除）

> 本次数字取自 **B-4（10-14）落地前的快照**，按卡面属「仅供参考」；B-4 上线后需在新快照上重测一次并以那次为准。

## 0. 解释器与命令口径

```
$ env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -V
Python 3.10.20
```
zstandard 0.23.0 / SQLite（venv310）。所有演练命令均带
`env -u PYTHONPATH http_proxy=https_proxy=all_proxy=`，footprint 口径为**直接 exec**
`/usr/bin/time -l`（不含外层 shell）。

## 1. 库规模与候选行

| 指标 | 值 |
|---|---|
| reports 总行数 | 2410 |
| completed 行 | 1624 |
| 候选行（`result_data_zst IS NULL AND result_data IS NOT NULL AND result_data <> 'null'`） | **1307** |
| 候选行明文字节合计 | 5,667,796,556 B（5.28 GiB） |
| `result_data IS NULL` 行 | 785 |
| 明文为字面量 `'null'` 行 | 318（按设计不迁移） |
| `typeof(result_data)` 分布 | `text`, `null`（无 BLOB 型，无隐式类型转换风险） |

## 2. 各阶段耗时 / peak footprint（`/usr/bin/time -l`，GiB）

| 阶段 | 命令 | 墙钟 | peak RSS | peak memory footprint | 备注 |
|---|---|---|---|---|---|
| 只读扫描 | `convert --scan-only` | 43.81 s | 590,987,264 = **0.550 GiB** | 58,262,176 = 0.054 GiB | 扫描在子进程做，避免父进程堆污染写入阶段 |
| 转换（主路径） | `convert`（27 批） | **130.17 s** | 590,987,264 = **0.550 GiB** | 601,113,872 = 0.560 GiB | 1307/1307 落库 |
| 逐行解码回读 | `verify --require-complete` | 82.53 s | 611,958,784 = **0.570 GiB** | 635,700,328 = 0.592 GiB | ok=true |
| P5b 清明文 + 备份 | `clear-plaintext`（无 vacuum） | 70.55 s | 330,694,656 = **0.308 GiB** | 429,294,480 = 0.400 GiB | 备份含 `.backup()` + zstd 流式整库压缩 |
| P5b 反向恢复 | `restore-plaintext` | 42.21 s | 440,500,224 = **0.410 GiB** | 419,955,600 = 0.391 GiB | 1307 行回写，0 失败 |
| 真实中断后续跑 | `convert`（续跑 657 行） | 85.94 s | 408,453,120 = **0.380 GiB** | 358,171,536 = 0.333 GiB | 见 §5 |
| P5b 清明文 + VACUUM INTO | `clear-plaintext --vacuum-into` | 81.04 s | 958,103,552 = **0.892 GiB** | 913,212,664 = 0.850 GiB | 含瘦身副本 pairwise 校验 |
| `rollback`（50 行批） | `rollback --export-file …` | 2.79 s | 1,592,688,640 = **1.483 GiB** | 1,557,956,432 = 1.451 GiB | 最高的一次，仍 ≪ 4 GiB 红线 |

**peak footprint 汇总：单次最高 1.483 GiB（rollback 批），主路径 convert 仅 0.560 GiB —— 直接 exec 口径全部 ≤4 GiB，红线满足，余量 ≥2.7x。**

## 3. 库文件前后大小

| 阶段 | 大小 |
|---|---|
| 快照源 | 5,834,317,824 B（5.434 GiB） |
| convert 后（含新增压缩列，文件未收缩） | 5,993,734,144 B（5.582 GiB，+2.7%） |
| 压缩列合计 | 145,398,065 B（0.135 GiB） |
| 压缩率 | **38.98x**（明文降 97.43%） |
| `clear-plaintext` 后（清空明文，文件仍占位） | 5,993,734,144 B（VACUUM 前不回收） |
| `VACUUM INTO` 瘦身副本 `slim.db` | **305,156,096 B（0.284 GiB）**，相对快照源 **-94.8%** |
| P5b 备份 `.db` / `.db.zst` | 5,993,734,144 B / 532,314,976 B（0.496 GiB，zstd -91.1%） |

结论：**空间回收必须走 VACUUM INTO**，`convert` 本身只增列不减文件（预期行为）。

## 4. 裁定②：原始字节 vs 重新序列化

迁移按原始字节执行：`zst = compress(stored_bytes)`，事务内 `decompress(zst) == stored_bytes` 才提交。

- `convert` 输出：`"unparsable_rows": 0`、**`"reserialize_mismatch_rows": 0`**、`reserialize_mismatch_samples: []`
- `verify` 输出同口径：`reserialize_mismatch_rows: 0`、`unparsable_rows: 0`

即：**「原始字节与重新序列化结果不一致的行数」= 0**。本快照全部 1307 候选行都能被
`json.dumps(json.loads(x))` 逐字节复现，故 json round-trip 路径在这批数据上不会丢信息——
但实现仍坚持原始字节路径（round-trip 会受 `ensure_ascii`/浮点格式化/键序影响，不作依赖）。
`verify --require-complete` 复核：`verified_ok: 1307`、`mismatch_count: 0`、`unmigrated: 0`、`ok: true`。

## 5. 分批 / 断点续跑（真中断，非模拟）

1. 主路径 convert：27 批、1307 行、130.17 s。
2. **删副本重拷**快照，跑 convert，`kill -9` 在中途（第 75 s）：journal 1950 行。
3. 中断后状态自检：zst 行 **650**、未迁移候选 **657**、journal committed id **650**，
   三者严格一致；`PRAGMA quick_check` = `ok`（中断点无半写行、无脏事务）。
4. 用**同一 journal** 续跑：`resumed_skipped: 650`、`candidates: 657`、`converted_rows: 657`、
   `verify_failures: 0`、`pre_export_mismatch: 0`、`write_conflicts: 0`，14 批 / 85.94 s。
5. 终态 `verify --require-complete`：`verified_ok: 1307`、`mismatch_count: 0`、`ok: true`。
6. 幂等：再次用完整 journal 重跑 → `resumed_skipped: 1307`、`candidates: 0`、`converted_rows: 0`。

## 6. 回退子命令

`rollback`（从批前像恢复 pre-convert 状态）：

- 明文仍在、仅多出 zst → `rolled_back: 50`（zst 行 1307→1257，未迁移候选回到 50），
  `conflicts/mismatched/missing` 全 0。
- P5b 已清明文的情形（把 1307 行明文置 NULL 后）→ `restored_plaintext: 50`，
  即从前像写回明文并清 zst 列，验证 50 行明文逐字节等于前像。
- 前像 sha256 不匹配时 fail-closed：`pre-export sha256 mismatch; refusing to restore`，exit 1
  （演练中由操作者手误触发过一次，脚本正确拒绝）。

`restore-plaintext`（裁定①前提③）：`restored_rows: 1307`、`failed: 0`，随后 verify 回到
`verified_ok: 1307 / cleared_ok: 0 / ok: true` —— 压缩↔明文往返无损。

`clear-plaintext`（P5b，裁定①前提①②）：

- `--allow-clear` 缺省即拒绝；`.backup()` + zstd 整库备份先于任何明文写入，备份命名
  `tradingagents-b6c-p5b-<stamp>.db{,.zst}`，30 天保留（`BACKUP_RETENTION_DAYS=30`，按 mtime 剪枝）。
- 逐行先验后清：`decompress(zst) == 明文字节` 才 `result_data=NULL`，事务内再验一次；
  `cleared_rows: 1307`、`failed: 0`、`failures: []`；随后 verify `cleared_ok: 1307`、`cleared_bad_count: 0`。
- 瘦身 pairwise 校验：`old_rows 2410 = new_rows 2410`、`view_mismatches: 0`、
  `missing_in_new: 0`、`integrity: ok`；slim 副本再 verify 亦 `ok: true`。

## 7. 未覆盖 / 遗留

- ~~演练全程 `--copy-rehearsal`，`check_runtime_guard` 未被真实服务驱动~~ —— **r1 这条声明已被 R1 推翻，事实是当时 `--copy-rehearsal` 被重置为 False，演练实际调用了真实 `127.0.0.1:8000/healthz`。修复后已在 r2 重新取证，见 §8。**
- 生产写闸门 `_refuse_production_unless_signed`（最早 2026-10-16、需 `--production-authorized`）在演练中未触发；**r2 已补 3 条单元断言**（未授权拒绝 / 最早日前一天拒绝 / 当天放行，边界取向 `<` 而非 B-4 的 `<=`）。
- 本次未演练 `export-preimage` 独立子命令的 CLI 入口（其底层 `export_pre_images` 已在 convert 中逐批执行并逐批核对 sha256）。
- 并发写入冲突路径（`write_conflicts`）未在演练中触发，演练期间无并发写者。

## 8. R1/R2 返修后重演练（r2，锁定解释器）

r2 与 r1 同一份快照、同一套解释器，命令一律将 `--copy-rehearsal` 放在**子命令之前**
（正是 R1 的失效位置）。所有命令经 `main()` 真实 argparse 解析。

### 8.1 R1 复核：`--copy-rehearsal` 真的到达了 dispatch

把 `check_runtime_guard` 包一层计数器，记录是否被调用：

| 阶段 | 退出码 | 墙钟 | `healthz` 实际调用次数 |
|---|---|---|---|
| `convert --copy-rehearsal` | 0 | 112.3 s | **0** |
| `verify --copy-rehearsal` | 0 | 51.0 s | **0** |
| `verify --copy-rehearsal --require-complete` | 0 | 72.3 s | **0** |
| `clear-plaintext --copy-rehearsal --allow-clear --vacuum-into` | 0 | 121.6 s | **0** |
| `restore-plaintext --copy-rehearsal` | 0 | 45.1 s | **0** |

`healthz 调用次数 = 0` 即证明演练口径真实生效；r1 下该值应为 >0（即真实闸门被误触发）。
r1 报告的“全程 `--copy-rehearsal`”不成立这一点，本节予以纠正。

### 8.2 转换与 P5b 往返（与 r1 数字一致）

| 阶段 | 结果 |
|---|---|
| `convert`（50 行批） | `verified_ok 1307 / mismatch 0 / cleared_bad 0 / lost_blob 0 / unmigrated 0 / ok true`（r2 verify 复核） |
| `clear-plaintext --vacuum-into` | `cleared 1307 / failed 0`，备份 `tradingagents-b6c-p5b-20261010T163749.db.zst` |
| 清后 `verify` | `cleared_ok 1307 / cleared_bad 0 / lost_blob 0 / ok true` |
| `restore-plaintext` | `restored 1307 / failed 0` |
| 往返后 `verify` | `verified_ok 1307 / cleared_ok 0 / ok true` |

r2 一次主路径 `convert` peak RSS **585,842,688 = 0.546 GiB**、`verify --require-complete`
**632,406,016 = 0.589 GiB**、P5b 往返段峰值 **1,071,251,456 = 0.998 GiB**，均 ≤4 GiB 红线。

### 8.3 R2 红队在真实数据上复核

在已 P5b 清明文的副本上人为置空一行 `result_data_zst`（保留 `result_data_zst_len`）：

- 受影响行 `3b9d4dfb7c0e414e838d9562dc1dad72`，其 `result_data_zst_len = 1343634`（1.28 MiB）。
- `verify --require-complete` → **`ok=false`**、`lost_blob_count=1`、`null_rows=785`
  （785 行真 NULL 未受污染）、`lost_blobs[0].error` 明文描述“唯一副本丢失, 不可恢复”。
- CLI 退出码 **1**。
- r1 下同一场景 `ok=true`、`cleared_bad=0`，即验收门禁失明。

### 8.4 r2 清理

演练副本、`.db`/`.db.zst` 备份、前像导出（合计 ≈18 GiB）已全部删除，`work/` 下仅留
本报告；`.gitignore` 已补 `dav1772-pre-export/`、`dav1772-backups/`、`dav1772-journal/`、
`dav1772-rehearsal/` 四条（复审 M7），`git check-ignore` 实测四条目录全部 IGNORED，
而本报告未被误伤。快照源与生产库 `data/tradingagents.db` mtime 未变。

## 9. r3：P5b 补三处演练（总控 10-12 裁定后）

同一份快照（`/private/tmp/ta-snapshot/current.db`）、同一锁定解释器，演练 `b31fd85a`。

### 9.1 三个门的实测

| 门 | 场景 | 结果 |
|---|---|---|
| 补 3 完整性门 | 人为注入 3 条未迁移残留行 | **拒绝并报计数、CLI exit 1**；修复后 `verify: verified_ok 1307 / unmigrated 0 / ok true` |
| 补 2 模式门 | 真实服务 `/healthz`（字段尚未部署） | 返回 `absent`、拒绝生效（fail-closed） |
| 补 2 模式门 | 演练模式未给 `--rehearsal-reason` | 拒绝；给出理由后放行并记录 `mode=rehearsal / enforced=false` |
| 三门全过 | `clear-plaintext --vacuum-into` | `cleared 1307 / failed 0`；`completeness_gate` 六项计数全 0 |

### 9.2 补 1：备份校验与 plain 副本删除

- `zstd_t: ok`（本机 `/opt/homebrew/bin/zstd` v1.5.7 实调用）、`stream_compare: ok`、
  `restored_bytes 5993734144`、`plain_backup_deleted: true`
- 备份目录**未压缩 `.db` 计数 0**（D-073：本地不留未压缩整库备份）
- 独立复验：另跑 `zstd -t` PASS；仅从 `.db.zst` 还原得 5,993,734,144 B，
  `PRAGMA quick_check ok`、reports 2410 行（临时还原件已删）
- `VACUUM INTO` pairwise：`2410=2410 / view_mismatches 0 / missing_in_new 0 / integrity ok`
- 清后 `verify: cleared_ok 1307 / cleared_bad 0 / ok true`；`restore-plaintext`
  往返 `restored_rows 1307 / failed 0`

### 9.3 peak 口径说明（复审 DAV-1913 🟢3 要求澄清）

两个数字口径不同，**并非矛盾**，此前未写在一起容易被误读：

| 轮次 | 覆盖范围 | peak（`/usr/bin/time -l`） |
|---|---|---|
| r1 / r2（§2、§8） | **六个子命令**全跑通，峰值出现在 `rollback --export-file` | **1.483 GiB**（RSS 1,592,688,640） |
| r3（本节） | **仅 P5b 补三处路径**（convert + 三门 + clear + vacuum + restore） | **1.389 GiB**（*peak memory footprint* 1,491,633,928） |

即 r3 的 1.389 GiB < r1/r2 的 1.483 GiB，是因为 r3 未执行峰值最高的 `rollback` 批。
两者均为直接 exec 口径，全部 ≤4 GiB 红线。全流程最高值以 **1.483 GiB** 为准。

⚠️ **口径提醒（复审 DAV-1917 🟢）**：上表两列取自 `/usr/bin/time -l` 的**不同字段** ——
r1/r2 的 1.483 GiB 取自 *maximum resident set size*（peak RSS），r3 的 1.389 GiB 取自
*peak memory footprint*。二者不是同一指标，不可直接相减比较；上表的「高低」结论只用于说明
r3 未覆盖 rollback 批这一事实。生产实跑时应**统一用同一字段重采一次**，再据以判定红线余量。

### 9.4 r3 清理

演练副本、备份、前像导出（合计 ≈6.7 GiB）已全部删除（现存 0 份）；
生产库 `data/tradingagents.db` mtime 未变，快照源未触碰。

### 9.5 勘误：noqa 删除的归属（复审 DAV-1917 指出，复核属实）

- 逐版实测 `scripts/b6c_compress_migration.py` 中 noqa 命中数：
  `2d9f37f`=3 → `b31fd85a`=3 → `b2634a99`=0 → `94cc782e`=0 → `48ff6a01`=0；
- 真实时间线：noqa 由初版 `2d9f37f` 引入 → `b31fd85a`（P5b 补三处轮）**原样保留 3 处**
  → `b2634a99`（DAV-1908 🟢 采纳轮）**删除** → 之后各版保持 0。

> ⚠️ 本节此前把删除落点写成 `b31fd85a`，系一处二次错误，经复审 DAV-1918 指出后更正为
> `b2634a99`。之所以容易错，是因为 `b2634a99` 的**直接父**恰是 `b31fd85a`，
> 看 parent 时容易误记到父提交上。

本报告此前把该删除记在错误轮次，现更正（并已按上表定位到具体落点）。结论不变：本仓无 ruff 配置、CI 不跑 ruff，
不构成门禁破坏；若后续引入 ruff，需与项目统一风格一并处理。

### 9.6 提交信息勘误（不可改写的记录，在此更正）

`48ff6a01` 的提交说明里同样把删除落点写成 `b31fd85a`，属同一处错误。Git 提交信息
不可在不重写历史的前提下修改（重写会使已推送的 SHA 脱钩、迫使总控改用新 SHA），
故在此显式更正：`48ff6a01` 提交说明中「实际删除发生在 b31fd85a」应为
**`b2634a99`**。以本节为准。
