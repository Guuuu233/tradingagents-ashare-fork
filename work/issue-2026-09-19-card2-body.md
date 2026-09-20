# 卡 2：Tushare 网关可用性采样（纯探测，不改代码）

背景与证据：`work/2026-09-19-global-indices-data-source-audit.md` §4.4（网关抖动）与「卡 2」节。
派工边界：`work/2026-09-19-global-indices-dispatch-brief.md` §3.2、§6、§7。

**已定案前提**：Tushare 三表（`income`/`balancesheet`/`cashflow`）网关稳定性**未确定**——
三方单轮/三轮探测结果互相矛盾（见正文第三部分被推翻判断「Tushare 三表网关不稳定/稳定」）。
单次探测结果一律不足以判定。本卡产出是卡 3（财报接入）的开工前置。

## 任务

纯探测，**不改任何代码、不改配置**：

1. 对 `income` / `balancesheet` / `cashflow` / `daily_basic` / `daily` 五个接口做重复采样：
   - **≥20 轮**，分散在不同时段（含盘中/盘后）；
   - 每轮记录成功/失败、延迟、失败分类（timeout / TLS EOF / 连接重置 / 限流 / 其他）。
2. 统计：成功率、P50/P95 延迟、失败分类分布。
3. 产出可用性结论与不确定性说明，写入 `work/` 下的采样报告（新文件，不覆盖既有文档）。

## 边界与红线

- **不得**以单次（或少轮）探测结果判定可用或不可用。
- Tushare 凭据从 `.env` 读（`TUSHARE_TOKEN`），复用仓库既有封装
  `industry_linkage_provider._query_tushare_api`，不另起一套。
  **不得把 token 打印、写入日志、提交进仓库或贴进卡片/报告。**
- 解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，
  报告须贴 `-V`。
- 如需核对生产库：只读且用 `file:...?immutable=1`（当前库无 `-wal/-shm`，
  `mode=ro` 会报 unable to open (14)，这不是库损坏）。**不得开可写连接。**
- 本卡不含部署、不含服务重启（线上服务当前停机，属已知状态，与本卡无关）。

## 交回时请报告

采样轮次、时段分布、各接口成功率、P50/P95 延迟、失败分类明细、结论（可用/不可用/仍不确定）
及证据局限。完成后**主动精确 mention `项目调度助手`**，由调度助手评估是否解锁卡 3。
