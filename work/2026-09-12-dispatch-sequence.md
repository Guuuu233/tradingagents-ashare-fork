# 剩余任务派工序列表（2026-09-12）

**性质：只读盘点产出的派工建议，不是派工、提交、合入、部署或真实采集授权。**
**基线：目标 trunk `220e253686a187ddd3b7890af2a85aa4dcf951e8`；运行服务 `4a5206f6e2d027dde8fb6da9bbbc1f38a7baca62`（落后 5 个提交）。**
依据：`2026-09-06_整合施工计划-v1.1.md`（候选整合计划，**尚未获批**）、D-009、`work/v03-freeze-sheet-20260909.md`、DAV-821/822/823/826 审计结论。

---

## 0. 前置：先确认三件事

开工前必须重新核验，不得沿用本文档的快照：

1. `git ls-remote origin codex/dav-4-p2a-trunk` 回读主干 SHA 是否仍为 `220e253`；
2. `multica daemon status` —— 当前为 **stopped**，不恢复则任何新卡不会被领取；
3. `curl -s http://127.0.0.1:8000/healthz` 回读服务 `commit_sha`。

**红线（继承 D-006/007/008/009，不得因本文档放宽）**：不开 `credit_weighting_enabled`；不改辩论轮次 3/1；不取消历史 snapshot refusal；不写生产库；不 FF 未授权候选；不部署未授权版本；agent 不得自授权。

---

## 批次 0 — 解锁（⚠️ 0-A 与 0-B 存在 SHA 顺序互斥，必须先二选一）

**顺序冲突说明**：D-04 的父提交是 `220e253`。若先合入 D-04，trunk tip 会变成 `70b5b47`，此后部署的 `/healthz` **不可能**回读成 `220e253`。因此只有两条合法路径：

- **路径 I（先部署，修复链先生效）**：先部署 `220e253` → 验收 `/healthz` = `220e253` → 再合入 D-04（trunk → `70b5b47`）→ 视需要二次部署并验收 `70b5b47`
- **路径 II（先合入，少一次重启）**：先合入 D-04（trunk → `70b5b47`）→ 再一次性部署并验收 `/healthz` = `70b5b47`

**无论走哪条，验收回读的 SHA 必须等于当次实际部署的 SHA。不得沿用固定字符串。**

| 编号 | 动作 | 允许改 | 抢文件 | 验收钉子 | 授权 |
|---|---|---|---|---|---|
| 0-A | **部署**：`4a5206f` → 当时 trunk tip（路径 I = `220e253`；路径 II = `70b5b47`） | 无 | 无 | 先做 `.backup()` 一致性备份（记录备份前后 `reports` 计数一致）；部署后 `/healthz` 回读 = **当次部署的 SHA**；启动时残留报告恢复 failed=0 | **需 David** |
| 0-B | **D-04 合入**：候选 `70b5b47bcff0618e0db1258a438b0875440d4ac5`（分支 `agent/1/618c95913588`，父 = `220e253`） | 仅 `tests/test_fund_flow_scale_consumption.py`（新增） | 无 | David 写出完整 SHA + 「准予合入」；FF 后联合回归 + `ls-remote` 远端回读确认 trunk tip | **需 David** |
| 0-C | **恢复调度**：`multica daemon start` | 无 | 无 | `daemon status` = running；随后确认无卡滞留 `todo` | **独立授权动作**（启动调度会改变运行态并可能触发自动派工） |

> 0-A 与 0-B 的先后由路径选择决定，**不可并行**。0-C 与两者均无依赖，但**属独立授权动作**，不得因批准部署而自动获得授权。
> **路径 I 的二次部署是独立动作**：若先部署 `220e253`、之后再合入 D-04，则「再次部署以让 D-04 生效」需要**另一次明确授权**，不得由前一次部署授权自动覆盖。
> 0-B 的复审 PASS 只是合入前必要证据，不等于合入授权（DAV-827 原话）。

---

## 批次 1 — 五条线（**必须各自隔离 worktree 才可并行**，同一工作树不得并行写）

| 线 | 任务 | 允许改（白名单） | 验收钉子 | 前置 |
|---|---|---|---|---|
| **L1** | **E-01 producer 接入**（L2 破局唯一入口） | **须与 v1.1 §7 E-01 逐项 reconcile**：<br>① 计划点名：`tradingagents/dataflows/news_event_evidence.py`、`tradingagents/agents/utils/claim_cluster.py`、`tradingagents/agents/utils/debate_utils.py`、新增 `tests/test_evidence_relations.py`<br>② 实际契约落点（DAV-821 核实）：`tradingagents/agents/utils/evidence_relations.py`<br>③ 消费端（DAV-806 已接 seam）：`agents/managers/research_manager.py`<br>④ **条件项**：`news_event_evidence.py` 是否在范围内**取决于 DAV-826 的方案选择**——走 Claim-to-Claim（辩论协议层）则可能不需要；走 Evidence-to-Evidence（采集层）则必需。**开卡时必须显式写明取哪条路径及对应的最终白名单。** | ① 5 条红队场景（含环路/断链/歧义/未知关系不得计独立票）；② RT-FULL 全量对照基线；③ **合法 fixture 应产出 `available`；但未知、歧义、断链输入必须继续为 `pending`/`unknown`/`invalid`——不得以「非 pending」作为全局门槛**；④ `reduce_evidence_claims` 节点宇宙与 producer 端点类型一致（防 `DANGLING_REFERENCE`） | **DAV-826 方案审定**（推荐 Claim-to-Claim / 辩论协议层；采集层建图端点会是 `evidence_id`，直接传入必触发 DANGLING_REFERENCE） |
| **L2** | **博弈论接线** | `tradingagents/agents/utils/game_theory_tools.py`、`agent_states.py`、`graph/` 下新增节点、必要 prompt 小节 | **不得只以「填充率非零 + 报告非空」作为完成标准**，须同时证明：① 生产图可达（新节点确实在 graph 路由上被执行，有 trace/日志证据）；② `game_theory_report` 与相关信号**正确持久化**（`ReportDB` 落库后回读一致）；③ 来源可追溯（每个信号可追到具体输入与计算，不得由 LLM 自由生成）；④ **无伪造指标**（缺数据必须显式标缺失，不得填默认值或编造）。DB 列已有，无需迁移 | 无（工具、DB 列、`api/main.py:2118` 读取点均已存在，缺生产者） |
| **L3** | **V-01-2 真实结算管道** | `tradingagents/dataflows/return_labels.py`（补 `resolve_horizon_return_label`）、`api/services/backtest_service.py`、`calibration_service.py`、新增测试 | ① 停牌 → `suspension`、一字涨停 T+1 不可买 → `unexecutable_entry`、缺数据 → `data_missing`/`provider_failure`（**均取现有 `OutcomeStatus` 枚举，不得自造新值**）；② **分红/送转处理方式需先定契约**：现有枚举**没有** `CASH_DIVIDEND`/`SPLIT`，`ReturnType` 只有 `price_return`/`total_return`，`cash_dividend_total`/`split_ratio_total` 字段已定义但未连通——**是「结果字段」还是「新增状态」须先决定，不得直接写进验收**；③ 缺数据不得当 0 收益；④ 节假日/缺行/周末负例证明与 `iloc[hold_days-1]` 的差异 | 无（依赖 H-05/D-02，均已合入） |
| **L4** | **B-01 Gate 0 环境核实（只读）** | 无 | MediaCrawler 目录/版本/任务/源库/归档路径/Cookie 与安全登录条件的核实清单；**不含任何采集动作**。**注意：本项只产出核实结论，不等于 Gate 0 完成** | 无 |
| **L5** | **量价填充率提升**（系统输入完整度三项之一） | `tradingagents/dataflows/providers/`、`graph/data_collector.py` | 填充率实测提升并留证；缺数据仍须显式标缺失，**不得填默认值**（AGENTS.md §3.5） | 无 |

**L1–L5 的边界说明**：L1 只碰 claim 系与 `research_manager`；L2 只碰 `game_theory_tools`/`agent_states`/`graph` 新节点；L3 只碰 `return_labels`/`backtest_service`/`calibration_service`；L5 只碰 `providers/` 与 `data_collector.py`。**L2 与 L5 同涉 `graph` 语义，L1 与后续 E-03d 共用 `research_manager.py`——这三处即使文件不同，同一工作树也不得并行写，必须各自隔离 worktree。**

---

## 批次 2 — 依赖批次 1

| 编号 | 任务 | 依赖 | 允许改 | 验收钉子 |
|---|---|---|---|---|
| 2-1 | **E-02 生效验证** | L1 | 原则上无需改码；仅当 seam 有缺陷才动 `claim_cluster.py` | 复制/语义改写/换 Agent/同观测指标四类对照中，已判定同事实的复制**不增加有效支持**；真正独立观察保留增量 |
| 2-2 | **E-03b/c/d 命题审查** | L1（c/d 还依赖 E-02 生效） | `debate_utils.py`、`evidence_verifier.py`、`decision_status.py`、`bull_researcher.py`、`bear_researcher.py`、`research_manager.py`、新增 `tests/test_claim_review_contract.py` | 第一发言者不得引用不存在的对手命题；已拒绝对手 claim 不全局阻断；采用命题中的 PIT 失败不得被总监洗白；命题状态可回溯 |
| 2-3 | **DAV-808 custom_prompt 守卫实现** | **需 David 先决策**（DAV-824 仅为规格草案，DAV-808 仍为 `backlog`）：守卫落点位置、命中处理方式、误报策略三项未定，**不得直接派实现**；另抢 `api/main.py` | `api/main.py`、`prompts/`、`custom_prompt_service.py`、新增测试 | custom_prompt 注入受 E-02 提示词守卫覆盖；规格草案见 DAV-824 |

**串行约束**：2-2 内部 b → c → d 严格串行（同一批文件）。2-3 与 0-A 部署抢 `api/main.py`，**部署完成后再开 2-3**。

---

## 批次 3 — 依赖 V-01-2（L3）

| 编号 | 任务 | 依赖 | 验收钉子 |
|---|---|---|---|
| 3-1 | **V-03 完整实验**：四维消融（复制/顺序/缺口/命题）+ 反泄漏 + 微观可执行交易成本 | L3 | 按 `work/v03-freeze-sheet-20260909.md` 冻结口径；回归股集（歌尔/工业富联/蓝思/美的/隆基/爱尔）作为固定集；重叠标签 purge/embargo |
| 3-2 | **Forward OOS 样本积累**（≥2026-09-09） | 时间 | 只读统计既有样本时无额外授权要求；**若该过程会新增报告、写入生产库或改动任何持久化状态，必须先取得明确的数据写入授权**（不得以「积累样本」为名隐式写库）。无论何种方式，均不得据 OOS 结果调参后再报同一 OOS |

> 3-1 的**收益结论**还额外依赖「系统输入完整度」——博弈论（L2）、真实舆情源（批次 4）、量价填充率。**在输入补齐前，任何 V-03 数字只能标为进度基线，不得写成收益系统完工或盈利能力定性依据**（DAV-823 已锁定的结论）。

---

## 批次 4 — 社交 B-01（须真实 Gate 0/1 完成之后，非 L4 的只读核实）

| 编号 | 任务 | 依赖 | 验收钉子 | 授权 |
|---|---|---|---|---|
| 4-1 | **Gate 2 shadow**：30 报告 / 10 股 | **真实 Gate 0/1 完成**（≠ L4 的只读环境核实）：真实 MediaCrawler 双平台采集、归档导入、append-only 不可变验证均已实跑并留证 | 报告能关联合格快照才算闭环；`snapshot_at <= cutoff` 资格判定；归档 append-only 无 update-in-place | **需单独授权**（真实采集/导入） |
| 4-2 | **Gate 3 canary active**：2–5 股 | 4-1 | `TA_SOCIAL_MODE` 由 `disabled` 切 `active`；`direction_allowed=false` 时不得把社交分数当多空证据 | **需 David 单独授权** |

> ⚠️ **L4 只是只读环境核实，不等于 Gate 0 完成。** Gate 0 需要真实双平台首轮采集与归档验证，属独立授权事项。原 Gate 顺序（0 合规环境 → 1 离线契约 → 2 shadow → 3 active canary → 4 删 legacy）不得跳过。

> Gate 4（`legacy_proxy` 遗留代码删除）**已合入主干**（实测 `tradingagents/` + `api/` 引用数为 0，且 `tests/test_social_rollout_modes.py:562-570` 有断言验证）。**不得因 Gate 4 已完成而宣布社交接入完成。**

---

## 批次 5 — 收口

| 编号 | 任务 | 依赖 | 验收钉子 | 授权 |
|---|---|---|---|---|
| 5-1 | **E-04 基本面/事件/预期修正分栏**（唯一尚未开始的工作包） | 2-2 | 标题或 PDF 字节 hash 不得变成已读毛利率；无旧基线不得生成修正幅度；`forecast` 不是实际半年报；同一影响已计入预测不重复加票 | — |
| 5-2 | **生产库符号碰撞清洗** | — | 3 组碰撞 + 34 条异常符号；先 `.backup()` 再清洗；**卡 DAV-800 本身已 `done`，未授权的是清洗动作** | **需 David** |

---

## 常驻项（不派工，持续遵守 —— 单列，不与上面批次混算）

| 编号 | 项目 | 当前实测状态 | 说明 |
|---|---|---|---|
| **S-1** | **R-01 H1b 信用加权门槛** | **FAIL / `KEEP_FALSE`** | **目标口径（可复现）**：`793 completed → 131 v2 合格 → 9 份 D-009 合格`；其中 **`9` 是全体 v2 的 D-009 合格数，`N=7` 是再按 `legacy_unversioned` cohort 筛选后的样本量**，两者不是同一层。复现命令（**解释器必须用绝对路径**，`4a5206f` worktree 内没有 `.venv310`）：<br>`cd /private/tmp/ta-serve-trunk && env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python scripts/verify_h1b_gates.py --cohort=legacy_unversioned --db-path /tmp/h1b_v2.db --output-json /tmp/h1b_v2.json`<br>（`/tmp/h1b_v2.db` 由 `data/tradingagents.db` 经 `.backup()` 生成；该 worktree 的 gate 脚本与 `shadow_credit.py` 与主干 `220e253` 逐字节相同） |
| **S-1a** | 关于「55 份」的来源（已查清，**非并列口径**） | 旧脚本 + 旧库的原始加载数 | `55` 来自 **旧分支 `4fa768` 的 `scripts/verify_h1b_gates.py`**：该版本**不支持 `--cohort`**，且 `load_reports_from_db(db_path)` **收下 `db_path` 却从不使用**，实际走 `api.database.get_db_ctx()` 读 `DATABASE_URL` → 落在**仓库根目录的 `tradingagents.db`**（实测 `completed=55` / `failed=62` / 全部 `analysis_status=NULL`）。用目标脚本对该库筛选后为 **`55 → 0 → 0`**（无 v2 合格样本）。<br>**结论：`55` 是「旧脚本 + 旧库」的原始加载数，不是 H1b 三段台账的任何一段，不可与 `793→131→9` 并列比较。** |
| **S-2** | 系统输入完整度（另两项） | 博弈论 0% → 见 L2；真实舆情源 → 见批次 4 | 三项须齐备，V-03 的**收益结论**才成立 |

---

## 抢文件冲突矩阵（必须串行）

| 文件 | 涉及任务 | 顺序 |
|---|---|---|
| `api/main.py` | 0-A 部署、2-3 DAV-808 | 部署 → DAV-808 |
| `tradingagents/agents/managers/research_manager.py` | L1、2-2 | L1 → 2-2 |
| `tradingagents/agents/utils/claim_cluster.py` | L1、2-1 | L1 → 2-1 |
| `tradingagents/graph/` | L2、L5、批次 4 社交 | L2 → L5 → 4-1 |
| `prompts/` | L2、2-3、5-1 | 逐项串行 |
| `api/services/calibration_service.py` | L3、2-2 | L3 → 2-2 |
| `tradingagents/dataflows/providers/` | L5 | — |
| `tradingagents/dataflows/social/**` | 批次 4 独占 | — |

**规则**：同树同时只允许一个写者；涉及相同文件的实现从前一已验收主干后代开工；每个候选必须走「同 SHA 独立审核（含 D-012 红队场景 + RT-FULL 全量回归）→ David 完整 SHA + 准予合入 → 线性 FF → 远端回读」。

---

## 证据边界（不得越界表述）

| 结论 | 证据强度 | 不可扩大为 |
|---|---|---|
| H 链「通过」 | **仅后端离线测试**（指定测试 116 passed） | ❌ 不等于前端/UI 通过。目标 SHA 的干净工作树无 `node_modules`，`vitest` 不可执行，前端 Vitest **未运行**；真实 UI 与部署后入口验收**均无证据** |
| 0-A 部署完成 | `/healthz` 的 `commit_sha` 回读 | ❌ 不等于 checkout 干净，也不等于写库安全（PROJECT_STATE 已明确此陷阱） |
| 0-B D-04 复审 PASS | 独立只读复审（12 passed / 组合回归 419 passed） | ❌ 不等于合入授权 |
| L4 Gate 0 核实 | 只读清单 | ❌ 不等于 Gate 0 完成，更不等于可采集 |

---

## 未核实项（不得直接当 backlog）

以下来自历史计划文档的未勾选项，**我未逐项回代码核验**，且已知至少 2 项实际已完成（`llm_call_logs.report_id` 列已存在、`role_routing_service._mask_api_key` 已脱敏）。若要用作派工依据，须先逐项核验：

- 美股/全球指数 1d 真值（`build_global_indices_markdown` + `get_global_indices` 已接线，数据质量未核）
- 产业链注入 fail-closed + 落库后关键词质量闸
- 多空模板对称化 / few-shot 去偏（A3 已部分交付）
- 风控三方递进 + 五步裁决落 `judge_decision`
- 可观测三项（其中 2 项已证完成）

---

## 本轮交付边界

本文档为**只读盘点产出的派工建议**。未修改代码、未改卡状态、未合入、未部署、未重启 daemon、未写生产库、未执行任何真实采集。
