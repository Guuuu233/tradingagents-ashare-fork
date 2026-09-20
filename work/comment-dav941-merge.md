## ✅ 已合入主线——本项目**首次**以完整 RT-FULL 对照放行

`origin/codex/dav-4-p2a-trunk`：`28d1adc6` → **`5a0320f0618d...`**（快进）

- 候选 `75e3c359858ccc908af3ec04be06e021a4bef647`（`origin/agent/1/dav-941-r9`）
- 审查：DAV-994 ✅ PASS
- 白名单：`tradingagents/knowledge/historical_cases.py`、`tests/test_historical_cases.py`

### RT-FULL 失败集合对照（D-012 §4b，完整口径）

两侧均为 `.venv310`（Python 3.10.20）、`-q -p no:randomly`、隔离 `DATABASE_URL`、**不设看门狗**：

| | 基线 `28d1adc6` | 候选（cherry-pick 到当前 tip） |
|---|---|---|
| 结果 | `20 failed, 4777 passed, 1 skipped, 3 deselected` | `20 failed, 4801 passed, 1 skipped, 3 deselected` |
| 耗时 | 1700.10s (28:20) | 1652.40s (27:32) |
| 失败集合 | 20 项 | **同样 20 项** |
| 新增失败 | — | **0 项** |
| 修复的既有失败 | — | 0 项 |
| 净增通过用例 | — | **+24**（候选自带测试） |

`comm` 逐项比对两侧失败清单，**差集为空**。

这是主干第一次用完整 RT-FULL（而非分文件预检）完成放行对照——此前因看门狗误判为「死锁」而一直拿不到基线，详见 DAV-979 的两条更正评论。

特别说明：本候选修改的 `historical_cases.py` 正位于 `calculate_t1_return → route_to_vendor` 这条慢路径上（基线中 7 条 181.3s 用例即源于此），对照显示候选未加剧该问题（总耗时 1652s < 基线 1700s）。

### 未触碰

未部署、未重启、未写生产库。`data/tradingagents.db` SHA256 保持 `94d2f6740db4f206...`。

### 备注

本轮候选侧首次全量曾被静默中断（日志停在 14:47、无 summary），疑似其他并发运行执行了 `pkill -f pytest` 误伤。已改为前台重跑取得完整结果。**请各位不要使用宽泛的 `pkill -f pytest`**，会误杀他人正在进行的门禁跑；如需清理请按 PID 精确处理。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
