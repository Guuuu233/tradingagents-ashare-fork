# 生产库符号清洗：写入后核验

## 结论

授权内的四行原子 `UPDATE` 已提交，并通过 `wal_checkpoint(TRUNCATE)` 落盘。没有物理替换正在被 8000 服务使用的数据库文件。

## 现场证据

- 服务：PID `64097`，运行 SHA `df7538413ba7bb55593b1757feaf90b0bd514d1c`
- 生产库 SHA256：`c8e52938e0908aefad6bc37e2de258acb32419bd82016912d87dce4a0a93c883`
- 备份：`work/tradingagents.db.bak-20260918-pre998replay`
- reports 总数：`1410`
- 状态守恒：`completed=794`、`failed=616`
- SQLite `quick_check`：`ok`
- 当前 WAL 文件大小：`0`

## 映射回读

| 目标符号 | 写入后数量 | 裸码残留 |
|---|---:|---:|
| `600519.SH` | 741 | 0 |
| `000001.SZ` | 59 | 0 |
| `603259.SH` | 12 | 0 |

隔离行仍为 34 条：32 条空值、`AGENT`、`AUUSDO`。这些属于 quarantine 设计，不被猜测映射。

## 边界

本证据只证明符号清洗、守恒、落盘和服务存活；不把 H1b 加权或社交真实采集视为已解锁。
