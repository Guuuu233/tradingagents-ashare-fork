# DAV-2322：解释器、代码与运行数据路径

本候选基于主干 `66ad35a886917731e0c16309bda308363d0dafb8`。本卡只交分支与证据；合入、部署和已加载 plist 的迁移由后续发布卡承接，随 DAV-2269 发布。

## 每日批日历

`work/daily-batch/run_daily.py` 默认读取
`{sys.prefix}/lib/python{major}.{minor}/site-packages/akshare/file_fold/calendar.json`。
Python 版本来自运行解释器，日历不再受 `--root` / `TA_DAILY_ROOT` 影响。
这两项仍决定数据库和旧账户文件的位置。

需要隔离测试或显式指定日历时，可设置 `TA_DAILY_CALENDAR`。
缺失或损坏的日历仍直接报错，不回退到数据根目录的 venv，也不把周一至周五当成交易日。
解析日期、交易日轮换、账户校验及请求流程保持原有语义。

## 快照包装器与候选 plist

`work/ta-snapshot/run_snapshot.py` 的解释器改为 `sys.executable`；
代码根目录、`scripts/ta_snapshot.py`、状态目录和子进程 cwd 从包装器自身位置解析。
发布目录须保留 `work/ta-snapshot/` 与 `scripts/` 的相对布局，以及快照子脚本依赖的项目包。
现有 `dav-snap-8571` 部署符合此布局。

数据独立于代码目录，默认继续读取现有位置：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `TA_SNAPSHOT_SOURCE_DB` | `/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db` | 传给快照脚本的活库输入 |
| `TA_SNAPSHOT_BATCH_DIR` | `/Users/davidliu/Documents/TradingAgents-AShare/work/daily-batch` | 等待目标交易日的 `summary.json` |

`TA_SNAPSHOT_SOURCE_DB` 与日报读取共享静态快照所用的 `TA_SNAPSHOT_DB` 分开命名，避免误把 `current.db` 当活库输入。
当每日批迁到独立检出后，发布卡须同步更新 `TA_SNAPSHOT_BATCH_DIR` 到实际摘要产出位置。
本卡不迁移活库、每日批产出或共享快照。

新增的 `releases/launchd/com.tradingagents.ta-snapshot.plist` 是**待发布模板**。
相对本机现装 plist，仅 `ProgramArguments[0]` 从主检出的 `.venv310/bin/python`
改为独立解释器 `/Users/davidliu/ta-venv/py310/bin/python`。
Label、包装器路径、WorkingDirectory、日志、600 秒间隔、周一至周五 19:30 和 `RunAtLoad=false` 均保留。

发布卡先同步经审查的包装器及其依赖代码、核对新解释器依赖和日历，再安装模板；
仅切 plist 而未同步包装器，旧包装器仍会启动主检出的解释器。
回退时恢复该发布批备份的包装器与 plist 配对，不更换主检出 HEAD，不移动数据。
本卡未安装模板、未操作 launchd、未运行真实快照或分析。

## 验证范围

`tests/test_dav2322_runtime_paths.py` 在临时目录验证默认日历、显式日历、
`--root` 覆盖后日历不变、日历缺失/损坏拒绝回退，以及快照子进程的解释器、代码 cwd、数据参数和完成标记。
既有每日批测试显式传入隔离日历；60 GiB 磁盘守卫测试在临时部署布局下继续运行。
全部验证须使用 `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`。
