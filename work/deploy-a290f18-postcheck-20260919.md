# a290f18 受控部署核验（2026-09-19）

## 发布对象
- 远端 trunk 回读 = `a290f187f18635dfc2c4890cea2645bed9f09f1a`（`git ls-remote` 逐位一致）
- 部署 worktree `/private/tmp/ta-serve-a290f18`，PID 1018（PPID=1，setsid detached）
- `/healthz` 精确回读 `commit_sha=a290f18…`，`executor_queued=0`

## 部署门执行
- 部署前 SQLite `.backup()`：`backup_ok ('ok', 1735, 977, 758)`，备份 `work/tradingagents.db.bak-20260919-021456-deploy-a290f18`（sha256 `fc7e7628…`）
- 端口 8000 预检无监听（服务停机状态下部署，非替换运行中实例）
- 启动 18s 后 `/healthz` ready

## 烟测
- `GET /` → 200（前端 dist 已随 worktree 部署）
- `GET /v1/reports` → 200
- `GET /v1/__release_probe__` → 404（API 404 闸）
- `GET /v1/social-data/status` → `mode=shadow`、`status=operational`，xhs/dy 均 operational
- social_archive `quick_check=ok`、`social_record_snapshots=357`

## 守恒
- 生产库主文件部署前后 mtime `Sep 18 21:18` 不变、SHA256 `7ec9aa51…` 不变，零写入
- 部署未授权真实分析、社交 active、Cookie、信用加权；TA_SOCIAL_MODE 保持 shadow

## 修复说明（相对上次脚本）
`deploy-4b540b0.sh` 的 `ps eww` 进程环境检查不适用于 dotenv 加载方式；本次改在 `.env` 显式固定 `TA_SOCIAL_MODE=shadow / TA_SOCIAL_PLATFORMS=xhs,dy / TA_SOCIAL_ARCHIVE_DB=…/social_archive.db`，运行态以 `/v1/social-data/status` 实际回读为准（shadow 已确证）。

## 未做
- 未跑真实分析验证 ABSTAIN 修复效果（须受控真实报告，另行授权）
- 未改 DECISIONS.md/PROJECT_STATE.md 提交状态（台账更新待 David 确认后提交）
