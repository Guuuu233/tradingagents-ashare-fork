# 共享只读快照使用说明（DAV-1528，v2）

每个交易日批后由 `scripts/ta_snapshot.py` 生成一份压缩的生产库快照，
固定位置 `/private/tmp/ta-snapshot/`：

- `tradingagents-YYYY-MM-DD.db.zst` — 按交易日的压缩快照，**只保留最近 3 份**，
  超出的旧 `.zst` 由脚本自动删除（删除记入运行 JSON）。
- `current.db` — 每次快照后由最新 `.zst` 解压刷新的**共享只读整库**
  （权限 `0444`，quick_check 验证后原子替换）。它计入 D-073 的全机
  未压缩整库副本上限（3 份），各卡自己的解压副本另计。

## 各卡使用模板

### 只读卡（绝大多数情况）

**直接用 `current.db`，不要复制、不要解压 `.zst`。**

```bash
# 只读打开共享快照（static 文件，immutable=1 最快且不建 WAL 侧文件）
sqlite3 "file:/private/tmp/ta-snapshot/current.db?mode=ro&immutable=1" \
  "select count(*) from reports;"

# 项目 Python 同样方式（不要指到活库 data/tradingagents.db）
DATABASE_URL="sqlite:////private/tmp/ta-snapshot/current.db" ...
```

**不得复制 `current.db`**——它本身就是给全机共享的那份整库；
每多复制一份就多占一份 5 GB 磁盘并占 D-073 副本额度。

### 需要写入的演练/测试卡

只有必须对库**写**（迁移演练、写路径测试）的卡才单独解压，且必须
解压到自己卡的临时路径、用完即删：

```bash
WORK=/private/tmp/ta-work-<卡号>
mkdir -p "$WORK"
zstd -d -f /private/tmp/ta-snapshot/tradingagents-<YYYY-MM-DD>.db.zst \
  -o "$WORK/tradingagents.db"
# ...用完...
rm -f "$WORK/tradingagents.db"   # 确切路径，不进废纸篓
```

## 纪律（D-073）

- 每卡同一时刻最多 1 份自解压整库副本；**全机未压缩整库副本上限 3 份**，
  `current.db` 占其中 1 份，各卡自解压合计不得再超 2 份。
- 自解压副本运行结束立即 `rm`（确切路径），路径登记在卡上。
- 磁盘可用 <100 GB（红区）不得新建整库副本。
- 解压/共享副本是**可再生**的，一律不上传夸克、不归档。

## 保留份数

- 本地 `.zst` 快照：**最近 3 份**，超出自动删除（v2 起脚本自己删，
  只删 `out_dir` 下 `tradingagents-*.db.zst`，删除记入 JSON `deleted`）。
- `current.db` 始终只有 1 份，随每次快照原子刷新。
- **当周最后一份快照兼作每周基线**：DAV-1508 的周任务把该 `.zst`
  上传夸克（远端保留 2 份），DAV-1508 不再另做整库备份。

## 定时方式（方案文本，不安装）

脚本只交付、不启用。建议由发布/运维卡在批准后安装 launchd 任务：

- Label `com.tradingagents.ta-snapshot`
- `StartCalendarInterval`：周一–周五 16:30（批后、收盘后；
  脚本内部再用交易日历硬判，遇节假日自动跳过，所以按工作日排即可）
- `ProgramArguments`：
  `/usr/bin/env -u PYTHONPATH <repo>/.venv310/bin/python <repo>/scripts/ta_snapshot.py`
- 工作目录 `<repo>`，stdout/stderr 落 `work/ta-snapshot.{stdout,stderr}.log`
- `RunAtLoad` false

模板 plist 可参考 `scripts/com.tradingagents.tplus5-shadow.plist`。
安装前必须经发布流程签字，本卡不安装。

## 验证

```bash
# 手动跑一个交易日（例：2026-09-30）
python scripts/ta_snapshot.py --date 2026-09-30
# 输出 JSON 含 snapshot / current_db / deleted / snapshots 清单
```
