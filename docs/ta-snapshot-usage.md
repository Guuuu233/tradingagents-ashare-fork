# 共享只读快照使用说明（DAV-1528）

每个交易日批后由 `scripts/ta_snapshot.py` 生成一份压缩的生产库快照，
固定位置 `/private/tmp/ta-snapshot/`，压缩方式 zstd，
文件名 `tradingagents-YYYY-MM-DD.db.zst`（日期为交易日，非生成日）。

各卡需要生产数据时**不再自己复制活库**，改为解压最近一份快照使用。

## 各卡使用模板

```bash
# 1. 找最新快照（按文件名日期排序即时间序）
SNAP=$(ls /private/tmp/ta-snapshot/tradingagents-*.db.zst | tail -1)

# 2. 解压到自己卡的临时路径（/private/tmp 下，与快照目录分开）
WORK=/private/tmp/ta-work-<卡号或自定义>
mkdir -p "$WORK"
zstd -d -f "$SNAP" -o "$WORK/tradingagents.db"
#   （zstd 解压默认保留 .zst 源文件，快照必须留下供别的卡用）

# 3. 只读使用（活库用 mode=ro；解压出的静态副本可用 immutable=1，更快）
sqlite3 "file:$WORK/tradingagents.db?immutable=1" "select count(*) from reports;"
#   或项目 Python：DATABASE_URL="sqlite:///$WORK/tradingagents.db" ...

# 4. 用完即删（D-073：每卡同一时刻最多 1 份整库副本）
rm -f "$WORK/tradingagents.db"
```

**纪律**

- 每卡同一时刻最多 1 份解压副本；全机未压缩整库副本上限 3 份（D-073 第 4 条）。
- 运行结束立即 `rm` 解压文件（确切路径，不进废纸篓）。
- 解压路径必须登记在卡上，便于他人清点。
- 磁盘可用 <100 GB（红区）不得新建整库副本，改用抽样小库或等待。
- 解压副本是**可再生**的，一律不上传夸克、不归档（D-073 第 4 条）。
- 快照文件本身 (`*.db.zst`) 不属于"未压缩整库副本"限额，但不要误删它——
  别的卡还在用。

## 保留份数

首次运行报告固定为 **保留最近 14 份**（约 3 周交易日，与 D-073 第 5 条
"本地留 1 份压缩基线"不冲突——那是每周基线，这是日粒度快照）。

**脚本不自动删除任何快照。** 每次运行输出 `total_snapshots` 和完整清单；
超出 14 份时在运行报告里标注 `over_retention`，由人手动清理
（`rm` 确切文件名），不复核不得删。

## 定时方式（方案文本，不安装）

脚本只交付、不启用。建议由发布/运维卡在批准后安装 launchd 任务：

- Label `com.tradingagents.ta-snapshot`
- `StartCalendarInterval`：周一–周五 16:30（批后、收盘后；
  脚本内部再用交易日历硬判，遇节假日自动跳过，所以按工作日排即可）
- `ProgramArguments`：
  `/usr/bin/env -u PYTHONPATH <repo>/.venv310/bin/python <repo>/scripts/ta_snapshot.py`
- 工作目录 `<repo>`，stdout/stderr 落 `work/ta-snapshot.{stdout,stderr}.log`
- `RunAtLoad` false

模板 plist 可参考 `scripts/com.tradingagents.tplus5-shadow.plist`
（同为"交易日批后"语义）。安装前必须经发布流程签字，本卡不安装。

## 验证

```bash
# 干跑（非交易日或周末也应能 --force-weekday 手动跑通）
python scripts/ta_snapshot.py --date 2026-10-05 --force-weekday
# 输出 JSON 含 snapshot 路径、字节数、total_snapshots、over_retention 标记
```
