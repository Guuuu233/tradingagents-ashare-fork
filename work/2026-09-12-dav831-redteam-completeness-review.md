# DAV-831 红队清单完备性复核报告（归档）

来源：Multica DAV-831 评论，代码复核员 79f9097d，2026-09-11T19:08Z
基线：70b5b47bcff0618e0db1258a438b0875440d4ac5

---

## DAV-828 / DAV-829 / DAV-830 红队清单完备性复核报告（D-012 §5b 只读复核）

**复核角色**：代码复核员 (`79f9097d-6c37-428c-82b8-ba55050c3797`)  
**复核环境与基线**：
- **精确基线 SHA**：`70b5b47bcff0618e0db1258a438b0875440d4ac5`
- **隔离 Worktree 路径**：`/Users/davidliu/multica_workspaces_desktop-api.multica.ai/davidsworks-d70c6ff76b54/dav-831-d6d713e4953a/workdir/tradingagents-ashare-fork`
- **Worktree 分支与状态**：`agent/agent/d6d713e4953a`，干净工作区（`nothing to commit, working tree clean`）
- **Python 解释器**：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python` (Python 3.10.20)
- **基线定向测试复跑**：`pytest tests/test_backtest_calibration_isolation.py tests/test_horizon_return_labels.py -q` -> **110 passed in 7.11s**

---

### 一、复核结论总览

依据 D-012 §5b 规范，经逐行核对基线源码行号、枚举调用入口、排查回退路径与 fail-closed 边界，本次三张实施卡的红队清单复核结论如下：

| 卡号 | 主题 | 现有清单条数 | 契约行号核验 | 覆盖面完备性结论 | 建议补充条数 |
|---|---|---|---|---|---|
| **DAV-828** | E-01 producer（Claim-to-Claim 路径） | 8 条 RT + RT-FULL | 全部真实存在 | **不完整**（遗漏反向误折叠、多容器双键歧义、互斥边/前视违规） | **建议补充 3 条 (RT-9~RT-11)** |
| **DAV-829** | L2 博弈论生产接线 | 7 条 RT + RT-FULL | 全部真实存在/自洽 | **不完整**（遗漏部分成功半写入态、历史日/标的无数据正常形态） | **建议补充 2 条 (RT-8~RT-9)** |
| **DAV-830** | L3 V-01-2 真实结算管道 | 8 条 RT + RT-FULL | 全部真实存在/自洽 | **不完整**（遗漏分红/送转半缺失假暴跌形态、非法价格基准拒绝） | **建议补充 2 条 (RT-9~RT-10)** |

---

### 二、DAV-828 逐项复核深度分析（E-01 Producer）

#### 1. 契约依据行号逐条真实性核验
- **RT-1**（`build_canonical_source_repetition` L444-480）：**真实存在**。`tradingagents/agents/utils/evidence_relations.py` 中 L440-488 定义了 `build_canonical_source_repetition`，L444-461 校验两端 `canonical_event_id` 一致非空，L463-480 校验 `evidence_id` 互异非空，L482-487 返回 `RelationType.SOURCE_REPETITION`。
- **RT-2**（`_RELATION_FOLDING_TYPES` L387-390）：**真实存在**。`tradingagents/agents/utils/claim_cluster.py` L387-390 显式定义 `_RELATION_FOLDING_TYPES = frozenset({RelationType.SOURCE_REPETITION, RelationType.DERIVED_OBSERVATION})`。
- **RT-3**（`validate_relation_graph` L480 / L1037-1044）：**真实存在**。`claim_cluster.py` L480 校验图闭包；L1037-1044 在 `reduce_evidence_claims` 中针对不在 `unique_claim_ids` 宇宙内的端点严格抛出 `FailClosedReason.DANGLING_REFERENCE`。
- **RT-4**（`EvidenceRelation` 禁止自环）：**真实存在**。`evidence_relations.py:268-273` 与 `claim_cluster.py:1047-1050` 均 fail-closed 拦截并抛出 `FailClosedReason.SELF_LOOP`。
- **RT-5**（环路校验）：**真实存在**。`evidence_relations.py:370-400`（`detect_relation_cycles`）及 `claim_cluster.py:1071-1103`（Phase 3 在 `DERIVED_OBSERVATION` 上进行 DFS 检测），严格抛出 `FailClosedReason.CYCLE_DETECTED`。
- **RT-6**（`validate_relation` L353-366 封禁推断）：**真实存在**。`evidence_relations.py` L353-366 严格封禁 `auto_inferred`、`similarity_score`、`verifier_status`、`soft_alignment`，返回 `FailClosedReason.UNSUPPORTED_INFERENCE`。
- **RT-7**（`_resolve_relation_graph_context` L160-167）：**真实存在**。`tradingagents/agents/managers/research_manager.py` L160-167 在 `len(supplied) > 1` 时直接返回 `RELATION_GRAPH_STATUS_INVALID`。
- **RT-8**（合法图输入下的端到端）：**真实存在**。`claim_cluster.py:595-638` 支持合法图消费并产出非零贡献。

#### 2. 三大核心问题解答
- **入口枚举**：
  - *消费端读取入口*：在 `research_manager.py:_resolve_relation_graph_context`（L146-158）中，系统按 4 个候选容器（`state`、`investment_debate_state`、`market_data_context`、`event_coverage`）× 2 个候选键名（`evidence_relation_graph`、`evidence_relations`）进行全量扫描，共计 **8 个潜在读取入口**。
  - *生产端输出入口*：`debate_utils.py` 中 Claim 产生于 3 个阶段（Opening 开篇、Challenge 质询、Tiebreak 裁决）以及结构化响应解析成功（`update_debate_state_with_payload`）与失败捕获（`_record_unstructured_response`）两条分支。
  - *清单覆盖盲区*：RT-7 仅测试了 `state` 与 `investment_debate_state` 的跨容器歧义（仅占 2 个入口），**遗漏了同一容器内部同时提供双键（`evidence_relation_graph` 与 `evidence_relations`）的同名歧义**，以及来自 `market_data_context`/`event_coverage` 的注入歧义。
- **回退路径**：
  - 在 `research_manager.py` 中，存在完整性拦截时的早退函数 `_build_structured_manager_state`（L280-289，INVALID/ABSTAIN 场景）。该早退路径同样调用了 `tally_cluster_votes`。RT-8 仅验证了主路径端到端，未覆盖早退路径对关系图审计数据的合规封装。
  - 当 `debate_utils.py` 解析失败进入 `_record_unstructured_response` 回退分支时，先前已建立的关系图边不得被篡改或清空。
- **fail-closed 边界与重点核点（DAV-828 RT-6 反向缺陷）**：
  - **总工自陈不确定点核对结论**：**确实存在严重覆盖遗漏**。
  - **机理分析**：`claim_cluster.py:1055-1112` 仅对 `SOURCE_REPETITION` 和 `DERIVED_OBSERVATION` 两类边进行无向投影与连通分支折叠；对于合法可证明的逻辑关系（如 `RelationType.SUPPORTS`、`RelationType.REFUTES`、`RelationType.REVISES`），reducer **绝对不应折叠**，两节点必须保持独立于 `unconnected_claim_ids`。
  - 现有 RT-6 仅覆盖了“不可证明时不得生成边”，但**完全未覆盖相反方向缺陷**：若 Claim A 与 Claim B 之间存在合法可证明的关系（例如辩论中一方明确反驳另一方产生的 `REFUTES` 边，或支持论点的 `SUPPORTS` 边，或异侧立场的对立 Claim），边合法生成，但**下游 reducer 绝不能误折叠**进同一 `FoldedComponent`。若被误折叠，将导致对抗双方的有效贡献上限被错误消解或合并为单一组。
  - 此外，`validate_relation_graph` 中的 `CONTRADICTORY_RELATION`（同一有向边并存 SUPPORTS 与 REFUTES，L418-426）和前视违规 `LOOKAHEAD_VIOLATION`（L300-341）在原清单中完全缺失。

#### 3. DAV-828 建议补充红队场景清单

| # | 场景 | 契约依据 | 预期 |
|---|---|---|---|
| **RT-9** | **可证明的非折叠关系（SUPPORTS / REFUTES / 跨立场）** | `claim_cluster.py` L387-390（`_RELATION_FOLDING_TYPES` 仅两项）、L1055-1070、L1135-1137 | 合法生成边且通过 E-01 校验，但下游 reducer **绝不得误折叠**进同一 `FoldedComponent`；两 Claim 必须保持在 `unconnected_claim_ids`，`folded_components` 为空，独立性仍为 `UNKNOWN` |
| **RT-10** | **同容器双键冲突与多容器歧义注入** | `research_manager.py:_resolve_relation_graph_context` L146-167 | 在 `investment_debate_state`（或 `state`）内同时提供 `evidence_relation_graph` 与 `evidence_relations` 双键，严格返回 `RELATION_GRAPH_STATUS_INVALID`，保留所有歧义源审计，不得择优 |
| **RT-11** | **互斥边矛盾（CONTRADICTORY_RELATION）与前视违规** | `evidence_relations.py` L418-426、L300-341 | 同一端点并存 `SUPPORTS` 与 `REFUTES` 时，或关系时间戳晚于 baseline_date 时，必须 fail-closed 抛错/置 `invalid`，下游不得折叠 |

---

### 三、DAV-829 逐项复核深度分析（L2 博弈论生产接线）

#### 1. 契约依据行号逐条真实性核验
- **RT-1 / RT-5**（`api/database.py:138 / :471 / :511`，`api/main.py:2118-2119`，`report_service.py`）：**真实存在**。`ReportDB` 确实包含 `game_theory_report` 列；`final_state` 中同时承载 `game_theory_signals`。
- **RT-2 / RT-3**（`AGENTS.md §3.4 / §3.5 / §4`）：**真实存在**。`AGENTS.md:75-85`（失败禁止返回空值/None/空DF）、L87-101（禁止依赖隐式形状/禁止填充默认值）、L104-110（单 agent 失败不得中断流程）。
- **RT-4**（图路由可达性）：**真实存在**。`tradingagents/graph/setup.py`（L212-256）当前零挂载，接线需确保图执行包含该节点。
- **RT-6**（确定性计算，禁止 LLM 捏造）：**真实存在且自洽**。对接 `game_theory_tools.py` 8 个底层确定性工具。
- **RT-7**（单双周期 short / medium）：**真实存在**。`agent_states.py:448` 与 `horizon_run_metadata` 周期隔离。

#### 2. 三大核心问题解答
- **入口枚举**：
  - *底层工具入口*：`game_theory_tools.py` 包含 **8 个工具入口**（`get_board_fund_flow`、`get_individual_fund_flow`、`get_lhb_detail`、`get_zt_pool`、`get_hot_stocks_xq`、`get_shareholder_count`、`get_margin_trading`、`get_northbound_flow`）。
  - *分析模式与标的特异性*：
    1. **历史回测/评估模式**：`get_board_fund_flow`（L10）与 `get_hot_stocks_xq`（L47）契约明确规定“历史日期分析不可用/历史日拒绝即时快照”。在历史回测时必然触发拒绝。
    2. **标的属性缺失（业务正常状态）**：非两融标的（无融资融券）、非陆股通标的（无北向持股）、非异动日（无龙虎榜上榜）。这些接口返回空数据属于**正常业务状态**，不得被粗暴当成服务崩溃。
  - *清单覆盖盲区*：RT-2 仅泛化描述“输入缺失”，未覆盖历史回测日期限制以及个股无龙虎榜/无两融等业务维度的无数据形态。
- **回退路径**：
  - 节点执行异常/超时时，必须捕获异常并返回降级 state 更新：`{"game_theory_report": "【博弈论分析不可用】原因：...", "game_theory_signals": None}`。
  - RT-3 仅要求“不中断流程”，但未约束降级时的 state 字段原子性。
- **fail-closed 边界与重点核点（DAV-829 RT-3 部分成功/半写入不完整 state）**：
  - **总工自陈不确定点核对结论**：**确实存在严重覆盖遗漏**。
  - **机理分析**：在 LangGraph 中，节点通过返回状态字典更新 `AgentState`。如果博弈论节点内部出现**部分成功**（例如：底层数据部分获取成功、`game_theory_signals` 算出了部分指标，但后续 LLM 报告生成超时失败；或者相反，生成了文本报告但 `game_theory_signals` 抛错为 None / 包含破损结构），若将这种非原子状态直接写入 `AgentState`：
    1. 导致持久化时 `ReportDB.game_theory_report` 与 `result_data["game_theory_signals"]` 出现**语义断裂**（报告声称有信号，但 signals 为空；或反之）；
    2. 若节点为规避报错对缺失指标静默填入 `0.0` 或空字段，直接违反 `AGENTS.md §3.4/§3.5` 的“禁止默认值”硬约束；
    3. `game_theory_signals` 若包含 `float('nan')`、`float('inf')` 或 numpy 数据类型，会导致 JSON 序列化崩溃。
  - 现有 RT-3 仅覆盖了“全挂/超时”场景，**完全未覆盖“部分成功、半写入 state”的形态**。

#### 3. DAV-829 建议补充红队场景清单

| # | 场景 | 契约依据 | 预期 |
|---|---|---|---|
| **RT-8** | **节点部分成功 / 半写入状态原子性保障** | `AGENTS.md §3.4 / §3.5 / §4`，`api/main.py:2118-2119` | 若数据计算与文本报告中任一环严重故障，写入 state 必须保持语义一致性与原子性；不可用指标必须显式标为缺失，**严禁填补 0.0/默认值**，且严禁出现“文本报告存在但 signals 为 None/字段残缺”的脱节状态；`game_theory_signals` 必须严格 JSON 安全（无 NaN/Inf） |
| **RT-9** | **历史日期回测模式与标的特异性无数据（非两融/无龙虎榜）** | `game_theory_tools.py` L8-11、L45-48、L27-32，`trade_calendar.py:58-60` | 历史分析时，即时快照类工具正确识别并显式标明不可用；非两融/无异动标的正常识别为“无两融/无龙虎榜”，不得抛出未捕获异常中断流程，其余可用指标正常计算 |

---

### 四、DAV-830 逐项复核深度分析（L3 V-01-2 真实结算管道）

#### 1. 契约依据行号逐条真实性核验
- **RT-1**（T+1 停牌/一字涨停无法买入）：**真实存在**。`tradingagents/dataflows/return_labels.py:45` 定义 `OutcomeStatus.UNEXECUTABLE_ENTRY = "unexecutable_entry"`。
- **RT-2**（持有期停牌双侧证据）：**真实存在**。`return_labels.py:41` `OutcomeStatus.SUSPENSION`。
- **RT-3**（节假日滚动与旧 `iloc` 负例）：**真实存在**。`return_labels.py:165-180`（`resolve_horizon_calendar_window`）、`api/services/backtest_service.py:271`（硬编码 `iloc[hold_days - 1]`）。
- **RT-4**（价格缺失/结构异常）：**真实存在**。`return_labels.py:42-43` `OutcomeStatus.DATA_MISSING` / `PROVIDER_FAILURE`。
- **RT-5**（分红送转走结果字段）：**真实存在**。`return_labels.py:49-54`（`ReturnType.TOTAL_RETURN`）、L105-106（`cash_dividend_total: float`, `split_ratio_total: float`）。
- **RT-6 / RT-7 / RT-8**（旧报告 T+5 不变性、新周期 T+10/T+40、混池隔离）：**真实存在**。`tests/test_backtest_calibration_isolation.py` 与 `tests/test_horizon_return_labels.py` 实测 110 项全部通过。

#### 2. 三大核心问题解答
- **入口枚举**：
  - *调用链路入口*：
    1. `api/services/backtest_service.py:_get_price_after` 及回测执行；
    2. `api/services/calibration_service.py:_get_price_after_strict` 及校准样本结算；
    3. `tradingagents/dataflows/return_labels.py` 中新落地的纯函数 `resolve_horizon_return_label`。
  - *参数入口*：`price_basis`（`raw`, `vendor_qfq`）、`return_type`（`price_return`, `total_return`）、`horizon`（`short`, `medium`, `legacy`）。
  - *清单覆盖盲区*：RT 清单遗漏了参数入口传入不受支持的复权类型时的校验。
- **回退路径**：
  - 当日历服务不可用或交易日历天数不足（触发 `InsufficientTradingCalendarError`）时，**必须严禁静默降级回退到旧有的 `iloc` 行切片逻辑**！否则在网络波动或日历故障时，系统会静默绕过真实日历校验。
- **fail-closed 边界与重点核点（DAV-830 RT-5 裁定自洽性与分红送转半缺失）**：
  - **总工裁定自洽性核验**：**完全自洽，设计正确**。
    - `return_labels.py` 的架构中，`OutcomeStatus` 负责状态机层（标的样本是否可以结算，如停牌、无法入场、数据缺失、OK 等），而收益计算口径属于 `ReturnType`（`price_return` vs `total_return`）。在 `HorizonReturnResult` 中，`cash_dividend_total` 与 `split_ratio_total` 作为伴生结果字段，语义分层完全正交且清晰。总工禁止新增 `CASH_DIVIDEND` 枚举值的决策完全成立。
  - **分红/送转半缺失形态核对结论**：**确实存在重大覆盖遗漏**。
    - **机理分析**：在 A 股除权除息（XD/XR）日，分红与送转通常紧密关联。
    - 当用户选择 `total_return` 口径时：
      - 场景 A：**分红数据存在，但送转数据缺失**（例如发生“10 送 10 派 2 元”，分红获取到了 0.2 元/股，但送转接口失败或缺失，送转比被默认填入 1.0 或 0.0）。由于除权后股价直接折半，若未调整股数，收益率计算将发生灾难性的**假暴跌（跌幅约 -50%）**！
      - 场景 B：**送转数据存在，但分红数据缺失**。同样导致收益少计。
    - 现有 RT-5 仅写了“缺分红数据不得静默按 0 计”，**完全未覆盖「分红存在但送转缺失」的假暴跌场景**。在 `total_return` 计算中，除权除息事件中的分红与送转数据具备原子完备性要求，任何一方缺失均必须 fail-closed 返回 `OutcomeStatus.DATA_MISSING` / `PROVIDER_FAILURE`，绝对禁止静默使用默认倍数。
  - **未受支持的价格基准**：`OutcomeStatus.UNSUPPORTED_PRICE_BASIS`（L44）在 RT-1~RT-8 中完全未测试。

#### 3. DAV-830 建议补充红队场景清单

| # | 场景 | 契约依据 | 预期 |
|---|---|---|---|
| **RT-9** | **分红/送转半缺失形态（total_return 口径下防假暴跌）** | `return_labels.py` L42-43（`DATA_MISSING`）、L49-54（`TOTAL_RETURN`）、L105-106（`cash_dividend_total`, `split_ratio_total`） | 在 `total_return` 结算中，标的发生送转除权但送转数据缺失时，**严禁将送转比例静默填默认值（如 1.0/0.0）引发假暴跌**，必须 fail-closed 判定为 `DATA_MISSING` 或 `PROVIDER_FAILURE`；分红与送转必须具备原子完备性 |
| **RT-10** | **不受支持的价格基准与日历异常严禁回退行切片** | `return_labels.py` L44（`UNSUPPORTED_PRICE_BASIS`）与 L118-121（`InsufficientTradingCalendarError`） | 传入未受支持的 `price_basis` 时，必须返回 `UNSUPPORTED_PRICE_BASIS`；交易日历不足或异常时，必须 fail-closed 抛出 `InsufficientTradingCalendarError` 或置 `PROVIDER_FAILURE`，**绝对禁止静默回退到 `iloc` 行切片** |

---

### 五、交付联动协议执行

1. **精确远端 branch/SHA 与环境**：
   - 检出与核验基线：`70b5b47bcff0618e0db1258a438b0875440d4ac5`
   - 隔离 Worktree 路径：`/Users/davidliu/multica_workspaces_desktop-api.multica.ai/davidsworks-d70c6ff76b54/dav-831-d6d713e4953a/workdir/tradingagents-ashare-fork`
   - 分支状态：`agent/agent/d6d713e4953a`，干净工作树（未修改任何源码，0 代码提交，0 tests 新增代码）。
2. **实际测试与复核结果**：
   - 定向基线回归：`pytest tests/test_backtest_calibration_isolation.py tests/test_horizon_return_labels.py -q` -> **110 passed**。
   - 三张实施卡的所有契约依据行号均已在隔离源码中完成逐行核实，均真实存在。
   - 确证总工自陈的三项不确定点中均存在重要红队覆盖遗漏，已逐卡提出具体补充场景、契约依据与预期。
3. **未完成与下游解锁事项**：
   - 本卡为只读复核卡，任务已完成，置 `in_review` 待总工/项目主管验收。
   - 下游实施卡 DAV-828、DAV-829、DAV-830 可据此补充红队场景并进入实现阶段。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)