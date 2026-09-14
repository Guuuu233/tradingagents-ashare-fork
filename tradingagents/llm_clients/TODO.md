# LLM client consistency backlog

> 本文件是源码包内的历史提示，不是运行时门禁。2026-09-14 已按当前主线
> `5cb93279290bba3a5c8ba41d5d87bac8bc631f22` 做过核对；详细证据见
> `work/2026-09-14-p2-55-llm-client-audit.md`。

## 当前核对结果

### 1. `validate_model()` 仍未进入 `get_llm()` 调用链

`OpenAIClient`、`AnthropicClient` 和 `GoogleClient` 都保留了显式的
`validate_model()` 方法，但当前没有调用者。这个事实仍是待设计项，不应直接把
`VALID_MODELS` 接到启动或建 client 的硬门禁上：设置页允许自由填写模型名，且支持
OpenAI 兼容服务、OpenRouter、Ollama、DeepSeek 等不适合由一份静态列表穷举的配置。

后续必须先确定“提示性校验”与“实际 provider 探测”的边界，再决定是否调用、何时
提示以及未知模型是否允许继续。未完成该设计前，不以本文件为依据修改运行时行为。

### 2. API key 的统一入口已基本落地

三个 client 都接受统一的 `api_key` 入口；Google client 在交给 SDK 前映射为
`google_api_key`，OpenAI 兼容 client 和 Anthropic client 继续使用 `api_key`。
原表格把 Google 写成“未统一”已过时。

### 3. `base_url` 的现状不是“Anthropic 被忽略”

Anthropic client 已使用 `base_url`，并在交给 SDK 前去掉末尾 `/v1`；Google client
仍接受公共构造参数以完成共享的代理安全检查，但不把它传给 Google SDK，这是该
provider 的预期边界。是否收窄公共签名属于单独的兼容性设计，不在本文件直接处理。

### 4. `VALID_MODELS` 没有可同步的静态 CLI 清单

当前设置页的模型名是自由文本，也可通过 `/v1/models/fetch` 动态取得列表；因此旧
“同步 CLI 选项”的描述不再适用。静态 `VALID_MODELS` 只能作为经过定义的 advisory
catalog，不能冒充 provider 的实时能力发现。

## 后续施工边界

另立窄卡完成模型校验策略设计和必要的离线测试后，才考虑运行时代码变更。卡内至少
要覆盖：自定义 OpenAI 兼容地址、provider-specific 参数映射、角色级模型配置、动态
模型列表、未知模型、Ollama/OpenRouter，以及 warmup/真实请求失败时的语义。任何代码
变更仍须由**代码审核员**对同一完整 SHA 只读审查，并按风险执行回归；本文件本身不
授权部署、真实分析或凭据操作。
