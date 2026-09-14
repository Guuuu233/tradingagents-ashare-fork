# P2-55：LLM 模型校验策略定义与契约测试交付报告

> 核验日期：2026-09-14（Australia/Perth）
> 代码候选 SHA：`8e85935793ea48c1ca0a844e3dcd3b5af532067f`
> 直接父提交（基线）：`3aa5be403c1216b79c8f491975535021d55709b3`
> 线上运行代码基准：`79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`
> 审计证据参考：`work/2026-09-14-p2-55-llm-client-audit.md`
> 责任人：资深开发1（`6050b57e-f551-4756-8ad9-3af522d7d4e3`）

---

## 1. 目标与背景

本任务（DAV-916 / P2-55）响应全仓审计中对 `tradingagents/llm_clients/TODO.md` 的遗留项治理要求。
核心目标是：**把 LLM client 的模型名校验从历史未成型的 TODO 变成可执行、分层明确且完全兼容现状的健壮策略，并交付全量离线契约测试**。

系统在设计上严禁将静态 `VALID_MODELS` 直接硬编码为 `get_llm()` 或启动初始化的 Hard Gate。如果将其作为硬阻断，会导致使用 OpenAI 兼容端点（如 DashScope 通义千问 `qwen-plus`、Moonshot、Baichuan、DeepSeek 等）的用户无法启动，或在上游发布新模型时造成合规配置瘫痪。

---

## 2. 边界一：当前调用链复核与三层校验状态语义

### 2.1 当前调用链全景复核

全仓静态核对表明，运行时全链条中**从未调用过 `validate_model()`**：
1. **图构建链路** (`tradingagents/graph/trading_graph.py:220-255`)：
   - 角色模型 (`role_client`)、深思考 (`deep_client`)、快思考 (`quick_client`) 均通过 `create_llm_client(...)` 构造，随后直接调用 `.get_llm()`。未调用 `validate_model()`。
2. **API 入口链路** (`api/main.py`)：
   - 自然语言抽取 (`_ai_extract_symbol_and_date` / `_ai_extract_symbol_and_date_streaming`)：直接构造 client 并使用 LLM 实例。未调用 `validate_model()`。
   - 报告任务执行 (`api/services/report_service.py:1059`)：构造后直接调用 `get_llm()`。未调用 `validate_model()`。
   - 运行时探活 (`_probe_runtime_config` / `_invoke_runtime_warmup`)：构造时传入真实超时并调用 `invoke(prompt)` 验证，依靠对端实际响应判定连通性，未执行静态名单核对。
3. **客户端与基类现状** (`tradingagents/llm_clients/`)：
   - `BaseLLMClient` 仅定义抽象方法 `validate_model() -> bool`。
   - `OpenAIClient`、`AnthropicClient`、`GoogleClient` 实现了该方法，底层调用 `validators.py` 中的 `validate_model(provider, model)`。
   - 旧逻辑若在 `base_url` 自定义（如阿里云兼容端点）时核对 `provider="openai", model="qwen-plus"`，会返回 `False`，存在误杀合法兼容配置的隐患。

### 2.2 三层校验状态语义定义

为彻底解耦“静态指导”、“协议能力发现”和“运行时连通性”，确立三层状态语义：

```
+-------------------------------------------------------------------------+
| Level 1: Advisory (静态参考目录层)                                       |
| - 范围：本地已知模型静态清单 VALID_MODELS                                |
| - 语义：Non-blocking 弱校验与联想提示。未命中时产生 ADVISORY 状态，绝不阻断 |
| - 接入点：前端设置页建议、自动补全、配置保存时的提示性 warning 日志       |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Level 2: Provider Discovery (协议/动态发现层)                            |
| - 范围：上游 /v1/models/fetch（经 SSRF 保护的受限连接）                   |
| - 语义：动态确认模型存在性（DISCOVERED_MATCH）。拉取失败降级为 Advisory   |
| - 接入点：前端设置页「拉取模型」按钮及后端能力探测接口                   |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Level 3: Warmup Failure (运行时探活硬门禁)                              |
| - 范围：_probe_runtime_config / _invoke_runtime_warmup (max_retries=0)   |
| - 语义：Fail-closed 拦截真实连通性故障（401 Key无效、404 模型不存在等）   |
| - 接入点：设置保存前的连通性测试按钮、系统冷启动时的显式健康检查          |
+-------------------------------------------------------------------------+
```

1. **Advisory（静态提示/目录层）**：
   - **语义**：基于代码库内置的 `VALID_MODELS` 提供静态参考建议。
   - **状态枚举**：
     - `CATALOG_MATCHED`：标准官方端点下的已知目录模型；
     - `ADVISORY_UNRECOGNIZED`：标准端点下的未知模型（可能是刚发布的新模型或用户自定义模型），**默认允许执行（is_supported=True, is_advisory=True）**，仅在显式 strict 模式下标记不支持；
     - `EMPTY_MODEL`：模型名为空或纯空格，拒绝。
   - **推荐接入点**：前端设置页（`Settings.tsx`）输入框失焦提示、模型下拉推荐，以及 `api/main.py` 保存配置时的非阻塞日志。
2. **Provider Discovery（提供商动态能力发现）**：
   - **语义**：通过现有的 `/v1/models/fetch` 接口从端点拉取其当前实时支持的模型清单（经 SSRF 防护）。
   - **状态枚举**：
     - `DISCOVERED_MATCH`：模型已在对端 `/v1/models` 返回的清单中，确认支持；
     - `DISCOVERY_UNAVAILABLE`：上游未实现 models 接口或网络不可达，优雅降级到 Advisory 判定，不产生误阻断。
   - **推荐接入点**：设置页「拉取模型」操作与配置变更校验流。
3. **Warmup Failure（运行时探活/连通性硬校验）**：
   - **语义**：通过真实发送轻量 Prompt（如 `"ping"`）调用 `invoke()` 进行探活，以端点真实返回为准。
   - **状态映射（Fail-closed）**：
     - `AUTH_FAILURE`：HTTP 401 / AuthenticationError，明确指出 API Key 错误；
     - `MODEL_NOT_FOUND`：HTTP 404 / ModelNotFoundError，明确指出模型名不存在或当前 Key 无法访问；
     - `RATE_LIMIT`：HTTP 429 / ResourceExhausted，提示配额耗尽或限流；
     - `PROXY_ROUTING`：`LLMProxyRoutingError`，提示修复 `no_proxy` 字面量 IP；
     - `NETWORK_TIMEOUT`：连接超时或网络不可达。
   - **推荐接入点**：现有的 `_probe_runtime_config` 和 `_invoke_runtime_warmup` 函数。

---

## 3. 边界二：兼容矩阵设计

针对六类核心场景及各层响应，建立标准化兼容矩阵：

| 场景分类 | 具体配置示例 | 静态 Advisory 状态 | 动态 Discovery 响应 | 运行时 Warmup 行为 | 最终系统行为 |
|---|---|---|---|---|---|
| **标准官方模型** | OpenAI `gpt-4o`<br>Anthropic `claude-sonnet-4-5`<br>Google `gemini-2.5-pro` | `CATALOG_MATCHED`<br>(is_supported=True) | 若拉取成功：`DISCOVERED_MATCH` | probe 返回 200，内容正常 | **完全放行**，无告警 |
| **标准端点未知/新模型** | `provider=openai`<br>`model=gpt-5.5-preview`<br>`base_url=None` | `ADVISORY_UNRECOGNIZED`<br>(is_supported=True,<br>is_advisory=True) | 若上游已发布：`DISCOVERED_MATCH`；若未发布：提示不在列表 | 依赖实际调用：若官方已有该模型则成功；若无返回 404 | **允许启动与调用**；若 probe 报错 404 则阻断配置保存 |
| **自定义兼容地址** | `provider=openai`<br>`model=qwen-plus`<br>`base_url=https://dashscope.aliyuncs.com/...` | `CUSTOM_ENDPOINT_ALLOWED`<br>(is_supported=True,<br>is_advisory=True) | 从兼容端点拉取模型列表，匹配后转为 `DISCOVERED_MATCH` | 对兼容端点发送 ping；鉴权或网络失败时捕获 | **完全支持**；打破旧静态白名单误杀，兼容通义千问等所有第三方服务 |
| **开放动态提供商** | `provider=ollama`<br>`model=llama3:latest`<br>或 `openrouter`<br>`model=deepseek/r1` | `PERMISSIVE_PROVIDER`<br>(is_supported=True,<br>is_advisory=False) | 由 provider 自身返回本地/路由模型清单 | 连接本地 11434 或 openrouter 探活 | **原生完全放行**，不设任何静态模型名约束 |
| **角色级多模型配置** | `research_manager: gpt-5`<br>`bull: deepseek-r1`<br>`bear: qwen-plus (dashscope)` | 逐角色独立判定：<br>- manager: MATCHED<br>- bull: ADVISORY<br>- bear: CUSTOM_ALLOWED | 各角色可绑定独立 provider 与 base_url，独立拉取 | `_invoke_runtime_warmup` 聚合各角色目标，分别发送轻量 probe | **角色间配置严格隔离**，缺省继承 global deep/quick，无交叉污染 |
| **空模型 / 缺失配置** | `model=""` 或纯空格 | `EMPTY_MODEL`<br>(is_supported=False) | N/A | 在发起 HTTP 请求前抛出 400 Bad Request | **前端与后端双重拦截** |

---

## 4. 边界三：离线契约测试证据与代码变更说明

### 4.1 离线契约测试实现 (`tests/test_llm_model_validation_contract.py`)

新增的 50 项聚焦契约测试严格遵循离线与无外部打网原则：
1. **测试用例分类与覆盖**：
   - `TestAdvisoryCatalog`（18 个测试）：验证已知官方模型目录匹配、空模型名拒绝、标准端点未知模型的非阻塞 Advisory 语义及 strict 模式对比；
   - `TestCustomEndpointAndCompatibility`（7 个测试）：验证各种自定义 OpenAI 兼容地址（DashScope、Moonshot、Baichuan、本地代理 IP）对 uncataloged 模型的自动放行机制与 base_url 识别逻辑；
   - `TestPermissiveProviders`（9 个测试）：验证 Ollama（本地任意 tag）与 OpenRouter（斜杠命名协议）的动态模型放行；
   - `TestProviderDiscoveryIntegration`（2 个测试）：模拟 dynamic discovery 清单注入，验证发现确认（`DISCOVERED_MATCH`）与回退逻辑；
   - `TestRoleLevelConfigurations`（1 个测试）：模拟多角色独立配置与全局 fallback 继承（研究总监与风控默认 deep 档，研究员默认 quick 档），验证无跨角色配置泄露；
   - `TestClientMethodsAndContract`（5 个测试）：验证 `OpenAIClient`、`AnthropicClient`、`GoogleClient` 与 `create_llm_client` 工厂的校验和状态评估方法；
   - `TestOfflineConstructorAndFakeTransport`（2 个测试）：
     - 验证 `get_llm()` 构造未收录模型时绝不抛出 HardGate 异常（确保启动稳定性）；
     - 注入 `httpx.MockTransport` 验证 `UnifiedChatOpenAI` 离线调用链路完整可用；
   - `TestFailureClassification`（5 个测试）：验证 401（Auth）、404（NotFound）、429（RateLimit）、ProxyRoutingError 与网络超时的结构化分类归约；
   - `TestBackwardCompatibility`（1 个测试）：确保旧签名 `validate_model(provider, model)` 与现有行为 100% 兼容。

### 4.2 实测证据（Python 3.10.20 固定环境）

执行命令：
```bash
/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_llm_model_validation_contract.py tests/test_llm_client_factory.py tests/test_llm_proxy_guard.py tests/test_model_tier_warning.py tests/test_role_llms_user_id.py tests/test_models_fetch_ssrf.py
```

实测输出：
```text
============================= test session starts ==============================
platform darwin -- Python 3.10.20, pytest-9.0.3, pluggy-1.6.0
rootdir: /Users/davidliu/multica_workspaces_desktop-api.multica.ai/davidsworks-d70c6ff76b54/dav-916-62314eb5897f/workdir/tradingagents-ashare-fork
configfile: pyproject.toml
plugins: anyio-4.9.0, cov-7.1.0, langsmith-0.7.31
collected 170 items

tests/test_llm_model_validation_contract.py ............................ [ 16%]
......................                                                   [ 29%]
tests/test_llm_client_factory.py ...                                     [ 31%]
tests/test_llm_proxy_guard.py ..................                         [ 41%]
tests/test_model_tier_warning.py ....................................... [ 64%]
....                                                                     [ 67%]
tests/test_role_llms_user_id.py ..                                       [ 68%]
tests/test_models_fetch_ssrf.py ........................................ [ 91%]
..............                                                           [100%]

============================= 170 passed in 0.64s ==============================
```
- 本卡新增专项契约测试：**50 passed**；
- 关联 LLM 相关既有全套测试：**120 passed**；
- 聚合测试总数：**170 passed，0 failed，0 skipped**。

### 4.3 运行代码变更范围说明

为支撑上述契约，代码变更严格限定在 `tradingagents/llm_clients` 包内：
1. `tradingagents/llm_clients/validators.py`：
   - 增加 `ModelValidationStatus`、`ModelValidationResult`、`FailureCategory` 数据结构；
   - 增加 `is_custom_base_url`、`evaluate_model_policy`、`evaluate_role_configurations`、`classify_llm_failure` 策略函数；
   - 保持 `validate_model` 向后兼容，增加可选 `base_url` 参数，自定义端点自动放行。
2. `tradingagents/llm_clients/base_client.py`：
   - 增加 `evaluate_model()` 方法，向调用方提供富状态校验对象。
3. `tradingagents/llm_clients/openai_client.py` / `anthropic_client.py` / `google_client.py`：
   - `validate_model()` 透传 `self.base_url`，确保兼容端点与自定义代理正确定位。
4. `tradingagents/llm_clients/__init__.py`：
   - 导出上述新增策略类型与函数。
5. **特别声明**：未修改 `get_llm()`，未在初始化链引入任何硬阻断异常，完全遵循“不把静态列表作为实时证明/启动硬门禁”的原则。

---

## 5. 边界四：源码包内 TODO 更新与治理证据

`tradingagents/llm_clients/TODO.md` 已全面更新：
- 澄清历史四项条目的真实工程现状；
- 记录 P2-55 模型校验策略的分层设计决策；
- 明确标注 `VALID_MODELS` 仅为 Advisory Catalog，绝不冒充实时能力；
- 明确说明运行时 client 的无硬门禁设计理由与离线契约保障。

---

## 6. 禁令与红线遵守审查

对照任务要求进行逐项合规审查：
- [x] **无真实模型调用**：全部测试使用构造自检、逻辑枚举或 `httpx.MockTransport`，无任何外网流量；
- [x] **无生产数据库写入**：未操作任何 SQLite 数据文件；
- [x] **无凭据/Cookie 操作**：测试均使用模拟 fake key，未触碰系统秘钥库；
- [x] **无服务重启或部署**：未杀死进程、未启动新容器或后台进程；
- [x] **无信用加权/社交采集/历史重写**：严格隔离于 LLM client 目录；
- [x] **未修改 API key 或 Google SDK base_url 既有语义**：Google SDK 边界保持 `google_api_key` 原样映射，Anthropic 末尾 `/v1` 处理保持原状。

---

## 7. 最终建议与后续流程

1. **建议采纳**：正式采纳本卡建立的“三层模型校验策略（Advisory + Discovery + Warmup）”，后续若在设置页 UI 增加模型校验提示，应仅对接 Advisory 层作为非阻塞辅助提示，严禁将其升级为报错拦截。
2. **审查派工建议**：代码候选提交完成后，由项目调度助手将本卡完整 40 位 SHA 派发给 **代码审核员**（统一口径，不得派发给“独立代码审核员”）。
3. **部署口径**：本卡为策略与契约测试落地，不涉及生产环境上线或服务重启。
