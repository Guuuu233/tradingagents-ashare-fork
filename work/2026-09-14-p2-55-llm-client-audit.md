# P2-55：LLM client 遗留 TODO 复核

> 核验日期：2026-09-14（Australia/Perth）  
> 核验主线：`5cb93279290bba3a5c8ba41d5d87bac8bc631f22`  
> 线上运行代码：`79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`

## 结论

`tradingagents/llm_clients/TODO.md` 的四条内容不能再整体当作“未完成施工项”。
其中一条是确实存在的策略缺口，另外三条已经完成、表述过时，或没有对应的静态
CLI 清单。当前不直接改运行时 client；先登记一个窄的策略设计项，避免静态白名单
误伤合法的自定义模型配置。

## 逐项核对

| 旧条目 | 当前事实 | 结论 |
|---|---|---|
| `validate_model()` 从未调用 | `BaseLLMClient` 定义抽象方法；三个 provider client 实现方法；全仓没有 `get_llm()` 或启动流程调用它 | 真实待设计项，但不能直接作为硬门禁接入 |
| API key 参数不统一 | OpenAI/Anthropic 接收 `api_key`；Google 接收统一入口后映射为 `google_api_key`；graph 和 warmup 都从统一配置传入 | 原 TODO 已过时，保留 provider SDK 映射 |
| Anthropic `base_url` 被忽略 | Anthropic 已使用该值，并去除 SDK 自动追加的 `/v1`；Google 仅保留公共参数用于共享代理检查，不传给 Google SDK | 原 TODO 对 Anthropic 的判断错误；Google 行为需保持明确 |
| 与 CLI 模型选项同步 | 设置页模型名是自由文本，另有 `/v1/models/fetch` 动态拉取；没有一份可作为唯一真相的静态 CLI 模型选项 | 旧同步任务不成立，需先定义 catalog/discovery 关系 |

## 直接证据

- `tradingagents/llm_clients/base_client.py`：抽象 `validate_model()` 只有定义。
- `tradingagents/llm_clients/openai_client.py`、`anthropic_client.py`、`google_client.py`：
  client 具体实现与参数映射。
- `tradingagents/graph/trading_graph.py`：角色级配置和统一 `api_key` 传入后直接调用
  `role_client.get_llm()`，未调用 `validate_model()`。
- `frontend/src/pages/Settings.tsx`：模型字段为自由文本，并调用
  `api.fetchAvailableModels()`；占位文本也允许 provider 自己的模型名。
- `api/main.py`：`_warmup_model_names()` 只收集配置中的模型名；warmup 通过 factory
  创建 client，不执行静态模型列表校验。

代表性行为核对（固定 Python 3.10 环境）：

```text
openai     gpt-4o-mini                       True
openai     qwen-plus                         False
deepseek   deepseek-chat                     True
google     gemini-2.5-flash                  True
anthropic  claude-sonnet-4-20250514          True
ollama     local-custom                      True
openrouter provider/custom-model             True
```

这说明如果把当前 `VALID_MODELS` 直接作为 OpenAI client 的警告或阻断依据，
OpenAI 兼容的 `qwen-plus` 等合法配置会被误报；反过来，unknown provider 会被
放行。因此校验策略必须先区分 provider catalog、兼容协议和真实探测结果。

## 后续卡的最小范围

1. 先写策略与状态语义：advisory、provider discovery、warmup failure 三者如何区分。
2. 明确未知模型是允许启动、只告警，还是仅在显式 warmup 时失败；不得因为静态列表
   过时而让已有用户配置无法启动。
3. 以离线 fake transport/构造测试覆盖五类入口：全局配置、角色绑定、动态模型拉取、
   warmup、真实 client 构造。
4. 不在本项改 API key 语义、Google SDK 行为、生产数据库、用户凭据或部署流程。

## 相邻旧审计项复核

为避免把 2026-08-05 的静态审计报告整批重新开卡，本轮顺手核对了相邻编号：

| 旧编号 | 当前核验 | 处理 |
|---|---|---|
| P2-51/P2-52/P2-53 | 相关生产路径当前已无 `print()` 残留 | 不重开 |
| P2-56 | 仍有几个 `except ... as` 未使用绑定，但只属代码卫生，不影响行为 | 记录为低优先清理候选，不混入本卡 |
| P2-57 | `alpha_vantage.py` 仍被 `providers/alpha_vantage_provider.py` 作为兼容 shim 导入 | 原“无消费者可删”判断不成立，不删除 |
| P2-58 | 三个空文件是 Python 包标记 | 正常，不是施工项 |
| P2-59 | 当前 `frontend/package.json` 已不存在原报告列出的四个未用包 | 不重开 |
| P2-60/P2-61/P2-63/P2-64 | 根 README、前端 README、版本号和 AGENTS 前端技术栈描述已与当前代码对齐 | 不重开 |
| P2-62 | 当前 CHANGELOG 已为 v0.6.0，未复现“移除 redis”仍作为当前事实的表述 | 不重开 |
| P2-65 | 已由 DAV-910 完成并进入当前发布代码树 | 不重开；容器级挂载运行核验仍按 PROJECT_STATE 单独保留 |
| P2-66 | `resolve_role_prompt()` 仍是 Phase C 的保留接口，当前注入路径使用统一 resolver | 不重开；standalone 历史仍按既有 P2 边界处理 |

这张表只用于关闭旧审计报告中的误报/已完成项，不把低优先清理候选伪装成当前
发布阻塞。

## 边界

本复核没有改生产数据库，没有调用真实模型，没有修改 client 代码，没有启动或重启
服务，也没有把静态列表当作当前 provider 能力的证明。后续若形成代码候选，仍须由
**代码审核员**对同一完整 SHA 只读审查，再按项目门禁做回归；本文件不构成部署授权。
