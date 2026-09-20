# TradingAgents-AShare 分角色模型配置 · 阶段 0 探查报告 (MODEL_CONFIG_AUDIT.md)

本探查报告针对 `KylinMountain/TradingAgents-AShare` 项目的模型配置架构进行了全面审计，为后续“分角色模型配置”重构提供代码级定位与决策依据。

---

## 探查结果一览

### 1. 模型配置存储层
- **文件路径**: `api/database.py` (Line 347–362)
- **数据库表结构**: `user_llm_configs`
  - `user_id` (`VARCHAR(36)`, Primary Key, Index) — 用户 ID
  - `llm_provider` (`VARCHAR(50)`) — 全局单一厂商（如 `openai`, `anthropic`, `google` 等）
  - `backend_url` (`VARCHAR(500)`) — API Base URL 端点
  - `quick_think_llm` (`VARCHAR(255)`) — 常规模型名称
  - `deep_think_llm` (`VARCHAR(255)`) — 推理/深度模型名称
  - `max_debate_rounds` (`INTEGER`) — 多空辩论最大轮数
  - `max_risk_discuss_rounds` (`INTEGER`) — 风控讨论最大轮数
  - `api_key_encrypted` (`TEXT`) — 加密后的 API Key
  - `wecom_webhook_encrypted` (`TEXT`) — 加密后的企微 Webhook
  - `default_analysts` (`TEXT`, JSON String) — 默认启用的分析师列表

---

### 2. API Key 加密与解密实现
- **文件路径**: `api/services/auth_service.py` (Line 53–75)
- **加密机制**:
  - 基于对称加密算法 **Fernet** (`cryptography.fernet.Fernet`)
  - 秘钥生成逻辑：使用 `TA_APP_SECRET_KEY` 环境变量（或默认开发秘钥 `"tradingagents-ashare-dev-secret"`）的 `SHA-256` 摘要派生 Fernet 密钥 (`_fernet_from_key`)。
  - 函数入口：
    - `encrypt_secret(value: str) -> str`
    - `decrypt_secret(value: Optional[str]) -> Optional[str]`
    - `decrypt_secret_with_fallback(...)`（支持系统秘钥迁移）
- **存储模式**: 目前为**单用户单条记录**（`user_llm_configs` 表中以 `user_id` 为主键，存储单一加密 `api_key_encrypted`）。

---

### 3. LLM 实例化位置
- **工厂函数**: `tradingagents/llm_clients/factory.py` (Line 9–41)
  - `create_llm_client(provider, model, base_url, **kwargs)` 返回 `BaseLLMClient` 的派生类对象。
- **具体适配器客户端**:
  - `OpenAIClient` (`tradingagents/llm_clients/openai_client.py`): 适配 OpenAI 兼容 API (`langchain_openai.ChatOpenAI`)。
  - `AnthropicClient` (`tradingagents/llm_clients/anthropic_client.py`): 适配 Anthropic API (`langchain_anthropic.ChatAnthropic`)。
  - `GoogleClient` (`tradingagents/llm_clients/google_client.py`): 适配 Google Gemini (`langchain_google_genai.ChatGoogleGenerativeAI`)。
- **图构建时实例化**: `tradingagents/graph/trading_graph.py` (Line 92–106)
  - 通过 `create_llm_client(...)` 构造 `quick_client` 与 `deep_client`。
  - 调用 `.get_llm()` 获取底层 LangChain Chat Model 对象赋值给 `self.quick_thinking_llm` 与 `self.deep_thinking_llm`。

---

### 4. Agent 与 LLM 的绑定点
- **文件路径**: `tradingagents/graph/trading_graph.py` (Line 125–142) 及 `tradingagents/graph/setup.py` (Line 57–190)
- **绑定逻辑**:
  - `TradingAgentsGraph.__init__` 将 `self.quick_thinking_llm` 与 `self.deep_thinking_llm` 注入 `GraphSetup` 构造函数。
  - `GraphSetup.setup_graph()` 中使用 `factories` 动态创建节点：
    - 分析师节点（`market`, `social`, `news`, `fundamentals`, `macro`, `smart_money`, `volume_price`）、多头研究员（`bull_researcher`）、空头研究员（`bear_researcher`）、交易员（`trader`）、风控发言人（`aggressive`, `neutral`, `conservative`）统一绑定传入的 `self.quick_thinking_llm`。
    - 研究总监/投研经理（`research_manager`）与风险裁决（`risk_manager`）统一绑定传入的 `self.deep_thinking_llm`。

---

### 5. 全部 Agent 角色及其内部标识符清单
经过审计，系统内共有 **15 个节点/角色**：

| # | 角色分类 | 角色显示名称 | 内部 Identifier (`role_key`) | 默认绑定 Tier | 工厂函数位置 (`tradingagents/agents/`) |
|---|---|---|---|---|---|
| 1 | 分析师 | Market Analyst | `market` | `quick` | `analysts/market_analyst.py` |
| 2 | 分析师 | Social Analyst | `social` | `quick` | `analysts/social_media_analyst.py` |
| 3 | 分析师 | News Analyst | `news` | `quick` | `analysts/news_analyst.py` |
| 4 | 分析师 | Fundamentals Analyst | `fundamentals` | `quick` | `analysts/fundamentals_analyst.py` |
| 5 | 分析师 | Macro Analyst | `macro` | `quick` | `analysts/macro_analyst.py` |
| 6 | 分析师 | Smart Money Analyst | `smart_money` | `quick` | `analysts/smart_money_analyst.py` |
| 7 | 分析师 | Volume Price Analyst | `volume_price` | `quick` | `analysts/volume_price_analyst.py` |
| 8 | 研究员 | Bull Researcher | `bull_researcher` | `quick` | `researchers/bull_researcher.py` |
| 9 | 研究员 | Bear Researcher | `bear_researcher` | `quick` | `researchers/bear_researcher.py` |
| 10| 裁决/经理 | Research Manager | `research_manager` | `deep` | `managers/research_manager.py` |
| 11| 执行 | Trader | `trader` | `quick` | `traders/trader.py` |
| 12| 风控 | Aggressive Analyst | `aggressive_analyst` | `quick` | `risk_mgmt/aggressive_debator.py` |
| 13| 风控 | Neutral Analyst | `neutral_analyst` | `quick` | `risk_mgmt/neutral_debator.py` |
| 14| 风控 | Conservative Analyst | `conservative_analyst` | `quick` | `risk_mgmt/conservative_debator.py` |
| 15| 风控 | Risk Judge | `risk_manager` | `deep` | `risk_mgmt/risk_manager.py` |

---

### 6. 厂商适配层实现
- **接口工厂**: `tradingagents/llm_clients/factory.py`
  - 适配类型：`openai` / `ollama` / `openrouter` / `xai` / `deepseek` 映射至 `OpenAIClient`；`anthropic` 映射至 `AnthropicClient`；`google` 映射至 `GoogleClient`。
  - 目前所有兼容 OpenAI 格式的中转厂商（如 DeepSeek、SiliconFlow、Moonshot、智谱等）均可使用 `openai` 类型并提供自定义 `base_url` 来统一接入。

---

### 7. Warmup 连通性测试逻辑
- **API 路由**: `POST /v1/config/warmup`
- **实现文件**: `api/main.py` (Line 3650–3830, Line 4008–4021)
- **运作逻辑**:
  - `_warmup_model_targets(config)` 提取配置中去重后的 `quick_think_llm` 与 `deep_think_llm` 模型列表。
  - `_invoke_runtime_warmup(...)` 循环对每个模型调用 `create_llm_client(...)` 构造临时 Client。
  - 使用 `llm.invoke("你好")` 发起一次轻量级请求测试连通性，并返回结果与错误提示。

---

### 8. 前端设置页对应的组件与状态管理
- **页面组件路径**: `frontend/src/pages/Settings.tsx`
- ** API 交互机制**:
  - 获取配置：`api.getConfig()`
  - 保存配置：`api.updateConfig(buildRuntimeConfigPayload(...))`
  - Warmup 测试：`api.warmupConfig(...)`
- **状态管理**: 使用 React `useState` / `useEffect` 管理 `llmProvider`、`backendUrl`、`quickThinkLlm`、`deepThinkLlm` 等本地状态。

---

### 9. 并发模型机制
- **调度层**: `tradingagents/graph/setup.py` (Line 183–190)
- **并发实现**:
  - 分析师的并行调度依赖于 **LangGraph 状态图框架**。
  - 在 `GraphSetup.setup_graph()` 中，所有启用的 `selected_analysts` (如 `market`, `social`, `news`, `fundamentals`, `macro`, `smart_money`, `volume_price`) 的入口边均直连 `START` 节点 (`workflow.add_edge(START, f"{analyst_display_name(analyst_type)} Analyst")`)。
  - LangGraph 框架在运行图时利用 asyncio/线程池自动并行触发这些同层无依赖节点。

---

**总结**: 探查阶段已完成，探查报告已写入 `docs/MODEL_CONFIG_AUDIT.md`。
