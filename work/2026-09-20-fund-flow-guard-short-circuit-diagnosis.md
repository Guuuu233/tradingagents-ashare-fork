# 资金流守卫短路深度诊断报告（DAV-1104 只读审计）

> **审计基线**：
> - 仓库路径：`/Users/davidliu/Documents/TradingAgents-AShare`
> - 分支：`codex/dav-4-p2a-trunk`
> - 提交 SHA：`6a57eb7f994cd751a13a9d2f449dd88fb1cd5bd2`
> - 解释器环境：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（实测 `Python 3.10.20`）
> - 数据库：`data/tradingagents.db`（全流程只读 `mode=ro`，零修改、零写入、零重跑生产服务）

---

## 摘要与核心发现

1. **存量 201 条 ABSTAIN 归因全量精准复核**：
   - 生产库 `analysis_status = 'ABSTAIN'` 恰好 **201 条**；
   - `manager_consistency_hard_gate` 强闸家族：**157 条**（78.1%）；
   - 资金流守卫短路（`final_trade_decision` 含「资金流来源选择 guard 已阻断」）：**44 条**（21.9%）；
   - 两者**交集精确为 0**，完全互补。

2. **44 条资金流守卫拦截的真实机制分布（关键架构定性）**：
   - **机制 A：未选择 `smart_money` 分析师导致守卫悬空（`status: not_checked`）**（共 3 条，含 DAV-1102 验收用例 `ce9e7a59`、`c21456dd`、`f8c59465`）：
     - **根因坐实**：在 LangGraph 流程中，`propagation.py` 默认将 `state["fund_flow_consensus_guard"]` 设为 fail-closed 的 `{"blocked": True, "status": "not_checked"}`。`data_collector` 成功采集了资金流证据并由 `select_fund_flow_source` 判为 `status: selected, direction_allowed: True`。但由于调用方未将 `smart_money` 选入 `selected_analysts`，`smart_money_analyst` 节点在图中根本未被实例化/执行，**全图没有任何节点将 `data_collector` 的有效选择桥接入图状态**。下游 `research_manager` 读取悬空的默认状态，直接短路判 ABSTAIN！
   - **机制 B：`validate_model_summary` 文本正则错配或多日混淆（`status: mismatch`）**（共 12 条，含 09-19 的 `31dcbebb` 以及 09-18 的 6 条）：
     - **根因坐实**：`smart_money_analyst` 虽正常执行且 `selection` 放行，但模型分析正文中出现多日趋势表述（如「近5日流出约5亿」）或句内标点切分不全，被 `extract_model_totals` / `extract_model_daily_values` 误提取为与单日结构化证据（如 `r0_net=0.40185` vs `-5`）比对，触发 `mismatch`。这导致正文被清空替换为短路提示词并置 `consensus_blocked=True`，下游同样被短路。
   - **机制 C：2026-09-02 历史老代码批次**（共 29 条）：
     - 均为 `c72dd7b`（2026-09-04 允许 `netamount` 作为 `r0_net` 旁证）合入前的历史报告，在旧逻辑下因同时存在 `r0_net` 与 `netamount` 被判 `incomparable_field_semantics` 阻断。当前主干代码已修复该判定。

3. **澄清：`consensus_audit` 并非运行态拦截者**：
   - 审计报告中引用的 `fund_flow_evidence.consensus_audit`（以及镜像 `same_field_consensus_audit`）是由 `build_consensus_evidence`（旧 median/MAD 多源算法）输出的**纯诊断审计日志**。在代码中，**没有任何下游决策节点或门禁读取 `consensus_audit`**。`ce9e7a59` 真正被掐死的原因是机制 A（守卫悬空 `not_checked`）。

---

## 第一部分：44 条资金流守卫拦截深度归因

### 1. 存量 44 条时间与机制穿透表

| 类别 | 数量 | 典型 ID | 产生时间 | 触发根因 | 状态特征 |
|---|---|---|---|---|---|
| **机制 A：Analyst 缺席守卫悬空** | **3** | `ce9e7a59`<br>`c21456dd`<br>`f8c59465` | 09-17 ~ 09-19 | 调用方 `selected_analysts` 未勾选 `smart_money`，图状态未由 `data_collector` 桥接，保持初始 fail-closed | `ffcg.status = not_checked`<br>`ffcg.blocked = True`<br>`reason = fund-flow source selection not checked` |
| **机制 B：模型文本校验假错配** | **12** | `31dcbebb`<br>`24d16748`<br>`5dae1534`<br>`32cbb660` | 09-18 ~ 09-19 | 模型正文被正则误提（多日与单日混淆、句内包含非结构化数字），导致 `validate_model_summary` 误判 | `val.status = mismatch`<br>`selection.direction_allowed = True`<br>`consensus_blocked = True` |
| **机制 C：历史旧逻辑语义互斥** | **29** | `18b5bbd3`<br>`a4ebe599` 等 29 条 | 09-02 | `c72dd7b` 合入前，`r0_net` 与 `netamount` 共存即触发 `incomparable_field_semantics` | `selection.status = data_conflict`<br>`c72dd7b` 后已在代码层解决 |

---

## 第二部分：口径离散判定逻辑诊断（THS `netamount` vs 东财 `r0_net` vs `lg_net`）

### 1. 三层口径的真实语义与上游映射

| 字段 | 上游 API 字段 | 数据源 | 真实业务语义 | 可比层级 |
|---|---|---|---|---|
| `r0_net` | `moneyflow_dc.net_amount` | 东方财富 | 今日**主力**净流入额（超大单 + 大单） | 主力净额口径（最高优先级） |
| `lg_net` | `moneyflow_ths.buy_lg_amount`<br>`moneyflow_dc.buy_lg_amount` | 同花顺<br>东方财富 | 今日**大单**净流入额（不含超大单分量） | 大单口径（大单对大单同义可比） |
| `netamount` | `moneyflow_ths.net_amount` | 同花顺 | 资金净流入（全市场总净额，非主力） | 旁证口径（全市场总资金，不可直接与主力比） |

### 2. 现行代码中的冲突与共识规则

1. **优先级选择（`select_fund_flow_source`）**：
   - 遵从 DAV-179 规范：东财 `r0_net`（rank 2）> 同花顺 `netamount`（rank 4）；
   - 当东财 `r0_net` 存在时，`non_side_fields = {f for f in new_fields if f != "netamount"}`；
   - 同花顺 `netamount` 被自动降为「旁证」，**不触发 `incomparable_field_semantics` 阻断**。
2. **大单对大单可比层（`lg_net`）**：
   - 东财与同花顺同时提供 `buy_lg_amount` 时，两者在 `lg_net` 上同义可比；
   - 在 `ce9e7a59` 中：东财 `lg_net = 0.874816` 亿，同花顺 `lg_net = 0.758931` 亿，相对离散度仅 **7.09%**（远低于 20% 阈值），方向同为 `inflow`。
3. **诊断审计层（`build_consensus_evidence`）的统计陷阱**：
   - 该旧函数按同字段统计 `source_count >= 2`。由于 `r0_net` 仅东财 1 源、`netamount` 仅同花顺 1 源，因此在 `field_results` 中各自显示 `insufficient_sources`；
   - 此信息仅记录于 metadata，**不参与下游运行态执行**。但人工审查时若只看 `market_data_context.fund_flow_evidence.consensus_audit`，极易产生「守卫因口径离散而阻断」的假象。

---

## 第三部分：「守卫正常工作 vs 过度拦截」的严格边界

为确保**不放宽守卫让报告变绿**，同时**消除判定机制的虚警与悬空误杀**，界定如下边界：

| 场景分类 | 具体情况 | 现行表现 | 正确治理动作 | 判定理由与红线 |
|---|---|---|---|---|
| **正常工作（硬阻断）** | 所有数据源全部失败、超时或空值（`all_sources_unavailable`） | 阻断 (ABSTAIN) | **保持硬阻断** | 无数据绝不能盲目放行 |
| **正常工作（硬阻断）** | 标的代码不匹配或交易日期错位（`symbol_mismatch`, `date_mismatch`, `future_date`） | 阻断 (ABSTAIN) | **保持硬阻断** | 串股、未来数据属于严重数据污染 |
| **正常工作（硬阻断）** | 两个同口径主力源给出方向冲突或相对离散度 >20% | 阻断 (ABSTAIN) | **保持硬阻断** | 真分歧且无主导优先级时须 fail-closed |
| **正常工作（硬阻断）** | 模型输出与结构化事实产生致命方向违背（如结构化大幅流入，模型却下达做空建仓指令） | 阻断 (ABSTAIN) | **保持硬阻断** | 防范 LLM 幻觉颠倒乾坤 |
| **过度拦截（治理项 1）** | **调用方未选 `smart_money_analyst`，但数据层已有确定性首选源（`ce9e7a59` 案）** | 悬空 `not_checked` 导致整卡 ABSTAIN | **消除过度拦截**：由 `data_collector` 将已校验的 `selection` 桥接入图状态 | 资金流证据已采集合格，不能因未跑该单项分析师而掐死全流程决策 |
| **过度拦截（治理项 2）** | **跨口径概念差异（东财主力 `r0_net` vs 同花顺大单 `lg_net` vs 总额 `netamount`）** | 在老版本中触发不可比阻断 | **消除过度拦截**：主力以自身方向为准；跨源分歧仅计入 `credibility: low` 降级为弱参考警示，不掐断决策 | 遵从 DAV-179：不同口径本不可比，不应将多源旁证上升为前置阻断硬门 |
| **过度拦截（治理项 3）** | **模型正文提取多日趋势，被校验器当成单日值比对导致 `mismatch`（`31dcbebb` 案）** | 正文被清空，置 `consensus_blocked`，下游 ABSTAIN | **消除过度拦截**：收紧正则与窗口判定；仅在方向反向时阻断，纯数值陈述偏差降为 `validation_warning` 醒目提示 | 结构化资金流方向已由代码层锁死，不应因正文修辞偏差倒推阻断整个投资计划 |

---

## 第四部分：历史样本离线复放对照验证（只读）

在锁定环境（`.venv310` / Python 3.10.20 / 只读库）下，对历史样本执行离线复放验证：

### 1. `ce9e7a59`（DAV-1102 验收用例）离线复放结果

- **数据层原状**：
  - `data_collector` 真实采集的 `fund_flow_evidence.selection`：
    - `selected_source`: `tushare_eastmoney_moneyflow_dc`
    - `selected_field`: `r0_net`
    - `selected_value`: `0.40185` 亿元
    - `direction`: `inflow`
    - `direction_allowed`: `True`
    - `hard_guard`: `{"blocked": False, "direction_allowed": True, "reason": "new_algorithm_source_priority"}`
- **图状态悬空原状**：
  - 生产库中的 `fund_flow_consensus_guard`：`blocked: True, status: not_checked`；
  - `research_manager` 检查 `fund_flow_guard.get("blocked")` -> `True` -> ABSTAIN。
- **复放验证（桥接数据层 selection 至图状态）**：
  - `replayed_guard["blocked"] = False`，`direction_allowed = True`，`status = selected`；
  - `research_manager` 短路条件 `fund_flow_guard.get("blocked") or not fund_flow_guard.get("direction_allowed")` -> **`False`（不阻断）**；
  - 辩论前置硬门校验：`validate_debate_preconditions(ce9e7a59_debate_state)` -> **`errors: []`（0 错误，10 条 Claims 完备）**；
  - **复放结论：修复后 `ce9e7a59` 场景决策层 100% 顺畅放行，不再 ABSTAIN！**

### 2. `c21456dd` 与 `f8c59465` 离线复放对照

- `c21456dd`：`selection.status = selected`, `selected_source = tushare_eastmoney_moneyflow_dc`, `direction_allowed = True`；辩论前置硬门通过。
- `f8c59465`：`selection.status = selected`, `selected_source = eastmoney_direct`, `direction_allowed = True`；辩论前置硬门通过。
- **复放结论**：机制 A 下的 3 条样本全部通过复放，无一新增异常。

---

## 第五部分：治理施工方案建议（待总工放行）

根据只读诊断结论，施工仅需聚焦以下极小范围（改动量 < 30 行，不破坏已有红线）：

1. **状态桥接（消除机制 A 悬空误杀）**：
   - 在 `tradingagents/graph/data_collector.py` 中，当 `data_collector` 收集并完成 `select_fund_flow_source` 后，若 `selection["status"] in {"selected", "consensus"}`，同时将 `selection` 的状态结果同步回写至 `final_state["fund_flow_consensus_guard"]`；
   - 或在 `tradingagents/graph/trading_graph.py` / `setup.py` 中，若 `smart_money` 未被选入分析师，则以 `market_data_context.fund_flow_evidence.selection` 作为守卫状态，不再保持未初始化的 `blocked: True`。
2. **校验防线精准化（消除机制 B 正则假错配）**：
   - 优化 `tradingagents/dataflows/fund_flow_evidence.py` 中的 `validate_model_summary`；
   - 确保当 `window_days == 1` 时，不因多日词汇误伤单日提取，且将轻微文本表述偏差降为警告提示，仅保留方向性违规作为硬闸阻断。

---

## 交付信息

- **交付卡号**：DAV-1104
- **诊断交付文件**：`work/2026-09-20-fund-flow-guard-short-circuit-diagnosis.md`
- **执行环境**：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20）
- **当前状态**：只读诊断完工，待总工确认排期与方案后放行施工。
