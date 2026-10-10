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
