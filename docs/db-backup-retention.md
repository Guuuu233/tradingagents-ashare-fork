# 数据库备份保留清单（provenance 台账）

盘点日期 2026-09-20 · 盘点时 trunk `a00eabe` · 线上服务 `42977154`

**这份文件是备份清理的前置凭据。** 12 份可移除备份的内容会被合并到各自的保留代表里，
但文件名编码的「这是部署 X 之前的库状态」这一标签只存在于本文件第 4 节。
**清理前必须先提交本文件；本文件丢失则不得清理。** 放在 `docs/` 而非 `work/`，
是为了不随 `work/` 下的备份一起被扫掉。

## 0. 执行状态：暂缓（2026-09-20）

**用户裁定暂不执行清理，转回项目实质工作。** 全部准备已完成并入库，可随时恢复。

| 项 | 体积 | 脚本 | 状态 |
|---|---|---|---|
| `.git` 13 个 `tmp_pack_*` | 436.25 MiB | `work/cleanup-1-tmp-packs.sh` | 就绪，未执行 |
| 74 个本项目孤儿目录 | 972.52 MiB | `work/cleanup-2-orphan-dirs.sh` | 就绪，未执行 |
| 12 份逐字节重复备份 | 4.087 GiB | `work/cleanup-3-backups-trash.sh` | 就绪，未执行 |

合计 5.463 GiB（字节级实测）。三份脚本互不依赖，可单独执行，均带运行时守卫。

恢复执行前必须重做的事：

1. 重跑 §6.2 的 `quick_check` 与 md5 比对 —— 本次验证时点为 2026-09-20 12:07，已过期。
2. 重新枚举孤儿目录 —— `/private/tmp` 内容会随时间变化，脚本里的 74 条是当时快照。
   脚本已内置守卫：目标缺失或出现 `.git` 即中止，但清单本身不会自动更新。
3. 确认 `work/dav990-diagnostic-logs/` 仍在库中（script2 有守卫，少于 26 文件即中止）。

**两个未决问题**：

- **§6.3 悖论**：`trash` 与源文件同卷 `/dev/disk3s5`，移入回收站不释放空间。
  按现行规则执行，即时回收只有 1.38 GiB，4.087 GiB 要手动清空回收站。
  要立即回收必须先显式修订 §6.3 并提交，不得在脚本里绕过。
- **§6.4 规则空白**：74 个孤儿目录在任何仓库都无 worktree 登记，
  「worktree 清理只能用 `git worktree remove`」对它们无对象可用，需人拍板。

**背景**：根卷当前 144 GiB 可用、已用 64%，不存在磁盘压力。此事为卫生而非急务。

**未量的更大目标**：`multica_workspaces_steer/davidsworks-*` 下 ≥17 个 fork worktree 副本，
体积未测，据第三方观察可能超过本次 5.463 GiB。

---
## 1. 盘点口径与完整性

| 项 | 值 |
|---|---|
| 范围 | `work/` 与 `data/` 顶层 `*.db.bak-*` / `*.db.migrated-*` |
| 文件总数 | 75 |
| 主库文件 | **39 份，12.09 GiB** |
| `-shm`/`-wal` 伴随文件 | 36 份，合计 0.56 MiB（全部 `-wal` 为 0 字节） |
| 完整性 | 39 份逐一 `PRAGMA quick_check`，**全部 `ok`** |
| 内容指纹 | 39 份逐一 `md5`，据此判定逐字节重复 |

所有 `-wal` 均为 0 字节，因此读**备份文件**可以用 `immutable=1`，不会漏数据。
读**活库**不行，见第 5 节。

## 2. 判定结果

| 分级 | 份数 | 体积 | 处置 |
|---|---|---|---|
| **保留** | 27 | 7.99 GiB | 内容唯一或为重复组代表，不动 |
| **可移除** | 12 | **4.08 GiB** | 与保留项逐字节相同，删除零数据损失 |

可回收上限 4.08 GiB，占总量约三分之一。剩余 7.99 GiB 每份内容独一无二。

已裁定的特殊项：`UNTRUSTED` 那份**保留**；`social_archive` 影子备份**保留**；
7 月最早两份**保留**；其余 27 份内容独特的主库备份**暂不动**。

## 3. 保留清单（27 份）

| 文件 | MiB | commit | reports/completed | 理由 |
|---|---|---|---|---|
| `data/tradingagents.db.bak-2026-07-30` | 85 | — | 333/205 | 用户裁定保留。早期状态（333/205）的仅存记录。 |
| `data/tradingagents.db.bak-20260729` | 9 | — | 278/151 | 用户裁定保留。项目最早期状态（278/151）的仅存记录。 |
| `data/tradingagents.db.bak-20260804` | 109 | — | 1057/553 | 内容唯一（1057/553，最新 2026-08-03 16:07），无第二份。 |
| `data/tradingagents.db.bak-20260823-035525` | 147 | — | 1248/662 | 内容唯一（1248/662，最新 2026-08-22 14:51），无第二份。 |
| `data/tradingagents.db.bak-20260919-deploy-aa2ccd5` | 732 | aa2ccd5 | 1736/978 | 内容唯一（1736/978，最新 2026-09-18 18:25），无第二份。 |
| `data/tradingagents.db.bak-20260920-pre-merge-41772fa` | 735 | 41772fa | 1742/980 | 内容唯一（1742/980，最新 2026-09-19 10:52），无第二份。 |
| `data/tradingagents.db.bak-cleanup-20260729-231127.UNTRUSTED` | 77 | — | 334/187 | 用户裁定保留。结构完好但当年被标 UNTRUSTED，标记原因已不可考，不得据「看起来可疑」销毁。 |
| `data/tradingagents.db.bak-industry-20260902` | 213 | — | 1330/731 | 内容唯一（1330/731，最新 2026-08-27 12:41），无第二份。 |
| `data/tradingagents.db.bak-pre-394e3ef` | 136 | 394e3ef | 1229/649 | 内容唯一（1229/649，最新 2026-08-20 18:31），无第二份。 |
| `data/tradingagents.db.bak-t5-20260902` | 208 | — | 1330/731 | 内容唯一（1330/731，最新 2026-08-27 12:41），无第二份。 |
| `work/social_archive.db.bak-20260918-122933-gate2-shadow` | 0 | — | -/- | 用户裁定保留。非主库（social_archive 影子备份），仅 0.4 MiB，无回收价值。 |
| `work/tradingagents.db.bak-20260908-2215` | 267 | — | 1407/791 | 内容唯一（1407/791，最新 2026-09-08 13:36），无第二份。 |
| `work/tradingagents.db.bak-20260909-serveupgrade` | 269 | — | 1408/792 | 重复组 `28a293f` 的保留代表，另 2 份与它逐字节相同。 |
| `work/tradingagents.db.bak-20260912-deploy-70b5b47` | 272 | 70b5b47 | 1409/793 | 内容唯一（1409/793，最新 2026-09-10 19:34），无第二份。 |
| `work/tradingagents.db.bak-20260913-deploy-54077b6` | 272 | 54077b6 | 1409/793 | 重复组 `1133dbf` 的保留代表，另 1 份与它逐字节相同。 |
| `work/tradingagents.db.bak-20260913-deploy-a227cdc` | 272 | a227cdc | 1409/793 | 内容唯一（1409/793，最新 2026-09-10 19:34），无第二份。 |
| `work/tradingagents.db.bak-20260913-deploy-bdb95f8` | 272 | bdb95f8 | 1409/793 | 重复组 `f453acd` 的保留代表，另 5 份与它逐字节相同。 |
| `work/tradingagents.db.bak-20260914-predeploy-9d702e7` | 272 | 9d702e7 | 1409/793 | 重复组 `dff22ba` 的保留代表，另 2 份与它逐字节相同。 |
| `work/tradingagents.db.bak-20260918-034833-deploy-e30f312` | 272 | e30f312 | 1410/794 | 内容唯一（1410/794，最新 2026-09-17 16:29），无第二份。 |
| `work/tradingagents.db.bak-20260918-041110-deploy-6ee1486` | 272 | 6ee1486 | 1410/794 | 内容唯一（1410/794，最新 2026-09-17 16:29），无第二份。 |
| `work/tradingagents.db.bak-20260918-122933-deploy-8e49a80-shadow` | 273 | — | 1416/794 | 内容唯一（1416/794，最新 2026-09-18 03:51），无第二份。 |
| `work/tradingagents.db.bak-20260918-130737-deploy-4b540b0` | 273 | 4b540b0 | 1418/794 | 内容唯一（1418/794，最新 2026-09-18 04:33），无第二份。 |
| `work/tradingagents.db.bak-20260918-211803-orphan-cleanup` | 732 | — | 1735/977 | 内容唯一（1735/977，最新 2026-09-18 13:17），无第二份。 |
| `work/tradingagents.db.bak-20260918-postclose-backfill` | 275 | — | 1419/795 | 内容唯一（1419/795，最新 2026-09-18 05:08），无第二份。 |
| `work/tradingagents.db.bak-20260919-021456-deploy-a290f18` | 732 | a290f18 | 1735/977 | 内容唯一（1735/977，最新 2026-09-18 13:17），无第二份。 |
| `work/tradingagents.db.bak-20260919-163716-deploy-4a48ec0` | 734 | 4a48ec0 | 1740/979 | 重复组 `ede5567` 的保留代表，另 2 份与它逐字节相同。 |
| `work/tradingagents.db.migrated-df753841` | 272 | df75384 | 1410/794 | 内容唯一（1410/794，最新 2026-09-17 16:29），无第二份。 |

## 4. 可移除清单（12 份，4.08 GiB）与 provenance 映射

### 4.1 可移除项

| 文件 | MiB | commit | 内容组 | 理由 |
|---|---|---|---|---|
| `work/tradingagents.db.bak-20260909-upto-fab99d9` | 269 | fab99d9 | `28a293f` | 与 `tradingagents.db.bak-20260909-serveupgrade` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260911-deploy-4a5206f` | 269 | 4a5206f | `28a293f` | 与 `tradingagents.db.bak-20260909-serveupgrade` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260914-deploy-4f1a1aa3` | 272 | 4f1a1aa | `f453acd` | 与 `tradingagents.db.bak-20260913-deploy-bdb95f8` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260914-predeploy-0263496` | 272 | 0263496 | `f453acd` | 与 `tradingagents.db.bak-20260913-deploy-bdb95f8` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260914-predeploy-63d5648` | 272 | 63d5648 | `f453acd` | 与 `tradingagents.db.bak-20260913-deploy-bdb95f8` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260914-predeploy-6cc4e1` | 272 | 6cc4e15 | `f453acd` | 与 `tradingagents.db.bak-20260913-deploy-bdb95f8` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260914-predeploy-79757a6` | 272 | 79757a6 | `f453acd` | 与 `tradingagents.db.bak-20260913-deploy-bdb95f8` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260914-predeploy-f094d6a` | 272 | f094d6a | `dff22ba` | 与 `tradingagents.db.bak-20260914-predeploy-9d702e7` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260918-001610-deploy-df753841` | 272 | df75384 | `1133dbf` | 与 `tradingagents.db.bak-20260913-deploy-54077b6` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260918-pre998replay` | 272 | — | `dff22ba` | 与 `tradingagents.db.bak-20260914-predeploy-9d702e7` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260919-172726-deploy-ac8955e` | 734 | ac8955e | `ede5567` | 与 `tradingagents.db.bak-20260919-163716-deploy-4a48ec0` 逐字节相同（md5 一致），删除零数据损失。 |
| `work/tradingagents.db.bak-20260919-182312-deploy-7a98819` | 734 | 7a98819 | `ede5567` | 与 `tradingagents.db.bak-20260919-163716-deploy-4a48ec0` 逐字节相同（md5 一致），删除零数据损失。 |

### 4.2 provenance 映射（**清理后唯一的标签来源，不得丢失**）

下列每组只保留一份文件，但该份内容同时代表组内所有部署点。
清理后若要回答「部署 X 之前的库是什么状态」，答案就在这张表里。

**`f453acd`** — 272 MiB，reports 1409/793，最新 2026-09-10 19:34

保留：`work/tradingagents.db.bak-20260913-deploy-bdb95f8`

该份内容同时是以下 6 个部署点的库状态：

- `tradingagents.db.bak-20260913-deploy-bdb95f8`（bdb95f8 2026-09-13）
- `tradingagents.db.bak-20260914-deploy-4f1a1aa3`（4f1a1aa 2026-09-14）
- `tradingagents.db.bak-20260914-predeploy-0263496`（0263496 2026-09-14）
- `tradingagents.db.bak-20260914-predeploy-63d5648`（63d5648 2026-09-14）
- `tradingagents.db.bak-20260914-predeploy-6cc4e1`（6cc4e15 2026-09-14）
- `tradingagents.db.bak-20260914-predeploy-79757a6`（79757a6 2026-09-14）

---

**`28a293f`** — 269 MiB，reports 1408/792，最新 2026-09-09 04:39

保留：`work/tradingagents.db.bak-20260909-serveupgrade`

该份内容同时是以下 3 个部署点的库状态：

- `tradingagents.db.bak-20260909-serveupgrade`（—）
- `tradingagents.db.bak-20260909-upto-fab99d9`（fab99d9 2026-09-09）
- `tradingagents.db.bak-20260911-deploy-4a5206f`（4a5206f 2026-09-11）

---

**`dff22ba`** — 272 MiB，reports 1409/793，最新 2026-09-10 19:34

保留：`work/tradingagents.db.bak-20260914-predeploy-9d702e7`

该份内容同时是以下 3 个部署点的库状态：

- `tradingagents.db.bak-20260914-predeploy-9d702e7`（9d702e7 2026-09-14）
- `tradingagents.db.bak-20260914-predeploy-f094d6a`（f094d6a 2026-09-14）
- `tradingagents.db.bak-20260918-pre998replay`（—）

---

**`ede5567`** — 734 MiB，reports 1740/979，最新 2026-09-19 04:12

保留：`work/tradingagents.db.bak-20260919-163716-deploy-4a48ec0`

该份内容同时是以下 3 个部署点的库状态：

- `tradingagents.db.bak-20260919-163716-deploy-4a48ec0`（4a48ec0 2026-09-19）
- `tradingagents.db.bak-20260919-172726-deploy-ac8955e`（ac8955e 2026-09-19）
- `tradingagents.db.bak-20260919-182312-deploy-7a98819`（7a98819 2026-09-19）

---

**`1133dbf`** — 272 MiB，reports 1409/793，最新 2026-09-10 19:34

保留：`work/tradingagents.db.bak-20260913-deploy-54077b6`

该份内容同时是以下 2 个部署点的库状态：

- `tradingagents.db.bak-20260913-deploy-54077b6`（54077b6 2026-09-12）
- `tradingagents.db.bak-20260918-001610-deploy-df753841`（df75384 2026-09-17）

## 5. 读库口径（2026-09-20 修正）

盘点中发现并已修入 `AGENTS.md` §10（commit `a00eabe`）：

| 对象 | 正确读法 | 说明 |
|---|---|---|
| 活库 `data/tradingagents.db` | `?mode=ro` | 有 WAL 时必须走这个 |
| 静态备份文件 | `?immutable=1` | 本清单 39 份的 WAL 均为 0，适用 |

实测差异：活库带 2,257,792 字节 WAL 时，`immutable=1` 读到 `1742 / 980`，
`mode=ro` 读到真实的 **`1746 / 982`**（最新报告 `2026-09-19 18:24:07`），少算 4 份报告、2 份 completed。

**旧交接里的「生产库 1742 / 980」是错误读法产生的历史快照，不再作为当前基线。**

另记一条容易误判的巧合：活库主文件与 `data/tradingagents.db.bak-20260920-pre-merge-41772fa` 的 md5 相同（`3706e9f…`）。
这不代表活库没有新数据，只说明自那次备份后主文件未被 checkpoint，新写入仍在 WAL 中。

## 6. 执行前置条件

清理第 4 节 12 份之前，必须全部满足：

1. 本文件已提交进 git（丢失 provenance 即不得清理）。
2. 每份保留代表在**清理当时**重新跑一次 `quick_check` 并为 `ok`。
3. 删除走可恢复方式（先移至回收区或 `trash`），不用 `rm -rf`。
4. 清理只针对备份文件；**不得顺带处理任何 git worktree**，worktree 清理只能用 `git worktree remove`。

## 7. 未执行声明

本次为只读盘点。**未删除、未移动、未重命名任何备份；未清理任何 worktree；未写生产库；未重启服务。**
对活库的 `mode=ro` 查询为只读，未产生写入。
