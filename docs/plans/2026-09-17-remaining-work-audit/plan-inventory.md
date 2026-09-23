# TradingAgents-AShare 方案与现行交付逐项对账及全景剩余工作清单

> **核验时间**：2026-09-17 20:00 (UTC+8)\
> **核验对象**：TradingAgents-AShare 整个项目生命周期各阶段规划方案与主线代码、看板状态的逐项实测对账\
> **对账代码主线**：`origin/codex/dav-4-p2a-trunk` @ **`5a0320f0618d203e95e7197e08b110c7850078d4`**\
> **生产服务基线**：**当前已停机**（8000 端口无监听），最后已知发布版为 **`f094d6a78bc699fc6224e57164d38455c2ad55a9`**（主线领先线上 27 个提交，包含近期 7 刀全部未部署）\
> **生产数据库**：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`，SHA256=`94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`，reports 1409（completed 793 / failed 616）\
> **看板快照依据**：`/Users/davidliu/Documents/Codex/2026-09-17/remaining-work-audit/issues.json`（总计 1022 张，已关闭 1004 张，活跃 18 张）\
> **治理准则**：依据 D-018 回归原交付联动协议，严格遵守 D-012 真实红队/RT-FULL、D-013 证据放行、D-014 代码审核员规则；严禁凭历史计划中陈旧的复选框或文字脑补认定未完，一切以代码、Git、数据库与测试真实证据为准。

---

## 一、 执行摘要与五分类说明

### 1.1 总体对账结论
经过对主仓 `work/`、`docs/`、Codex 交接目录以及看板快照的完整审计，**TradingAgents-AShare 并不存在大面积完全未动工的旧业务线**：
- 2026-08-27 统一方案（Track A 裁决修补、Track B 社交离线链路）的绝大多数核心代码已经陆续实现并合入主线。
- 2026-09-06 增补整合计划中的期限（H 链）、价格/资金结算（D 链）、命题审查（E 链）主体也已合入 `5a0320f`。
- 真正造成“看似散落、进度模糊”的根因在于**五重脱节**：
  1. **代码与服务脱节**：主线已至 `5a0320f`，但因 D-012 门禁阻塞与阻断缺陷，线上服务停留在 `f094d6a` 且处于关机状态，27 个关键提交（含近期 7 刀窄修）**完全未部署**。
  2. **门禁与测试脱节**：baostock 底层无 EOF 检查与裸 TCP 穿透引发忙循环活锁（DAV-979），导致 RT-FULL 门禁收敛困难；当前正通过卡 A（网络隔离）、卡 B（拒绝传播）、卡 C（lifespan 恢复）及 DAV-998（T+1 回填返修）联合收口。
  3. **离线与实证脱节**：博弈论（E-01~04）代码已接线，但生产库 793 份完成报告中相关字段全为空（P0-B）；真实社交（Track B）代码已完备，但 Gate 0–4 真实账号、采集与 Canary 从未上线；收益评估（V-03a）代码存在，但真实行情 provider 股票池仍是全放行占位（P0-C）。
  4. **历史案例状态机断裂**：主线 `5a0320f` 存在严重缺陷（DAV-998），`historical_cases.py` 将所有 typed refusal 视作永久终态，导致日常 T+1 回填全面停摆，必须前向返修后方可放行部署。
  5. **测试套件假红**：主干保留有 20 项历史既有失败，其中 10 项为契约与 mock 未同步（DAV-931/932/933 待办）。

### 1.2 严格状态分类定义
本文档对所有历史方案、交接项和看板任务进行逐项核验，严格区分以下五种状态：
1. **【已交付】**：代码或测试已合入当前远端主线 `codex/dav-4-p2a-trunk`（`5a0320f`），且在 Git/测试中有确凿证据；
2. **【未部署】**：代码已合入当前远端主线，但线上服务尚未部署/尚未运行（目前停留在 `f094d6a`，服务停止监听），等待部署门禁放行；
3. **【真待办】**：主线尚未实现、或正在施工、或属于真实存在的代码/基础设施/测试修改项；
4. **【替代废止】**：已被后续架构决策（如 D-009/D-011/D-013/D-018 等）明确废除、或被更优方案合并取代、或属于错误归因的历史任务；
5. **【缺证待核】**：代码表面存在或有离线测试，但缺乏生产真实运行 evidence、或被前置外部授权（如真实 Cookie/写生产库/开加权）所卡住，不可标记为全链路完成。

---

## 二、 终版审计收口线（RT-FULL / 活锁 / T+1 回填 / 资源隔离）

源依据：`work/2026-09-16-final-audit-and-integration-plan.md`、`PROJECT_STATE.md`、DAV-979、DAV-998、DAV-1003。

| 序号 | 方案/任务项 | 状态 | 源路径及行号 / 证据 | 关联 Issue | 依赖关系 | 验收与授权边界 |
|:---:|:---|:---:|:---|:---:|:---|:---|
| 1.1 | **baostock EOF 活锁收敛与 RT-FULL 网络隔离** | **真待办** (收口中) | `PROJECT_STATE.md:12-35`<br>`baostock/util/socketutil.py:55` | DAV-979 (in_progress) | 依赖卡 A、卡 B、卡 C | **验收**：离线模式下裸 TCP 拦截有效，无 EOF 忙循环，RT-FULL-OFFLINE 完整跑通。<br>**授权**：无需外部授权，纯测试与 provider 底层硬化。 |
| 1.2 | **T+1 历史案例可重试拒绝与永久终态严格解耦** | **未部署** (待收口) | `tradingagents/knowledge/historical_cases.py:793-799` 阻断缺陷已在候选 `4efe4163` 修复 | DAV-998 (in_review) | 阻塞部署与后续主线合入 | **验收**：`future_eval_date`、`eval_date_not_closed` 等临时状态不脱离回填队列，定向 59 passed，diff --check 无空行。<br>**授权**：总工放行整合。 |
| 1.3 | **唯一收口整合候选（DAV-1003）打回项返修** | **真待办** | `work/2026-09-16-final-audit-and-integration-plan.md:11-37` | DAV-1003 (blocked) | 整合 989/995/996/998 | **验收**：①lifespan 启动期异常 `try/finally` 提前避免全局泄漏；②baostock 硬化失败由 fail-open 改为 fail-closed；③删除 `/tmp` 宽泛通配删除；④RT-FULL 零新增失败。<br>**授权**：代码审核员同 SHA 审查 + 总工放行。 |
| 1.4 | **卡 A：pytest 网络隔离双层架构（audit hook）** | **已交付** (候选) | 候选 `8589e65`<br>`tests/conftest.py` | DAV-1007 (in_review) | 作为卡 B/C 基础 | **验收**：DAV-1008 同 SHA 独立复审已 PASS，定向门禁通过。<br>**授权**：纳入 DAV-1003 唯一收口。 |
| 1.5 | **卡 B：离线护栏拒绝即时上传播（第一阶段诊断）** | **真待办** (进行中) | `work/2026-09-16-guardrail-architecture-decision.md` | DAV-1009 (in_review) | 基于卡 A (`8589e65`) | **验收**：第一阶段仅诊断，给出 FD 探测与拒绝传播矩阵，不盲目修改代码。<br>**授权**：调度助手分配第二阶段实施。 |
| 1.6 | **卡 C：FastAPI lifespan 全局状态与 socket 恢复** | **未部署** (审查中) | 候选 `714f620e` 打回后演进至 SPEC_V2 | DAV-1021 (in_review) | 需与卡 A/B 解冲突 | **验收**：严格 SPEC_V2 契约，启动与关闭异常均不污染全局 executor 与 socket timeout。<br>**授权**：代码审核员复审。 |
| 1.7 | **API lifespan 全局 executor 与客户端泄漏收敛** | **替代废止** (合并) | `api/main.py:441,607` | DAV-989 (in_review) | 被 DAV-1003 / DAV-1021 取代 | **结论**：单独合入会与 DAV-995/996 产生文件冲突，其有效改动并入整合卡，旧独立卡关闭。 |
| 1.8 | **测试离线拦截护栏与 socket 隔离单独交付** | **替代废止** (合并) | `cn_baostock_provider.py` | DAV-995 (in_review) | 被卡 A (`DAV-1007`) 及 DAV-1003 取代 | **结论**：原 85db 候选存在 3 处真静默捕获与全局超时污染，已分拆至卡 A 重做，旧卡关闭。 |
| 1.9 | **api_smoke 全局状态与表清理单独交付** | **替代废止** (合并) | `tests/test_api_smoke.py` | DAV-996 (in_review) | 被 DAV-1003 / 卡 C 取代 | **结论**：原候选删全表过宽且未根治 EOF，已提取有效部分并入整合收口，旧卡废止。 |
| 1.10 | **旧单一归因卡：API smoke 永久挂死修复** | **替代废止** | `PROJECT_STATE.md:76-88` | DAV-992 (cancelled) | 原归因被证伪 | **结论**：将挂死单一归咎为 lifespan 关闭全局 executor 已被实测证伪，保持 cancelled，绝不重启。 |
| 1.11 | **96% 高 CPU 空转独立根因只读诊断** | **真待办** (挂起) | `PROJECT_STATE.md:90-106` | DAV-990 (blocked) | 待 DAV-1003 整合后复查 | **验收**：在网络隔离与 EOF 修复整合后，复测 96% 处是否仍有高 CPU，若自然消失则结项；若复现则单独定位。 |
| 1.12 | **API smoke 全局 executor 污染二分只读诊断** | **已交付** (历史留档) | `work/dav987-*.md` | DAV-987 (in_review) | 诊断目标已达成 | **结论**：二分数据已在 DAV-979/989 沉淀，作为历史诊断留档，可收口关闭。 |
| 1.13 | **分文件 RT-FULL 看门狗基线对照** | **已交付** (历史留档) | `PROJECT_STATE.md:47-58` | DAV-988 (in_review) | 用于 DAV-941 合入放行 | **结论**：已产出 214 OK / 8 失败 / 1 挂死的基线证据，历史职责完成，可收口关闭。 |

---

## 三、 数据完整性与时间语义窄修（2026-09-15 窄修及活跃 Backlog）

源依据：`work/2026-09-15-codex-to-claude-handoff.md`、`PROJECT_STATE.md:37-74`、看板快照。

| 序号 | 方案/任务项 | 状态 | 源路径及行号 / 证据 | 关联 Issue | 依赖关系 | 验收与授权边界 |
|:---:|:---|:---:|:---|:---:|:---|:---|
| 2.1 | **59c..b95 早期 9 刀数据完整性窄修** | **未部署** | Git 提交：`8064d7c` 至 `b95a9b8`<br>涵盖排序、重复行、边界截断等 | DAV-928, 929, 936, 949, 947, 950, 939, 935, 951 (全部 done) | 位于当前 trunk 历史中 | **验收**：已全部 FF 入主干，白名单与单测全过。<br>**授权**：等待全量部署门禁通过后上线。 |
| 2.2 | **b95..5a0 近期 7 刀主线合入（P2A 第二轮）** | **未部署** | Git 提交：`f125179`、`9f2ab95`、`8854853`、`331a322`、`483a109`、`28d1adc`、`5a0320f` | DAV-944, 940, 930, 938, 946, 934, 941 (全部 done) | 当前主线 tip `5a0320f` | **验收**：D-012 审查均有记录，DAV-941 包含父/候选完整 RT-FULL 对照（零新增）。<br>**授权**：等待部署门禁。 |
| 2.3 | **Alpha Vantage 日期过滤失败不得原样放回越界 CSV** | **真待办** | `tradingagents/dataflows/alpha_vantage_common.py:_filter_csv_by_date_range` | DAV-943 (backlog) | 独立窄修 | **验收**：遇不可解析日期或越界时 fail-closed 排除，不回退全量原始 CSV。<br>**授权**：代码审核员同 SHA 审查。 |
| 2.4 | **股票 K 线 API 必须二次限制返回日期窗口** | **真待办** | `api/main.py:get_kline`<br>`_parse_stock_csv` | DAV-952 (backlog) | 与 DAV-930 共享 `api/main.py`，需串行 | **验收**：股票分支对 vendor 返回数据执行严格 `[start_date, end_date]` 二次过滤。<br>**授权**：代码审核员同 SHA 审查。 |
| 2.5 | **yfinance 历史行情输出必须执行请求窗口校验** | **真待办** | `tradingagents/dataflows/y_finance.py:get_YFin_data_online` | DAV-953 (backlog) | 需在 DAV-946 基础上推进 | **验收**：`Ticker.history` 返回后二次过滤区间，杜绝返回请求窗口外的历史行情。<br>**授权**：代码审核员同 SHA 审查。 |
| 2.6 | **同步 D-009 决策状态契约测试，消除长期假红** | **真待办** | `tests/test_signal_processing.py`<br>`tests/test_dav27_report_semantics.py`<br>`tests/test_debate_state_persistence.py` | DAV-931 (backlog) | 依赖 D-009 状态定稿 | **验收**：消除 10 个因断言旧 `HOLD`/`BUY` 引起的假红，使全量套件恢复真实绿灯。<br>**授权**：测试契约对齐，无业务代码破坏。 |
| 2.7 | **同步图拓扑与日期方法白名单测试契约** | **真待办** | `tests/test_two_stage_analyst_topology.py`<br>`tests/test_provider_date_guards.py` | DAV-932 (backlog) | 依赖当前 Graph 实际接线 | **验收**：更新拓扑测试认识 Run Integrity Gate；修复 5 个拓扑断言假红。<br>**授权**：测试契约对齐。 |
| 2.8 | **固定测试时间并补齐 CNINFO 离线 mock** | **真待办** | `tests/test_social_data_api.py`<br>`tests/test_cninfo_disclosure_metadata.py` | DAV-933 (backlog) | 依赖离线化规范 | **验收**：消除因真实墙钟漂移（lookback=14天）引起的测试超时与失败，补齐 CNINFO 离线 mock。<br>**授权**：测试工程项。 |

---

## 四、 2026-08-27 统一方案（Track A 裁决修补与 D-009 决策语义）

源依据：`work/2026-08-27-unified-final-plan.md`、`work/2026-08-27-audit-decision-semantics-plan.md`。

| 序号 | 方案/任务项 | 状态 | 源路径及行号 / 证据 | 关联 Issue | 依赖关系 | 验收与授权边界 |
|:---:|:---|:---:|:---|:---:|:---|:---|
| 3.1 | **A0：前端 Chat 强制使用 v2 runtime** | **已交付** | `frontend/src/services/api.ts`（已设 override） | DAV-A0 | 独立前置 | **验收**：前端发起的分析请求默认带 v2 标识。<br>**授权**：已合入。 |
| 3.2 | **A1：财报假缺口账本（有数字无 ISO 日期不标失败）** | **已交付** | `tradingagents/graph/data_collector.py:839` `available_unverified_as_of` provenance | DAV-A1 | 基础数据流 | **验收**：财报正文有数字但缺乏严格发布日时，标为可用但未核验，不进入 failure gaps。<br>**授权**：已合入。 |
| 3.3 | **A2：缺日线 OHLCV 执行 fail-closed 阻断** | **已交付** | `tradingagents/agents/analysts/market_analyst.py`<br>`extract_and_validate_manager_verdict` | DAV-A2 | 决策安全闸 | **验收**：688981 等缺乏行情数据场景禁止生成 bull/bear 方向。<br>**授权**：已合入。 |
| 3.4 | **A3：Few-shot 与 Hold 去偏修补** | **已交付** | `tradingagents/prompts/zh.py` | DAV-A3 | 提示词工程 | **验收**：移除提示词中把多头当作基准的倾向性用语，Hold 规则客观化。<br>**授权**：已合入。 |
| 3.5 | **A4：资金流对打默认 tie，禁止「流出=吸筹」** | **已交付** | `tradingagents/graph/evidence_verifier.py`<br>`research_manager.py:147-182` | DAV-A4 | 证据校验 | **验收**：EM 与 THS 口径对打时裁定 tie，禁 Wyckoff 人格化洗盘脑补。<br>**授权**：已合入。 |
| 3.6 | **A5：v2 winner 对接 T+5 校准管线** | **已交付** (未部署) | `api/services/calibration_service.py` | DAV-A5 | 依赖历史回填 | **验收**：`/v1/calibration` 接入 v2 winner 统计口径。<br>**授权**：代码已进主线，待部署运行。 |
| 3.7 | **A6：门槛验证脚本严格仅统计 v2** | **已交付** | `scripts/verify_h1b_gates.py` | DAV-A6 | H1b 门槛依据 | **验收**：脚本显式隔离 legacy 样本，仅对 v2 样本做行业与分侧检验。<br>**授权**：代码已交付。 |
| 3.8 | **P0-1：引入独立决策四元组与 7/7 上游失败中断** | **已交付** (未部署) | `api/database.py:343-387`<br>`api/main.py:2580-2643`<br>`analysis_status`, `trade_action`, `direction`, `risk_status` | DAV-P0-1 | 核心语义层 | **验收**：支持 `INVALID_RUN`、`DATA_ERROR`、`ABSTAIN`；7/7 失败不得出中性。<br>**授权**：主干已合入，待部署上线。 |
| 3.9 | **P0-2：Point-in-time（PIT）统一证据契约与禁前复权** | **已交付** (未部署) | `cn_akshare_provider.py:718-815`<br>`yfinance_provider.py` | DAV-P0-2, DAV-946, DAV-949 | 跨供应商契约 | **验收**：历史回溯严格禁用前复权（qfq），严格按 as-of cutoff 过滤。<br>**授权**：已合入主线。 |
| 3.10 | **P0-3：财务 `period_kind` 与单季 `H1-Q1` 推导** | **已交付** (未部署) | `tradingagents/dataflows/financial_announce.py`<br>`DAV-939` commit `d29e741` | DAV-P0-3, DAV-939 | 财报解析 | **验收**：区分累计与单季，杜绝将 H1 累计值叫作 Q2 单季。<br>**授权**：已合入主线。 |
| 3.11 | **P0-4：证据 Cluster 与资金流共识解耦** | **已交付** (未部署) | `tradingagents/graph/evidence_verifier.py`<br>`DAV-808` prompt 守卫 | DAV-P0-4, DAV-808 | 命题核验 | **验收**：消除单一价格冲击被三大分析师重复计票问题。<br>**授权**：已合入主线。 |
| 3.12 | **P1-1：新闻事件覆盖与统一 `first_seen_at`** | **缺证待核** | `tradingagents/dataflows/news_provider.py` | DAV-P1-1 | 新闻数据流 | **现状**：新闻窗口过滤已做，但全局跨平台统一 `first_seen_at` 与催化剂覆盖率指标在实跑中缺乏落盘证据。 |
| 3.13 | **P1-2：Capitulation / Reversal 候选确认机制** | **替代废止** (研究保留) | `work/2026-08-27-audit-decision-semantics-plan.md:162-175` | DAV-P1-2 | 策略层演进 | **结论**：属于生成侧策略高级特性，未列入近两周施工白名单，按 D-009 保留为研究项，当前不派工。 |
| 3.14 | **P1-3：回测与校准严格排除无效/未决样本** | **已交付** (未部署) | `api/services/backtest_service.py`<br>`calibration_service.py` | DAV-P1-3, DAV-998 | 评价准确性 | **验收**：`INVALID_RUN`、`DATA_ERROR` 绝不计入胜率与校准；待 DAV-998 合入后闭环。 |
| 3.15 | **P1-4：Provider 红黄绿灯显式状态机与 typed refusal** | **已交付** (未部署) | `tradingagents/dataflows/interface.py`<br>`historical_cases.py` | DAV-P1-4, DAV-941 | 供应商治理 | **验收**：供应商失败返回结构化 `VendorRefuse`，记录错误码与可重试标志。<br>**授权**：已入主线。 |

---

## 五、 2026-08-27 统一方案（Track B 社交舆情全链路与 Gate 0–4）

源依据：`work/2026-08-27-unified-final-plan.md`、`docs/social_data/implementation_plan.md`、`docs/social_data/acceptance_checklist.md`。

| 序号 | 方案/任务项 | 状态 | 源路径及行号 / 证据 | 关联 Issue | 依赖关系 | 验收与授权边界 |
|:---:|:---|:---:|:---|:---:|:---|:---|
| 4.1 | **B1–B7：社交数据 Contracts / Schema / Importer / Entity / Bundle** | **已交付** (未部署) | 主线 `5a0320f` 存在：<br>`tradingagents/dataflows/social/contracts.py`<br>`archive_schema.py`<br>`mediacrawler_importer.py`<br>`entity_resolver.py`<br>`provider.py`<br>`classifier.py`<br>`collector.py` | DAV-P2-T1~T7 (全部 done) | 离线基础设施 | **验收**：时间语义第二轮映射（`published_at`, `first_seen_at`, `snapshot_at`），append-only archive，确定性实体解析。<br>**授权**：纯离线模块，主线已合入。 |
| 4.2 | **B8–B12：Collector 挂载、Graph 接线、Prompt 分离与报告映射** | **已交付** (未部署) | 主线 `5a0320f` 存在：<br>`analyst_adapter.py`<br>`prompt_formatter.py`<br>`data_collector.py`<br>`trading_graph.py` | DAV-P2-T8~T12 (全部 done) | 核心主链接入 | **验收**：`_fetch_all` 之后调用短超时独立采集；Prompt 区分新闻与社交；`direction_allowed=false` 保护生效。<br>**授权**：主线已合入。 |
| 4.3 | **B13–B14：运维导入工作流与 Shadow / Canary 门禁代码** | **已交付** (未部署) | `tradingagents/dataflows/social/registry.py`<br>`tests/test_social_rollout_gates.py` | DAV-P2-T13~T14 (全部 done) | 部署治理机制 | **验收**：`TA_SOCIAL_MODE` 默认 `disabled`；支持 `shadow` 和 `active` 切换逻辑。<br>**授权**：主线已合入。 |
| 4.4 | **B15 / Gate 4：彻底移除 legacy_proxy 代码** | **已交付** (未部署) | `tradingagents/dataflows/social/` 下已无 `legacy_proxy.py`，全走 archive provider | DAV-P2-T15b (done) | 依赖 B1–B14 | **验收**：历史无保护代理调用已物理删除。<br>**授权**：主线已合入。 |
| 4.5 | **Gate 0：独立合规运行环境与固定版本（AUTH-01）** | **缺证待核** | `docs/social_data/acceptance_checklist.md:Gate 0`<br>MediaCrawler SHA `d6f7c5bb...` | 外部环境依赖 | 真实采集前置 | **验收**：独立 Python 环境与爬虫依赖就绪，不污染主服务。<br>**授权边界**：需独立服务器/沙箱资源。 |
| 4.6 | **Gate 1：离线数据契约核验与发布时间判定文档** | **已交付** (离线) | `work/xhs_last_update_time_verification.md` | Gate 1 门禁 | 离线数据包 | **验收**：小红书 `last_update_time` 语义核验完成，不参与资格判断。<br>**授权**：已完成离线验收。 |
| 4.7 | **Gate 2：受控账号、Cookie 与真实小样本采集导入（AUTH-02/03）** | **缺证待核** (未授权) | `docs/social_data/implementation_plan.md:Gate 2` | 外部运维项 | **阻塞：未获授权** | **授权边界**：**David 独占红线**：严禁在未获用户显式授权前配置真实 Cookie 或向外网平台发起爬取。 |
| 4.8 | **Gate 3：生产环境 Shadow 连续运行（30 份报告/10 标的）（AUTH-04/05）** | **缺证待核** (未运行) | `docs/social_data/acceptance_checklist.md:Gate 3` | 服务部署后推进 | 依赖部署主线及 Gate 2 | **验收**：线上服务在 `TA_SOCIAL_MODE=shadow` 下稳定生成 30 份报告，社交数据打标但不对多空方向计票。<br>**授权边界**：随部署门禁放行。 |
| 4.9 | **Gate 4：小范围 Canary 试放行与全量 Active（AUTH-06/07）** | **真待办** (未启动) | `docs/social_data/implementation_plan.md:Gate 4` | 最终商业激活 | 依赖 Gate 3 绿灯 | **授权边界**：**David 独占授权**：必须由用户确认 Canary 表现后，方可切换 `TA_SOCIAL_MODE=active`。 |

---

## 六、 收益评估实验与基线（V-03 / V-03a / H1b / 冻结表）

源依据：`work/v03-freeze-sheet-20260909.md`、`work/2026-09-13-full-repository-plan-audit.md:P0-C,P0-D`。

| 序号 | 方案/任务项 | 状态 | 源路径及行号 / 证据 | 关联 Issue | 依赖关系 | 验收与授权边界 |
|:---:|:---|:---:|:---|:---:|:---|:---|
| 5.1 | **V-03a 评估引擎与消融框架（四种消融、隔离库 runner）** | **已交付** (未部署) | 主线 `5a0320f` 存在：<br>`tradingagents/eval/v03_return_measure.py`<br>`tests/test_v03_return_measure.py` | DAV-866 (done) | 收益评测底座 | **验收**：默认 provenance 修复完成（历史生成 SHA 不冒充当前 running SHA），21 定向测试全绿，全量无新增失败。<br>**授权**：已合入主线。 |
| 5.2 | **V-03a 前置 Blocker：股票代码规范化（Canonical Symbol）** | **已交付** (代码) /<br>**缺证待核** (数据) | `work/v03-freeze-sheet-20260909.md:46-60`<br>DAV-800 提交 `5a00c75` | DAV-800 (done) | 阻塞正式实验 | **验收**：代码已实现 1408 守恒与 3 处 collision 合并；<br>**授权边界**：**写生产库清库需要 David 明确授权 + 备份**，当前数据库尚未跑迁移脚本。 |
| 5.3 | **P0-C：真实 Price Provider 股票池安全边界（ST/新股过滤）** | **真待办** | `tradingagents/eval/v03_return_measure.py:917-924`<br>`VendorPriceDataProvider.is_st()` 恒返 `False` | 待建卡 (P0-C) | 阻塞正式实验准确性 | **验收**：接入真实 ST 列表与上市日期核验，未知状态必须 typed exclude，严禁 fail-open 放行。<br>**授权**：代码审核员审查。 |
| 5.4 | **P0-D：V-03a 行情截止日动态化与系统完整度动态计量** | **真待办** | `tradingagents/eval/v03_return_measure.py:837-840` 行情截止写死 `2026-09-09`；`:222-229` 完整度写死 | 待建卡 (P0-D) | 阻塞正式实验持续计量 | **验收**：行情截止日与 forward OOS 上界参数化；系统完整度改为实测值或 unknown，废除静态写死。<br>**授权**：工程窄修。 |
| 5.5 | **P1-A：当前候选完整 V-03 实验与全量证据归档** | **缺证待核** (未就绪) | `work/2026-09-13-full-repository-plan-audit.md:P1-A` | 正式评测里程碑 | 依赖 5.3、5.4、主线部署 | **验收**：同一 cutoff、同一交易成本、严格 purge/embargo，出具 OOS 净值曲线与消融报告。<br>**定性约束**：系统输入未齐前，仅作进度基线，不作 AI 盈利定性结论。 |
| 5.6 | **H1b 门槛脚本与样本池激活保护** | **已交付** (规则) /<br>**替代废止** (暂不激活) | `DECISIONS.md` D-006/D-007<br>`scripts/verify_h1b_gates.py` | 持续长效门禁 | **绝对红线：维持 KEEP_FALSE** | **约束**：样本池未满足且服务未部署前，严禁打开 `credit_weighting_enabled`，严禁缩短 T+5 或放宽门槛。 |

---

## 七、 博弈论可达性与生产图连贯（E 链）

源依据：`work/2026-09-13-full-repository-plan-audit.md:P0-B`、`.hermes/plans/2026-09-06_整合施工计划-v1.1.md`。

| 序号 | 方案/任务项 | 状态 | 源路径及行号 / 证据 | 关联 Issue | 依赖关系 | 验收与授权边界 |
|:---:|:---|:---:|:---|:---:|:---|:---|
| 6.1 | **E-01：关系图生产者与 Reducer 架构** | **已交付** (未部署) | `tradingagents/graph/` 关系图相关代码已在主线 | DAV-828 (done) | 命题图基础 | **验收**：支持从 Claim 到 Claim 的关系图构建与消费。<br>**授权**：已合入主线。 |
| 6.2 | **E-02 / DAV-808：自定义 Prompt 纳入硬约束三层守卫** | **已交付** (已部署上线) | `work/dav856-merge-deploy-20260913.md`<br>`DECISIONS.md` D-015/D-016 | DAV-808, DAV-856 (done) | 已于 09-13 部署 | **验收**：保存/加载/经理三层阻断，违规提示词全单 fail-closed 判 NO_TRADE，不写库。<br>**授权**：已上线运行。 |
| 6.3 | **E-03：多空研究员、证据核验与经理反驳 WAIT 修复** | **已交付** (未部署) | `tradingagents/agents/analysts/`<br>R1/R2/R3 fixture 在主线 | DAV-846~854 (done) | 辩论质量层 | **验收**：普通反驳恢复 WAIT 状态，多空证据核验对称，极端杀跌不再强行多空。<br>**授权**：已合入主线。 |
| 6.4 | **E-04：预期修正契约三轮返修合入** | **已交付** (未部署) | Git 提交 `bdb95f8`<br>42 项契约，2 项密度测试全绿 | DAV-877 (done) | 业绩修正处理 | **验收**：禁止从 PDF/正文无凭据脑补数字，严格 PIT 预期对比。<br>**授权**：已合入主线，待部署。 |
| 6.5 | **P0-B：博弈论接线运行时硬化与生产非空证据** | **真待办** | `tradingagents/graph/trading_graph.py:279-305`<br>异常捕获静默退回原图；数据库 0 条非空 | 待建卡 (P0-B) | 影响 V-03 完整度 | **验收**：接线失败时写入 typed `game_theory_unavailable` 或明确报错，补接线失败红队；部署后产生非空业务报告回读证据。<br>**授权**：工程代码硬化。 |

---

## 八、 前端产品与展示层收口

源依据：`work/2026-09-13-full-repository-plan-audit.md:P1-C`、`docs/KNOWN_ISSUES.md:8-26`。

| 序号 | 方案/任务项 | 状态 | 源路径及行号 / 证据 | 关联 Issue | 依赖关系 | 验收与授权边界 |
|:---:|:---|:---:|:---|:---:|:---|:---|
| 7.1 | **Dashboard 优先展示 Partial Analysis Status** | **未部署** | `frontend/src/pages/Dashboard.tsx`<br>Git 提交 `331a322` | DAV-938 (done) | 主线已合入 | **验收**：静态语义核验通过，PARTIAL 优先级高于 trade_action 回落。<br>**授权**：待部署。 |
| 7.2 | **P1-C：次级页面接入中文 Direction 映射** | **真待办** | `frontend/src/components/TrackingBoardPanel.tsx:663,827`<br>`frontend/src/pages/Portfolio.tsx:621` | 待建卡 (P1-C) | 展现层收口 | **验收**：调用 `reportText.ts` 的 `localizeDirection`，消除次级页面直显 raw English direction 的已知缺陷。<br>**授权**：纯前端组件修补。 |
| 7.3 | **前端生产构建与 UI 测试独立放行核验** | **缺证待核** | `frontend/` 目录；宿主环境缺 `node_modules` | 持续交付流水线 | 依赖 Node 环境 | **验收**：在包含完整依赖的干净环境中执行 `npm test` 与 `npm run build`，出具通过日志，纳入部署前置证据包。<br>**授权**：CI/本地构建验证。 |

---

## 九、 生产主线既有 20 项测试红灯对账

源依据：`work/2026-09-16-final-audit-and-integration-plan.md:189-196`、`PROJECT_STATE.md:60-63`。

在主线 `5a0320f` 上执行离线全量测试存在 20 项基线失败（非本轮新增，属于历史遗留）。这些失败对例如下：

| 序号 | 测试文件与失败项 | 真实性质 | 对应处置方案 / 关联 Issue |
|:---:|:---|:---:|:---|
| 8.1 | `test_cninfo_disclosure_metadata` (1 项) | 假红 (环境/时间漂移) | 归入 **DAV-933**，补齐离线 fixture 与固定时间戳。 |
| 8.2 | `test_dav27_report_semantics` (2 项) | 假红 (断言旧 HOLD) | 归入 **DAV-931**，同步 D-009 决策状态断言。 |
| 8.3 | `test_debate_state_persistence` (5 项) | 假红 (断言旧决策契约) | 归入 **DAV-931**，同步 D-009 四元状态断言。 |
| 8.4 | `test_game_theory_integration` (1 项) | 契约假红 / 隔离边界 | 归入 **P0-B** 与拓扑契约同步。 |
| 8.5 | `test_h1b_gates` (1 项) | 既有样本池口径断言 | 保留作为 H1b 门槛未达的真实安全红灯，不强行抹平。 |
| 8.6 | `test_provider_date_guards` (1 项) | 契约假红 (方法白名单) | 归入 **DAV-932**，同步方法白名单契约。 |
| 8.7 | `test_recalculate_weekly_metrics` (1 项) | 历史样本计数边界 | LOW 级既有技术债，保持现状。 |
| 8.8 | `test_signal_processing` (3 项) | 假红 (指标提取断言旧值) | 归入 **DAV-931**，同步信号处理断言。 |
| 8.9 | `test_social_data_api` (1 项) | 假红 (墙钟超 14 天过期) | 归入 **DAV-933**，固定测试时间，解耦系统当前时间。 |
| 8.10 | `test_two_stage_analyst_topology` (4 项) | 假红 (未更新门禁节点) | 归入 **DAV-932**，更新拓扑断言认识 Run Integrity Gate。 |

> **对账结论**：20 项既有红灯中，**有 16 项是纯粹的测试契约滞后或时间漂移假红**（已在 DAV-931、DAV-932、DAV-933 的工作范围内）；其余 4 项为受控的安全门禁或次级技术债。消除假红后，全量套件可达 99.7% 以上真绿。

---

## 十、 统揽总账：看板 18 张活跃卡对账与处置建议

基于 `/Users/davidliu/Documents/Codex/2026-09-17/remaining-work-audit/issues.json` 现场快照：

| Issue 标识 | 当前状态 | 标题 / 职责 | 本清单对账定性 | 下一步处置与派工建议 |
|:---|:---:|:---|:---:|:---|
| **DAV-979** | in_progress | RT-FULL 网络穿透与活锁收敛 | **真待办 (总控)** | 保持进行中，作为门禁主控卡，待 DAV-1003 整合后复测收口。 |
| **DAV-1021** | in_review | 卡 C lifespan 全局状态恢复 (SPEC_V2) | **未部署 (审查中)** | 派代码审核员同 SHA 审查；通过后并入 DAV-1003 整合候选。 |
| **DAV-1009** | in_review | 卡 B 离线护栏拒绝上传播 (第一阶段诊断) | **真待办 (诊断中)** | 验收第一阶段诊断证据；若无需大改则形成明确窄修进入实施。 |
| **DAV-1007** | in_review | 卡 A pytest 网络隔离架构 (audit hook) | **已交付 (候选待并)** | DAV-1008 复审已 PASS，冻结于 `8589e65`，直接作为整合基石。 |
| **DAV-998** | in_review | T+1 历史案例 refusal 误标终态阻断级返修 | **未部署 (待合入)** | 核心逻辑已由 DAV-1002 PASS，作为必选内容并入 DAV-1003 整合候选。 |
| **DAV-1003** | blocked | 唯一收口整合候选（989/995/996/998） | **真待办 (解阻断)** | 按终版审计意见返修（提前 try/finally、baostock 硬化 fail-closed），重新生成整合候选。 |
| **DAV-989** | in_review | API lifespan 全局 executor 泄漏窄修 | **替代废止 (并入)** | 改动并入卡 C (DAV-1021) 与 DAV-1003 后，收口关闭。 |
| **DAV-995** | in_review | 离线网络拦截护栏与 socket 隔离 | **替代废止 (并入)** | 改动经去静默/去全局超时后已分拆入卡 A (DAV-1007) 与整合卡，旧卡关闭。 |
| **DAV-996** | in_review | 复位 api_smoke 全局状态与 registry | **替代废止 (并入)** | 有效复位逻辑已并入卡 C 与整合卡，旧卡关闭。 |
| **DAV-987** | in_review | 只读二分：API smoke executor 污染与 96% | **已交付 (留档收口)** | 诊断任务已完成，证据已采纳，可直接关闭。 |
| **DAV-988** | in_review | 分文件 RT-FULL 看门狗对照基线 | **已交付 (留档收口)** | 交付证据已完成，已支撑前序合入，可直接关闭。 |
| **DAV-990** | blocked | 只读诊断：96% 高 CPU 空转独立根因 | **真待办 (挂起)** | 保持 blocked，待 DAV-1003 整合候选落地后单跑复查。 |
| **DAV-943** | backlog | Alpha Vantage 日期过滤失败越界 CSV | **真待办 (Backlog)** | 待整合门禁放行后，派资深开发单关注点窄修。 |
| **DAV-952** | backlog | 股票 K 线 API 二次限制返回日期窗口 | **真待办 (Backlog)** | 待整合门禁放行后，串行修补 `api/main.py`。 |
| **DAV-953** | backlog | yfinance 历史行情输出执行请求窗口校验 | **真待办 (Backlog)** | 待整合门禁放行后，派资深开发窄修。 |
| **DAV-931** | backlog | 同步 D-009 决策状态契约测试 (消除 10 假红) | **真待办 (Backlog)** | 优先推进，消除假红，恢复全量套件置信度。 |
| **DAV-932** | backlog | 同步图拓扑与日期方法白名单契约 (消除 5 假红) | **真待办 (Backlog)** | 与 DAV-931 同步推进，对齐拓扑契约。 |
| **DAV-933** | backlog | 固定测试时间并补齐 CNINFO 离线 mock (消除 1 假红) | **真待办 (Backlog)** | 推进测试时间解耦，消除时钟漂移测试红灯。 |

---

## 十一、 唯一推荐落地施工顺序（严格服从 D-018 原流程）

基于各项任务的底层代码依赖与授权边界，提出以下五个阶段的推进顺序（**不引入任何新流程改革，严格走老流程写审分离与调度协议**）：

```text
【阶段一：解除阻断与整合收口】（当前最高优先级）
  1. 返修并落地唯一整合候选 DAV-1003（融合 DAV-998 修复、卡 A 8589e65 隔离、卡 C lifespan 规范、修复 3 处静默捕获与全局超时污染）；
  2. 代码审核员同 SHA 独立只读复审 DAV-1003；
  3. 执行 RT-FULL-OFFLINE 完整回归，确认相较 5a0320f 基线零新增失败；
  4. 满足 D-013 证据放行，非强制快进合入主线，收口 DAV-979、DAV-998、DAV-1003、DAV-1007、DAV-1021。
  5. 顺手收口已完成/废止的历史子卡（DAV-987, 988, 989, 995, 996）。

【阶段二：消除既有假红与剩余 Backlog 窄修】
  1. 推进 DAV-931, DAV-932, DAV-933 测试契约同步，消除主干 16 项既有假红，实现全量测试真绿；
  2. 串行推进剩余 3 张数据窄修：DAV-943 (Alpha Vantage), DAV-952 (K线 API 窗口), DAV-953 (yfinance 窗口)；
  3. 每卡经单关注点 commit + 代码审核员同 SHA 审查 + RT-FULL 对照快进合入。

【阶段三：生产独立部署与服务上线】（重点里程碑）
  1. 此时主干汇集 5a0320f 前 27 提交 + 整合收口提交 + 假红修复提交；
  2. 执行 SQLite 完整性检查与 consistency `.backup()`，记录 1409 报告守恒；
  3. 部署代码并启动 8000 端口服务，回读 `/healthz` SHA 严格等于主线 SHA；
  4. 验证残留报告恢复（failed=0），受控执行一次业务只读 smoke，证明线上服务复活。

【阶段四：评估安全与产品展现层修补】
  1. 实施 P0-B（博弈论接线异常 typed 记录，杜绝静默吞错）；
  2. 实施 P0-C（V-03a 真实 price provider ST/新股可核验过滤）；
  3. 实施 P0-D（V-03a 行情截止日动态化与完整度实测）；
  4. 实施 P1-C（前端 Portfolio 与 TrackingBoard 中文方向映射）。

【阶段五：外部受控授权项】（需 David 亲自单项授权）
  1. 授权并执行生产数据库 Canonical Symbol 历史迁移清洗；
  2. 授权配置受控 Cookie 与独立沙箱，推进社交 Gate 0~2 真实小样本测试；
  3. 生产服务稳定后，视 forward 样本积累情况重新核验 H1b 门槛，决定是否激活信用加权。
```

---
*清单编制完成，已写入 `/Users/davidliu/Documents/Codex/2026-09-17/remaining-work-audit/plan-inventory.md`。未改动任何产品代码，未删除任何历史文件。*
