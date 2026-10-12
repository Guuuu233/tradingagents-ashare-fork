# 发布卡草稿 — DAV-1614 存储 B-4 存量瘦身（`result_data` → `result_data.storage.v1`）

> **草稿**，随本卡交付。真实执行须总控对准确 SHA 的「准予合入 / 准予部署 / 准予写生产」签字，且须在 10-09 监测窗口之后、B-1 合入之后。

## 对象

- 变更：`reports.result_data` 中物理携带 `horizons` 别名的存量行 → `result_data.storage.v1`（删除物理 `horizons` 与顶层 `market_data_context`，权威档为 `short_term`/`medium_term` + 档内 mdc）。
- 脚本：`scripts/slim_result_data_storage.py`（候选 SHA 见交付评论）。
- 生产库：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`（约 5.0 GB）。

## 前置条件（全部满足才可执行）

1. B-1（DAV-1506）已合入主干并已在生产运行——本迁移的读侧契约完全依赖其兼容层。
2. 本 B-4 候选经同 SHA 复审 PASS、总控签字准予执行写生产。
3. 生产 `healthz` `status=ok` 且 `active_analysis_count=0`；脚本守卫会自动跳过非空闲窗口。
4. 10-09 监测窗口已过。

## 执行步骤

1. **预检**：`PRAGMA integrity_check` = `ok`；记录库文件字节数与 SHA-256。
2. **备份副本**（唯一整库副本，D-073）：
   `sqlite3 "file:data/tradingagents.db?mode=ro" ".backup '/private/tmp/dav1614-prod-copy.db'"`
3. **演练核对**（在 `/tmp` 副本上，`--copy-rehearsal`）：
   `python scripts/slim_result_data_storage.py --db-path /private/tmp/dav1614-prod-copy.db --copy-rehearsal --journal-path ... --exceptions-path ... --pre-export-dir ...`
   - 报：scanned / candidates / converted / conflicts / verify_failures / 耗时 / 峰值内存 / 前像大小。
   - `verify_failures` 或 `conflicts` 非零 → 停止，先评审 exceptions 清单。
4. **VACUUM INTO** 生成新库：
   `python scripts/slim_result_data_storage.py --db-path <新演练库> --vacuum-into /private/tmp/dav1614-slim.db`（演练路径）；
   生产执行时：`VACUUM INTO` 到 `data/tradingagents.db.slim`（同盘，保证原子 rename）。
5. **校验新库**：`--vacuum-into` 内建 `verify`（integrity_check + 行数一致 + 逐行 `expand_compat_view` 比对）必须全绿；另跑 `scripts/verify_h1b_gates.py` 对比新旧库输出逐字相同。
6. **切换**（生产，须总控在场回读）：
   - 停服务；
   - `mv data/tradingagents.db data/tradingagents.db.pre-slim`（原子、保留旧库作回退）；
   - `mv data/tradingagents.db.slim data/tradingagents.db`；
   - 启服务，`/healthz` 回读 + H1b 门槛脚本 + 每日批判终态抽样比对；
   - 异常即 `mv` 回退。
7. **收尾**：观察 24–48h 无读异常后，`data/tradingagents.db.pre-slim` 按归档流程退役（夸克归档 / 删除），演练副本删除。

## 回退

- 行级：每个写入批次有独立 zstd 前像导出文件（`work/dav1614-pre-export/`），`restore` 子命令逐行回写并比对。
- 库级：步骤 6 的原子 rename 保留旧库，整体回退 = 换回旧库文件。

## 风险与边界

- 冲突行（别名不一致/不可还原）不改、入例外清单；生产如存在，单独评估。
- `*_term`-only 行不在本迁移范围（打 v1 会改其兼容读），如总控要一并瘦身须另行评估。
- `result_data_sha256_at_seal` 类按物理 blob 哈希的字段在转换后变化——属设计（物理形态即目的），但若有按哈希比对的下游须先确认。
- 运行时守卫与 `_is_production` 保护沿用回填脚本口径；10-09 前生产写一律拒绝。
