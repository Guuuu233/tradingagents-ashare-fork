# DAV-301 部署 ab3cda6（全球指数回退 + 设置页 URL 级联）

主干已核验：`target/codex/dav-4-p2a-trunk@ab3cda62bc676562c9421f809c0f1fa95d62f34e`
父链：`346af80` → `bb6ecc3`（DAV-297 全球指数，DAV-298 PASS）→ `ab3cda6`（DAV-299 URL 级联，DAV-300 PASS）。
组合树全量回归 **1613 passed / 0 failed / 1 skipped**。

## 保护

禁止 reset --hard / clean -fd；禁止改 `.env`、providers 数据、role_bindings、持久轮次。WIP 先 stash 再 FF。

## 部署

1. 确认远端主干仍为 `ab3cda62…`。
2. active reports = 0。
3. 宿主 FF 到该 SHA。
4. kill 旧 PID，lsof :8000 清空。
5. 完整 no_proxy + DATABASE_URL + `.venv310` 重启 uvicorn。
6. healthz 必须 = `ab3cda62…`。

## 冒烟

- 角色解析 15 个角色 base_url host = `100.65.130.33:8317`。
- `route_to_vendor('get_global_indices','2026-08-21')` 不得整包失败（至少 1 个指数真值，或显式【数据缺失】分项，禁止空串）。
- 持久配置仍 3/1。

不得 @项目调度助手。
