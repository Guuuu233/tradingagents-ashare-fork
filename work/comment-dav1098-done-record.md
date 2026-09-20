## ✅ 合入与部署门执行完毕（总工放行后）

### 合入结果

- **主干新 tip**：`41772fa73c2f885252aa00c807e9db3c93563c9e`（`codex/dav-4-p2a-trunk`，已推送远端，`git ls-remote` 可复验）
- **合入方式**：fast-forward（`--ff-only` 实测成功），基点 `7a98819`，直接父 `4541cbc`（口径已更正入账，见前条）
- 工作区 `tradingagents/ api/ tests/` 干净，无未提交改动

### 部署门四项证据（执行时间 2026-09-20 01:08 CST 前后）

1. **备份**：`data/tradingagents.db.bak-20260920-pre-merge-41772fa`（771,129,344 字节，与生产库逐字节同尺寸，`cp -p` 保留时间戳）
2. **完整性核对**：生产库 `immutable=1` 只读实测 reports **1742** / completed **980**，与审计基线一致；备份库同口径 1742 ✓
3. **`/healthz` 精确回读**：返回 `commit_sha: 41772fa73c2f885252aa00c807e9db3c93563c9e`，`build_identity` 同源一致 ✓
4. **启动恢复证据**：启动日志干净——`Recovered stale active reports: failed=0`、历史案例 backfill `scanned=9, backfilled=0`、startup complete，无错误堆栈

### 服务状态

- `127.0.0.1:8000` LISTEN（pid 17940），启动命令带完整 `no_proxy` 清单与 `env -u PYTHONPATH`（`.venv310`，3.10.20）
- 数据库 `DATABASE_URL=sqlite:///./data/tradingagents.db`（服务正常写连接为预期行为；本轮全程未用其他可写连接碰生产库）

### 部署后验收（四条并列）状态

服务已就绪，但**受控真实分析尚未跑**——四条验收中的「外盘数值对照 Tushare」「partial 标记」「陈旧值落【数据缺失】」需要一次真实分析才能取证。建议由调度助手安排受控真实分析后逐条核对。本卡到此收口。
