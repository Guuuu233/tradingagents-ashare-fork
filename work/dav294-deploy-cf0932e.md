# DAV-294 部署 cf0932e（P0 安全修复 + 案例回填）

主干已核验：`target/codex/dav-4-p2a-trunk@cf0932ed1eed47c0f70e308b05923c1d6a002d27`
父链：`cfc1e22` → `a1626e7`（DAV-290 TA_API_KEY 泄漏修复）→ `cf0932e`（DAV-287 回填）。两候选独立终审 PASS（DAV-292/291），组合树全量回归 1584 passed/8 failed（失败均为已知非交易日用例）。

## 保护

禁止 reset --hard / clean -fd；禁止改 `.env`、providers、role_bindings、持久轮次。WIP 先保护再 FF。

## 部署

1. 确认远端主干仍为 `cf0932ed1eed47c0f70e308b05923c1d6a002d27`。
2. active reports（pending/running）= 0。
3. 宿主 FF 到该 SHA。
4. kill 旧 PID，lsof :8000 清空。
5. 完整 no_proxy + DATABASE_URL + `.venv310` 重启 uvicorn。
6. healthz 必须 = `cf0932ed…`。

## 冒烟

- 宿主树带 `.env` 跑 `pytest tests/test_models_fetch_ssrf.py -q`：必须 54 passed（证明 P0 修复在真实环境生效）。
- 启动日志出现 backfill_pending_cases 执行痕迹（non-fatal 或正常完成），historical_cases 表可查询。
- 持久配置仍 3/1；models 列表拉取正常。

不得 @项目调度助手。立即执行。
