# TradingAgents-AShare 分角色模型配置与路由指南 (Model Routing)

本文档说明 `TradingAgents-AShare` 的多厂商凭证与分角色模型路由（Role-Based Model Routing）架构、降级机制（Fallback Chain）以及数据追溯说明。

---

## 1. 架构概述 (Architecture Overview)

系统采用**三层解耦设计**，支持多厂商凭证管理、模型参数档案（Model Profile）以及角色到模型的动态绑定：

1. **Provider（厂商凭证层）**
   - 存储厂商类型（`provider_type`，如 `openai`, `anthropic`, `google`, `siliconflow` 等）、API 端点（`base_url`）及 Fernet 加密存储的 API Key。
   - 一个用户可以同时持有并启用多个 Provider 凭证。

2. **ModelProfile（模型档案层）**
   - 关联具体 Provider，定义 `model_name`、`temperature`、`max_tokens`、`extra_params` 及档位类型（`tier`: `quick` | `deep` | `null`）。

3. **RoleBinding（角色绑定层）**
   - 映射具体 Role Key（如 `bull_researcher`）或角色分组 Key（如 `researchers`）到特定 `ModelProfile`。

---

## 2. 核心解析与降级机制 (Fallback Chain)

当某个 Agent 节点在构图或运行过程中请求模型时，`role_routing_service` 按照以下顺序依次解析，**确保绝对不会因未绑定或绑定失效导致分析抛错中断**：

```
1. 显式 RoleBinding
   ↳ 检查该 role_key 是否有用户手动绑定的 ModelProfile。

2. Group Binding
   ↳ 若无显式 RoleBinding，检查所属角色分组（如 analysts, researchers, arbiter, trader, risk）是否有分组绑定。

3. Tier Default ModelProfile
   ↳ 若无分组绑定，检查该 role 声明的 tier（quick 或 deep）对应的默认 ModelProfile。

4. Global Default ModelProfile
   ↳ 若无 Tier 默认 Profile，退回全局默认 ModelProfile。

5. System Runtime Fallback
   ↳ 若仍无配置，使用系统环境变量（如 `.env` 中的 LLM_PROVIDER / LLM_MODEL）兜底运行。
```

当触发 Fallback 降级时，系统会在日志及辩论消息元数据中标注 `fallback_used: true` 与 `resolved_via` 解析路径。

---

## 3. 15 个 Agent 角色与分组

| 角色分组 (Group) | 包含 Role Key | 默认 Tier | 说明 |
|---|---|---|---|
| `researchers` | `bull_researcher`, `bear_researcher` | `quick` | 核心红蓝对抗多空研究员 |
| `arbiter` | `research_manager` | `deep` | 辩论裁决与报告综合总监 |
| `analysts` | `market`, `social`, `news`, `fundamentals`, `macro`, `smart_money`, `volume_price` | `quick` | 7 维度数据分析师 |
| `trader` | `trader` | `quick` | 交易指令与仓位生成 |
| `risk` | `aggressive_analyst`, `neutral_analyst`, `conservative_analyst`, `risk_manager` | `quick` / `deep` (`risk_manager`) | 风控质询与决策团队 |

---

## 4. 预设模式 (Presets)

系统提供三种一键预设：

1. **单模型 (`single`)**：所有角色均退回 Quick / Deep Tier 默认 Profile，行为与旧版保持完全一致。
2. **多空异构 (`bull_bear_hetero`)**：为 `bull_researcher` 与 `bear_researcher` 独立绑定不同 Profile。
3. **三方异构 (`three_way_hetero`)**：为 `bull_researcher`、`bear_researcher` 以及 `research_manager` 独立绑定不同 Profile。

---

## 5. API 接口说明

- `GET /v1/providers` — 获取已配置的厂商凭证列表
- `POST /v1/providers` — 新增厂商凭证
- `PATCH /v1/providers/{id}` — 更新厂商凭证
- `DELETE /v1/providers/{id}` — 删除厂商凭证
- `GET /v1/model-profiles` — 获取模型 Profile 列表
- `POST /v1/model-profiles` — 新增模型 Profile
- `PATCH /v1/model-profiles/{id}` — 更新模型 Profile
- `DELETE /v1/model-profiles/{id}` — 删除模型 Profile
- `GET /v1/role-bindings` — 获取当前角色的绑定列表
- `PATCH /v1/role-bindings` — 批量更新角色绑定
- `POST /v1/role-bindings/presets` — 应用一键预设模式 (`single`, `bull_bear_hetero`, `three_way_hetero`)
- `GET /v1/role-bindings/resolved` — 获取所有 15 个角色当前生效解析后的模型快照

---

## 6. 溯源与安全 (Traceability & Security)

1. **辩论发言溯源**：在辩论流消息与 WebSocket 推送中，每条发言卡片均包含 `model_name` 字段，前端实时呈现该卡片由哪款模型生成。
2. **报告级快照**：每次运行结果保存在 `execution_results` 表中，其中的 `model_config_snapshot` 记录了分析发起时刻 15 个角色的实际模型解析路径。
3. **API Key 安全**：所有 API Key 均在后端通过 Fernet 算法加密保存。前端 API 响应中仅包含掩码（如 `sk-f****89a`），决不暴露明文 Key。
