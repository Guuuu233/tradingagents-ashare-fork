# LLM client consistency backlog

> 本文件是源码包内的历史技术架构备忘。P2-55（DAV-916）已按当前主线
> 完成策略分层与离线契约测试实现；详细设计与证据见
> `work/2026-09-14-p2-55-llm-client-audit.md`。

## 治理与策略实现结果（P2-55 / DAV-916）

### 1. `validate_model()` 与三层校验状态语义正式确立

历史遗留的 `validate_model()` 抽象方法已升级为具备完整状态分类的分层校验策略：
1. **Advisory（静态提示层）**：
   - 静态 `VALID_MODELS` 作为本地参考目录（Advisory Catalog），供设置页建议、联想补全及非阻塞提示使用。
   - 严禁将静态清单接成 `get_llm()` 或启动时的硬阻断门禁，避免阻断新发布官方模型或合法的自定义配置。
2. **Provider Discovery（动态发现层）**：
   - 通过 `/v1/models/fetch`（受 SSRF 严格防护的端点）拉取对端服务实时模型列表，动态确认为 `DISCOVERED_MATCH`。
3. **Warmup Failure（运行时探活硬门禁）**：
   - 配置保存和显式预热时通过真实轻量 probe（`_probe_runtime_config` / `_invoke_runtime_warmup`，`max_retries=0`）做连通性硬校验，Fail-closed 拦截 401（鉴权失败）、404（模型不存在）、代理路由失败等具体错误。

### 2. 自定义 OpenAI 兼容地址与开放提供商完全兼容

- 在 `evaluate_model_policy` 与 `validate_model(provider, model, base_url)` 中：
  - 当指定了非官方的自定义 `base_url`（如 DashScope、Moonshot、Baichuan、本地代理），未收录于静态清单的模型（如 `qwen-plus`）归类为 `CUSTOM_ENDPOINT_ALLOWED` 并放行。
  - 开放提供商（`ollama`、`openrouter`）按动态命名协议自动归类为 `PERMISSIVE_PROVIDER` 并放行。
  - 角色级模型配置（`evaluate_role_configurations`）支持多角色独立评估与继承，防止跨角色污染。

### 3. API key 与 `base_url` 边界已明确规范

- `api_key` 统一入口覆盖所有 client；Google 在 SDK 边界映射为 `google_api_key`。
- Anthropic `base_url` 自动去除末尾 `/v1`，Google client 保留代理检查但不向 SDK 传递非标参数。

### 4. 离线契约与不破坏现有运行代码保证

- 完整的离线契约测试已置于 `tests/test_llm_model_validation_contract.py`，全量覆盖构造、fake transport、角色继承与失败归类，不发起任何真实外部网络请求。
- 静态列表不得作为 provider 实时能力证明；生产部署仍须遵循独立发布门禁。
