# DAV-300 只读终审：DAV-299 设置页 URL 级联到 providers

- 目标 SHA：`7b1a2baa670599400802e51aa307fdf8df2fe335`
- 基线父提交：`346af8049ddd2a0d8c9c69f3147732de0537ec0a`（必须为直接父）
- 白名单：api/services/auth_service.py、tests/test_provider_url_cascade.py

## 缺陷（已坐实）

PATCH /v1/config 只写 user_llm_configs.backend_url。15 个分析角色走 role_bindings → model_profiles → providers.base_url。用户设置页已是 Tailscale `http://100.65.130.33:8317/v1`，providers 四条仍是 7-28 公网 `92.119.124.146:8317`，角色请求 SYN_SENT 超时。

## 审核点

1. 父链正确、无越白名单、无 .env / role_bindings / 辩论轮次改动。
2. upsert_user_llm_config 在 backend_url 非空时级联更新**该 user_id** 的全部 ProviderDB.base_url；api_key / clear_api_key 同样级联 api_key_encrypted。
3. 不级联其他用户；不改模型名、不改 role_bindings。
4. 测试：保存 URL 后本用户 providers 全变、其他用户不变；保存/清空 Key 同步。
5. pytest tests/test_provider_url_cascade.py tests/test_config_fallback.py + compileall + git diff --check。

结论 PASS/FAIL + 证据。禁止合主干、禁止部署。不得 @项目调度助手。
