# TradingAgents-AShare 审计与可执行修改方案

日期：2026-08-27  
审计对象：`/Users/davidliu/Documents/TradingAgents-AShare`  
审计性质：只读恢复现状、案例审计、施工设计；本次未修改仓库代码、配置、数据库或 Git 历史。

## 1. 结论先行

当前系统已经完成一批“数据拒绝和证据核验”的局部修补，但还没有形成一条能把“数据不可用、数据可用但观点不确定、风险高但方向未知”严格分开的闭环。最严重的缺口不是再增加一个分析师，而是状态、时间、证据依赖和交易动作仍然存在语义坍缩：

`上游失败 / 前视数据 / 证据冲突 / 方向分歧` → `中性或 HOLD` → `API completed` → 可能进入回测、统计和校准。

建议按以下顺序施工：

1. **P0：先堵住错误决策和回测污染。** 引入独立的 `analysis_status`、`trade_action`、`direction`、`risk_status`；实现 7/7 上游失败的 `INVALID_RUN/DATA_ERROR`；统一 point-in-time 证据契约；禁止历史 qfq/未来财报；把资金流语义和证据 cluster 解耦。
2. **P1：恢复可用的研究能力。** 增加财务 `period_kind` 与单季推导、新闻事件覆盖和首次发现时间、capitulation/reversal 候选及确认机制、业务换锚/催化剂链；回测和 T+5 校准只使用合格运行。
3. **P2：完成社交舆情独立链路和校准运营。** 当前社交代码只到 archive/importer/entity resolver，必须经 provider、聚合器、collector、状态、报告和 shadow/canary Gate 后才可 active。

本报告把引用对话中的四组案例作为验收样本，但没有把对话中的外部事实冒充成仓库原始快照。当前仓库未发现这四个日期对应的完整、可重放原始报告和行情/新闻快照；因此案例中的具体数值、新闻内容和模型输出需要施工时以冻结 fixture/manifest 补齐后才能称为“已重放”。

## 2. 真实现状恢复

### 2.1 Git 和工作树

本次实际读取结果：

| 项目 | 当前值 |
|---|---|
| 路径 | `/Users/davidliu/Documents/TradingAgents-AShare` |
| 分支 | `codex/dav-4-p2a-trunk` |
| HEAD | `de88de4eb33b7595d6fcb9a4c4e84d0a69267db5` |
| 跟踪远端 | `origin/codex/dav-4-p2a-trunk`，与本地 HEAD 同步 |
| origin | `https://github.com/Guuuu233/tradingagents-ashare-fork.git` |
| 最近提交 | `de88de4 feat(social): add deterministic equity entity resolver` |
| 当前 tracked dirty | `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json` |
| 当前 untracked | 共享上下文、handoff、`.squad/`、`.hermes/`、大量 `work/` 与临时文件 |

当前 tracked diff 仅 3 个文件、27 insertions/12 deletions；`git diff --check` 未发现空白错误。施工时必须保留这些脏文件，不得 `git add .`、不得清理 untracked、不得把它们混进数据/社交提交。

`docs/social_data/implementation_plan.md` 和 `work/2026-08-27-unified-final-plan.md` 的基线仍写着旧 SHA `aa41f44...`；`PROJECT_STATE.md` 也是约 16:30 的快照，并且其中 tip 的 SHA 后缀与 Git 实际值不一致。它们可作为施工意图参考，不能作为当前 HEAD、issue 或部署状态的证明；当前 Git 事实优先。

### 2.2 最近落地的施工进度

从当前 Git 可验证的提交看：

| 轨道 | 已落地/存在 | 当前边界 |
|---|---|---|
| 数据/裁决 | 财报公告日截断（A4 逻辑）、非法 collector 日期 fail-closed、日线 OHLCV 缺失方向闸、资金流冲突 tie 闸、去 bullish few-shot 的 prompt 修补 | 这些是局部闸，不是顶层运行状态；仍会得到 `中性/HOLD` 及 `completed` 语义 |
| 财报 | `financial_announce.py` 已有报告期、法定披露期限、有效公告日和历史截断 | 没有统一的 `period_kind`，不能保证把 H1 累计值与 Q2 单季区分 |
| 新闻 | 东财新闻要求可解析发布时间并按窗口过滤；历史新闻有 provider allowlist | 没有统一的新闻 `first_seen_at`、事件去重/cluster、催化剂覆盖指标 |
| 资金流 | `fund_flow_evidence.py` 有字段/单位/日期语义和审计证据；新算法与 legacy Web 有区分 | `select_fund_flow_source()` 是选择器，不是同字段多源共识；provider 仍把 selector 兼容写入 `metadata["consensus"]`，容易误读 |
| 社交 | `social/contracts.py`、`archive_schema.py`、`mediacrawler_importer.py`、`entity_resolver.py` 已存在，最近 `de88de4` 合入实体解析 | 当前没有 `social/provider.py`、`aggregator.py`、`classifier.py`、`analyst_adapter.py`；尚未进入 DataCollector/Graph/API 主路径 |
| 前端/运行 | `frontend/src/services/api.ts` 有未提交的 v2 runtime override | 不属于本次报告施工；不得覆盖或擅自提交 |
| 加权 | H1b 规则和 shadow credit 代码存在，生产 flag 仍应保持 False | 未到 `ELIGIBLE_FOR_ACTIVATION` 前，禁止让 credit weighting 改变裁决 |

当前默认配置代码是 `max_debate_rounds=3`、`max_risk_discuss_rounds=3`；本项目既有约束要求保留用户当前 3/1 运行边界，施工方案不通过增加轮次解决问题。`credit_weighting_enabled`、模型绑定、密钥和用户配置也不在本次改动范围。

### 2.3 当前测试基线

| 检查 | 结果 | 解释 |
|---|---:|---|
| `pytest tests --collect-only -q` | **2186 collected，exit 0** | 仅跑 `tests/`，没有把 `work/` 下历史测试目录混入 |
| 纯离线核心集（资金流、OHLCV、数据缺口、财报预告、资金语义、社交 contract/importer/entity、vendor、verdict） | **142 passed，exit 0** | 当前局部修补的可重复基线 |
| 财报公告日纯函数集 | **21 passed，11 deselected，exit 0** | 公告日窗口/列过滤逻辑可重复 |
| 较宽定向集 | **118 passed，5 failed，随后因网络型 smoke 中止** | 失败见下表；不能称为全绿 |

当前已重现的 5 个红灯：

1. `tests/test_financial_announce_cutoff.py::test_provider_historical_refuses_ths_fallback`：新浪财报失败后，历史日期仍返回带公告日的备用表，不符合测试要求的历史拒绝语义。
2. `tests/test_financial_announce_cutoff.py::test_fund_flow_requires_curr_date_and_oor_message`：请求日期早于可用数据区间时，返回了别的日期的 Tushare 资金流，而不是明确 OOR/fail-closed。
3. `tests/test_financial_as_of.py::test_smoke_three_tickers_eight_interfaces_as_of[600900.SH]`：`balance_sheet` 的可验证 `as_of` 为 None。
4. 同一 smoke 的 `000333.SZ` 失败。
5. 同一 smoke 的 `600276.SH` 失败。

这些红灯直接说明：现有局部“公告日/资金流/数据缺口”规则尚未形成所有 provider 一致执行的 point-in-time 合同。网络 smoke 在本机耗时过长并被手动中止，后续验收必须全部改为冻结 fixture 的离线重放，再另外做有限的 provider smoke。

## 3. 当前执行链和问题落点

```text
provider/router
  → DataCollector._fetch_all / _build_source_provenance / data_failure_ledger
  → 七个 analyst（错误多被转为字符串或退化文本）
  → bull/bear/debate claims
  → research_manager_node
  → extract_and_validate_manager_verdict
  → trader/risk manager
  → TradingAgentsGraph result
  → api.main 结构化提取与 ReportDB
  → backtest_service / calibration_service
```

当前最关键的语义断点：

- `tradingagents/graph/data_collector.py:152` 的 VPA 已识别“卖出高潮/高位放量滞涨”，但输出仍是文本标签，未建立统计置信度、独立来源或后续确认状态。
- `data_collector.py:330` 附近的 `_safe()` 将异常变成“调用失败”字符串；`_build_data_failure_ledger()` 只记录数据源失败，未形成 7/7 analyst 的运行完整性判定。
- `data_collector.py:839` 的 provenance 已处理部分 `available_unverified_as_of`，但仍是 source-level 字典，不是贯穿新闻、资金、财务、价格的统一证据记录。
- `data_collector.py:1410` 的非法 collector 日期已 fail-closed；这是正确方向，但不能替代所有 provider 的实际日期/期间验证。
- `research_manager.py:147-182`：资金流 guard 被阻断时直接生成 `direction=中性`、`winner=tie`、0% 仓位；这是“禁止交易”的安全动作，却仍伪装成“中性观点”。
- `evidence_verifier.py:1096-1425`：缺 OHLCV 或资金流分歧可以把 bull/bear 改成 tie，但没有 `INVALID_RUN/DATA_ERROR/ABSTAIN`；解析器也只返回 winner/direction/position 等字段。
- `tradingagents/prompts/zh.py:31-73,254-375`：manager 机读协议只允许 bull/bear/tie 与看多/看空/中性；中性同时承载数据不足、冲突和真实势均力敌。
- `prompts/zh.py:526-528`：Trader 的 HOLD 规则要求技术、资金、催化剂全部无方向才可 HOLD，并要求信号满足其一就 BUY/SELL；这会把“不确定但有风险”和“有效方向”挤到二元交易动作。
- `prompts/zh.py:610-636`：smart-money prompt 把资金流转成“主力真实意图/建仓/洗盘/派发/主力成本区间”，并明确把“净流出+急跌+缩量/长下影”写成“假摔洗盘”。
- `prompts/zh.py:638-733`：volume-price prompt 把“局内人”作为可推断主体，并把形态直接映射为买卖行为；这正是高位放量滞涨被讲成洗盘、VWMA 被讲成主力成本的生成侧诱因。
- `api/database.py:343-387` 的 `ReportDB` 只有 lifecycle `status`、`decision`、`direction`、`confidence` 等字段，没有独立的分析有效性/交易动作/风险状态。
- `api/main.py:2580-2643` 只接受 `BUY/SELL/HOLD`；不认识 `WAIT/NO_TRADE/ABSTAIN`，会回退到 `UNKNOWN` 或旧的 graph decision。
- `api/main.py:3120-3164` 对成功图结果直接写 `status=completed`；即使结果包含严重 data gaps，也没有运行完整性状态。
- `api/services/backtest_service.py:155-241` 将无法识别的文本归类为 HOLD；`_get_price_after()` 在数据不足时还会缩短 hold_days，破坏严格 T+N 语义。
- `api/services/calibration_service.py:277-388,434-460` 只筛 `ReportDB.status == completed` 并计算概率/结果；没有先排除 `INVALID/ABSTAIN/NO_TRADE`，也没有统一价格基准/调整模式证明。

## 4. 四组案例审计结论

下表的“案例事实”来自用户提供的引用对话；“代码对应”是本次实际读取的当前仓库。代码和测试证据已核验，案例原始报告未在仓库中完整发现，需作为 PIT fixture 补录。

### 4.1 歌尔股份 2026-05-28

用户案例指出：高位巨量滞涨被解释为洗盘；技术/量价/资金高度共线；东方财富与同花顺资金口径冲突却选择性排除不利证据；VWMA 被人格化为“主力成本”；历史前复权污染；AI 终端国标发布时间/新鲜度错误；核心分歧未确认仍次日开仓；没有 WAIT/NO_TRADE。

当前对应问题：

- VPA 只输出形态文字，smart-money/volume-price prompt 再用 Wyckoff 叙事补足“主力行为”，没有“这是同一价格冲击的重复描述”这一约束。
- `cn_akshare_provider.py:2389-2444,2542-2695` 保留多源侧证据，但 `fund_flow_evidence.py:1384` 的选择器与 `metadata["consensus"]` 命名混在一起；EM `r0_net` 与 THS `netamount` 并非天然同义，不能选择性只留有利口径。
- stockstats `vwma` 只有技术指标值；当前 prompt 把它放在主力资金/成本框架内，代码没有禁止所有权、机构持仓或成本归因。
- AkShare `stock_zh_a_hist`/Sina/Tencent 历史路径在 `cn_akshare_provider.py:718-815` 使用 `adjust="qfq"`；Investoday/Fuyao 也明确返回前复权/forward；这不是 point-in-time 安全的价格基准。
- 新闻 provider 虽在 `cn_akshare_provider.py:1389-1467` 过滤可解析发布时间，但没有统一对外部标准公告/政策的发布时间、首次看到时间、新鲜度和来源哈希做证据级校验。
- manager verdict 没有“核心 claim 必须连续/独立证据确认”的状态机；`winner=tie` 仍可能被 Trader 的旧 HOLD/方向逻辑转成行动。

预期修复后的行为：高位放量滞涨只能输出“事件候选/风险信号”，不能输出“洗盘”或主力意图；VWMA 只能称为成交量加权价格描述；资金冲突只禁用资金方向证据，不得自动判空；未完成确认必须 `trade_action=WAIT`、`risk_status` 可独立为 elevated，禁止次日自动新建仓。

### 4.2 工业富联 2026-07-30

用户案例指出：技术/资金/量价三 Agent 的 60% 实际依赖同一价格冲击；大单流被人格化为机构/主力；极端杀跌被线性外推，缺 capitulation/reversal；漏抓 7 月 29 日微软/Meta 财报与 CSP CapEx；把 7 月 9 日业绩预告当未出清；使用 8 月 11 日后半年报；把 H1 累计营收 5578.61 亿元误作 Q2 单季；未来数据在多头处被驳回却在风控处使用；前复权污染；回购与单日大单比较；准入过严导致 V 反转永远买不到。

当前对应问题：

- 没有 claim→底层字段→source 的依赖图；三份报告对同一收盘价/跌幅的复述可形成三票。
- smart-money prompt 把分单资金主体命名为机构/主力，但大单金额本身不提供投资者身份。
- VPA 没有 regime transition 或 follow-through 结构；“卖出高潮”只是 `_compute_vpa_indicators()` 的标签，不是反转已确认。
- 新闻 analyst 默认短窗 14 天，新闻结果是文本；没有事件覆盖检查，不知道“微软/Meta 财报/CSP CapEx”这一类跨市场催化是否已被检索、漏检或已过时。
- `financial_announce.py` 能按公告日过滤，但财务表仍按报告期呈现，缺 `period_kind`；它不能阻止 LLM 把 `2026H1` 的累计值叫成 `2026Q2`。
- `backtest_service.py:155-183` 会在价格不足时把 hold_days 缩短；这与严格 T+5 相反。当前校准也没有统一 `price_basis`/as-of adjustment factor。
- 回购事件和资金流比较没有同一时间尺度、同一金额口径和执行节奏字段；不能用累计/日均回购直接与单日大单净流比较。

预期修复后的行为：7 月 9 日已公开的业绩预告作为“已知并可评估的事件”，8 月 11 日后数据在 7 月 30 日完全不可见；H1 只能展示为累计，Q2 单季只有在同口径 Q1 可验证时按 `H1-Q1` 派生；极端杀跌进入 `capitulation_candidate`，只有后续冻结到分析日的确认信号才提升为 `reversal_confirmed`；入场策略允许分层 `WAIT→小仓试探→确认加仓`，而不是永久无交易。

### 4.3 蓝思科技 2026-05-06

用户案例指出：7/7 上游 Agent 全部 502，系统仍编码成 Neutral/Hold；应有独立 `INVALID_RUN/DATA_ERROR/ABSTAIN`；零数据却生成 2%-3%、-8%-12% 数字；把内部中断映射成市场流动性风险；恢复后开仓条件又过严。

当前对应问题：

- analyst `_safe()`/stream fallback 把 provider/LLM 失败转成字符串或退化报告；没有“有效报告数/失败报告数/必需证据覆盖率”的顶层运行状态。
- manager 的资金流 guard 和 pre-gate 都安全地阻断交易，但返回 `direction=中性`；这在 UI、历史统计和 calibration 中与真实 Neutral 混淆。
- API legal decision 只有 BUY/SELL/HOLD；ReportDB 只有 completed/failed 等任务状态；所以 7/7 失败可以表现成完成的 HOLD。
- prompt 没有禁止在缺 benchmark、ATR、历史波动率、支撑位时编造百分比；report quality gate 不是确定性的数值来源检查。
- 结果里的风控理由仍可能把系统不可用解释成市场堰塞/流动性风险；风险模块不应从“数据不可用”推导市场风险。

预期修复后的结果：

```json
{
  "analysis_status": "INVALID_RUN",
  "failure_class": "DATA_ERROR",
  "direction": "N/A",
  "trade_action": "NO_TRADE",
  "risk_status": "UNKNOWN",
  "confidence": null,
  "probability": null,
  "numeric_ranges": [],
  "reason_codes": ["analyst_upstream_7_of_7_failed"]
}
```

恢复后也不应立即 BUY/SELL；应根据恢复后的最低证据门槛产生 `ABSTAIN` 或 `WAIT`，并标记这是恢复后重跑，不得把故障窗口当成中性样本。

### 4.4 蓝思科技 2026-05 至 6 月：业务换锚

用户案例指出，机器人、折叠屏、AI 服务器、AI 眼镜、光通信等连续增量催化带来估值重构。这个案例的价值不是要求系统预测涨幅，而是要求系统能够识别“业务结构/估值锚发生变化”，并区分：

- 单条新闻的短期价格刺激；
- 多个独立业务方向的连续催化剂 cluster；
- 公司披露的交付/产能/客户事实；
- 市场估值从单一手机玻璃到 AI 硬件制造平台的假设变化。

当前 news/industry context 主要把文本送进 LLM；没有结构化 `catalyst_id`、`first_seen_at`、重复新闻 cluster、业务 segment、直接影响、传导链、已定价状态。因此至少把 2026-06-12 与 2026-06-18 作为可选事件重放样本：样本必须用真实冻结新闻/公告快照；如果找不到可验证发布日期，就记录“缺证据”，不补写事实。

## 5. 按问题维度映射到模块、修改和验收

| 维度 | 当前模块/接口 | 当前问题 | 施工改动 | 依赖/风险 | 验收标准 |
|---|---|---|---|---|---|
| 数据层失败语义 | `data_collector.py` `_safe`、ledger、provenance；`analysts/*` fallback | 异常字符串可继续进入 LLM；没有 run-level 失败率 | 新增 `RunIntegrity`：required/available/failed/quality、failure_class、reason_codes；7/7 失败在 manager 前变 `INVALID_RUN` | API/DB schema 兼容；不能把单项非必需缺失升级为全局失败 | 7/7 502 不生成方向、概率、百分比；动作 `NO_TRADE`；报告不进入 calibration |
| point-in-time | provider CSV/文本；`source_provenance` | `as_of` 分散；cached_at/请求窗口/当前数据可能冒充有效日期 | 统一 `EvidenceRecord`：`source_family, field, value, unit, published_at, first_seen_at, snapshot_at, effective_as_of, requested_as_of, report_period, period_kind, adjustment_mode, source_hash, cluster_id` | 需要给旧文本适配；缺日期必须拒绝或标 unknown，不能 now 回填 | 任一证据 `effective_as_of > cutoff`、日期不可解析或缺必需时间时不得被 claim 采用 |
| 历史价格 | `cn_akshare_provider.py:718-815`、Investoday/Fuyao forward、`stockstats_utils.py:43-50` auto_adjust | qfq/forward/auto_adjust 的调整因子来自当前数据，可能把未来分红/拆股带回历史；缓存窗口以 today 命名 | PIT 默认使用 raw/unadjusted OHLCV；若使用 adjusted，必须保存 cutoff 时可知的 adjustment factor；报告/回测写 `price_basis` | 指标数值会变化；需要重建 golden fixtures，旧报告不改写 | 2026-05-28/07-30/05-06 fixture 中禁止未来调整因子；回测同一基准下 entry/exit 可重放 |
| 财务 period | `financial_announce.py`；`cn_akshare_provider.py:_financial_report_sina`；fundamentals analyst | 公告日已截断，但 H1 累计和 Q2 单季仍由文本/LLM理解 | 加 `period_kind`、`scope`、`reported_value`、`derived_value`、`derivation_formula`；Q2 仅在 H1/Q1 同口径可验证时 `H1-Q1` | 财务表来源字段不一致；不能把无法拆分的数据强行单季化 | H1 输入渲染为“累计”；没有 Q1 时 Q2 单季为 N/A，并附理由 |
| 新闻时间/新鲜度 | `cn_akshare_provider.py:get_news`；`news_analyst.py`；`interface.py` 历史 allowlist | 有发布时间过滤，但没有统一 first_seen、来源哈希、事件去重、覆盖率 | `NewsEvidence` + `EventCluster`；`published_at` 是内容时间，`first_seen_at` 只用于 archive；事件按实体/主题/时间 cluster；计算检索覆盖/缺失 | 不能把后补抓取新闻当历史已知；跨市场新闻需要允许列表 | 7/30 fixture 必须能证明 7/29 事件可见、8/11 数据不可见；缺时间新闻不进方向证据 |
| Agent 证据相关性 | `build_evidence_summary`、claims/debate、`analyst_traces` | 各 analyst 票数不等于独立证据；同一价格冲击可能三次计票 | claim 绑定底层 evidence IDs 和 `cluster_id`；评分按 cluster 去重，每 cluster 对方向最多一票；报告只作解释，不作额外票 | 旧 claim 没有 ID/来源时只能 `unsupported`；会降低旧结果可用性 | 工业富联模拟三份 price-derived report 只能形成一个 cluster，不得得到 60% 独立权重 |
| 资金流语义 | `fund_flow_evidence.py:1384`、`build_consensus_evidence`；`cn_akshare_provider.py:2389` | selector 被兼容命名为 consensus；EM/THS 字段不同；大单不等于机构 | 明确 `selection`、`same_field_consensus`、`incomparable_side_evidence`；净额只描述字段；主体身份缺证时不写机构/主力 | 不能把所有冲突都改成 bear/neutral；保持方向与风险解耦 | EM/THS 不同字段冲突时，资金方向 `direction_allowed=false`，但其他有效证据仍可裁决 |
| VWMA/主力成本 | `stockstats_utils.py`、smart-money prompt | 技术描述越界成所有权/成本区间 | prompt 和 schema 把 VWMA 定义为价格统计；新增 `ownership_inference=false` 默认；没有持仓数据不得输出主力成本 | 旧 golden 文本可能失败；需更新 prompt 测试 | VWMA 只能出“成交量加权价格/参考带”，不能出“主力成本区间/吸筹价” |
| confirmation/recency | `research_manager.py`、`evidence_verifier.py`、debate state | `tie` 不是待确认状态；近期文本可能压过更早已验证事实；无核心分歧清单硬闸 | `confirmation_state`：`confirmed/partially_confirmed/unresolved`; 对核心 claim 要求独立证据、时间合格、无致命冲突；recency 只在同等资格内使用 | 会阻断部分过去能给方向的报告；这是有意的安全变化 | Goertek 样本核心分歧未确认时 `WAIT/NO_TRADE`，不能次日自动开仓 |
| WAIT/ABSTAIN | prompts、verdict parser、API legal decisions | HOLD 同时表示真实中性、数据不足、冲突；没有 WAIT/NO_TRADE | 运行状态与动作四元拆分：`analysis_status`、`direction`、`trade_action`、`risk_status`；兼容层可保留旧 `decision`，但显示和统计以新字段为准 | DB migration/API/UI 联动；旧客户端需默认映射 | 真实 Neutral 可 `VALID+HOLD`；故障为 `INVALID+NO_TRADE`；未确认方向为 `ABSTAIN/WAIT` |
| 风险/方向解耦 | risk_manager、research_manager、ReportDB | 风险理由会反过来产生方向；数据不可用被写成市场风险 | 风险只输出风险状态、触发器、仓位上限；方向不可用时 risk 允许 `UNKNOWN/ELEVATED`，不产生 bull/bear | 旧交易文本兼容；要避免 trader 自行补方向 | 无行情数据时可有“执行风险未知”，但 direction 必须 N/A、不能输出跌幅区间 |
| 回测污染 | `backtest_service.py:155-302` | 文本 HOLD 吞掉失败；价格不足缩短 hold；复权基准不明；历史分析与未来 outcome 可能混写 | 回测记录保存 `analysis_status`、`decision_source`、`price_basis`、`entry_as_of`、`outcome_as_of`; 严格 T+N，不足即 unknown | 历史统计会变少；必须接受样本变少换取真实 | `INVALID/ABSTAIN/NO_TRADE` 不作为方向命中；未满 T+5 不计入 Brier/命中率 |
| 概率校准 | `calibration_service.py` | 只按 completed+probability 筛；未排除故障/WAIT；无分 regime/cluster | 仅 `analysis_status=VALID` 且 `trade_action` 有明确语义的样本进入；保存 forecast/outcome manifest；输出 Brier、reliability、ECE、样本门槛 | 不能拿旧 0 sample 或小样本宣布改进 | 每个统计结果含 eligible/excluded counts 和 exclusion reasons；N 不足不显示结论 |
| 社交接入 | `social/contracts.py`、archive/importer/entity；`social_media_analyst.py:38-71`、`trading_graph.py:245-268` | 当前 analyst 读 news/涨停池/雪球热股，social ToolNode 还绑定 `get_news`；新 provider/aggregator 不存在 | 按现有 plan 实施 archive→readonly provider→aggregator→social_data_context→analyst/manager/report；active 前 shadow/canary | 不能改旧数据/爬虫；不把 social 塞进 `_fetch_all` 线程池 | news/social sentinel 完全分离；disabled/shadow/active 行为和 Gate 0-4 测试一致 |

## 6. P0 施工任务：先阻断错误决策

### P0-1：建立运行状态和动作状态机

建议新增一个不依赖 LLM 的确定性层，例如 `tradingagents/agents/utils/run_integrity.py` 和 `decision_status.py`，不另起 `_v2` 平行路径。字段建议：

```text
analysis_status: VALID | PARTIAL | ABSTAIN | INVALID_RUN | DATA_ERROR
direction: BULL | BEAR | NEUTRAL | N/A
trade_action: BUY | SELL | HOLD | WAIT | NO_TRADE
risk_status: OK | ELEVATED | BLOCKED | UNKNOWN
confirmation_state: CONFIRMED | PARTIAL | UNRESOLVED
```

规则：

- `INVALID_RUN/DATA_ERROR`：direction=N/A、trade_action=NO_TRADE、probability/confidence/range 全部 null/空；不得生成市场流动性理由。
- `ABSTAIN`：数据足够部分可用，但核心方向证据不可裁决；trade_action=NO_TRADE 或 WAIT。
- `VALID+NEUTRAL+HOLD`：仅用于数据合格且真实分析后多空接近，不能用于异常。
- `WAIT` 是“等待确认的交易动作”，不是事实上的 Neutral；`NO_TRADE` 是执行风险动作，不代表看空。
- 风险层可以单独返回 `ELEVATED/BLOCKED/UNKNOWN`，不能修改 direction。

接线顺序：`_fetch_all` 产出完整性摘要 → analyst 只读摘要 → manager 前置确定性判断 → graph result → `api.main` → ReportDB/result JSON → backtest/calibration 排除非 eligible。不要先改 prompt 再依赖 LLM 自觉。

### P0-2：统一 EvidenceRecord 和未来数据防火墙

先定义最小可落地的结构化证据，不要求一次迁移所有历史文本。旧文本只能作为 `legacy_unverified`，不能作为高质量方向证据。必备字段：

```json
{
  "evidence_id": "...",
  "source_family": "eastmoney|ths|sina|cninfo|archive|provider",
  "field": "close|net_amount|revenue|news_event",
  "value": 0,
  "unit": "CNY|shares|percent",
  "requested_as_of": "YYYY-MM-DD",
  "effective_as_of": "YYYY-MM-DDTHH:MM:SS+08:00",
  "published_at": null,
  "first_seen_at": null,
  "snapshot_at": null,
  "report_period": null,
  "period_kind": null,
  "adjustment_mode": "raw|cutoff_adjusted|unknown",
  "cluster_id": "...",
  "provenance_status": "verified|unverified|refused|future|conflict"
}
```

所有采用函数必须做：解析失败拒绝、future 拒绝、单位/字段/期间校验、source hash/fixture manifest 记录。`cached_at`、ingest time、当前 API 返回时间不能自动成为有效 as-of。社交 archive 的 `published_at`、`first_seen_at`、`snapshot_at`、`ingest_at` 继续按 `docs/social_data/implementation_plan.md` 的 D-008 分层，`ingest_at` 永远不参与资格。

### P0-3：修复财务 period_kind

在 `financial_announce.py` 增加 period normalization，不破坏已经通过的有效公告日逻辑：

- `20260630` 的损益/现金流通常是 `half_year_cumulative`；资产负债表是期末点值，不能套累计语义。
- 只有 H1 与 Q1 具有相同合并范围、币种、单位、会计口径和可验证公告日时，才派生 `single_quarter_derived = H1 - Q1`。
- 无 Q1、范围不一致、单位不一致、表格只给同比百分比时，Q2 单季为 unknown，不得由 LLM 猜。
- 输出同时显示 `reported_period_label`、`period_kind`、`effective_announce_date`、`derivation_formula`。

### P0-4：资金流和 cluster 去重

将 `select_fund_flow_source()` 的结果命名为 `selection`；把 `build_consensus_evidence()` 的审计结果命名为 `same_field_consensus_audit`，不再把选择器兼容塞进 `consensus`。方向允许条件必须是“同字段、同日、同窗口、同单位、语义可比”的合格来源；EM 主力净额与 THS 总净额同时出现时只作 side evidence/conflict，不互相冒充。

每个 claim 记录 `evidence_ids` 和 `cluster_id`。技术、量价、资金若都只依赖同一收盘价冲击、成交量和当日价格变化，只计一个底层 cluster。claim 统计应同时报告：`analyst_count`、`independent_cluster_count`、`verified_evidence_count`，前者不得直接当权重。

### P0-5：去除 anthropomorphic 资金和 VWMA 语义

把 prompt 和输出 schema 改为可观察事实：

- “大单净流入/流出”只能是订单分组统计；未有席位/账户身份证据，不得称机构、主力、公募、外资。
- VWMA 只能是成交量加权价格统计；不得称主力成本、吸筹成本或筹码成本区。
- VPA 的“高位放量滞涨/卖出高潮”只能是 `event_candidate`，并说明构成字段；“洗盘/派发/反转”必须是待验证假设，不能是数据字段自带结论。

## 7. P1 施工任务：恢复研究可用性

### P1-1：事件检索和业务换锚

在 news/dataflow 增加结构化 `NewsEvidence/EventCluster`：实体、主题、published_at、first_seen_at、来源、source hash、直接影响、传导链、预计时滞、是否已经在 cutoff 前公开。对每个短期分析输出 `event_coverage`：已检索主题、命中数、不可验证数、疑似漏项，不把“没有命中”解释成“没有新闻”。

蓝思科技 5 月至 6 月样本应标注多业务 cluster，但必须依赖冻结公告/新闻。可以记录“机器人、折叠屏、AI 服务器、AI 眼镜、光通信等主题的连续增量催化”作为验收场景；没有实际 source snapshot 时，报告只能写待补证据。

### P1-2：capitulation/reversal 和分层入场

在 `_compute_vpa_indicators()` 增加确定性特征而非生成结论：成交量 z-score、振幅、收盘位置、换手/流动性（如有）、连续下跌、前后 follow-through。输出：

```text
normal | high_volume_stagnation_candidate | capitulation_candidate
reversal_unconfirmed | reversal_confirmed | insufficient_data
```

确认只能使用 cutoff 前已经发生的后续 bars；当日判断不能偷看 T+1。交易策略采用 `WAIT → confirmation → staged entry`：

- `capitulation_candidate`：只产生风险/观察，不买入。
- `reversal_confirmed` 且方向证据独立：允许小仓试探。
- 后续确认不足：维持 WAIT，不把“严格条件未满足”写成永久 NO_TRADE。

### P1-3：回测与校准隔离

修改 `backtest_service.py`：价格不足不缩短 hold_days；严格要求目标交易日数量；每条记录保留 `analysis_status`、`trade_action`、`price_basis`、`entry_price_as_of`、`exit_price_as_of`、`outcome_status`。`_classify_decision()` 不得把 `INVALID/ABSTAIN/WAIT/NO_TRADE` 统一转成 HOLD。

修改 `calibration_service.py`：

- 仅 `analysis_status=VALID` 且 probability 与 trade_action 语义匹配的样本进入 reliability/Brier。
- 单独统计 `excluded_invalid`、`excluded_abstain`、`excluded_no_trade`、`excluded_incomplete_outcome`。
- 保存 forecast anchor、基准收盘、价格基准、outcome source/as-of；不要用当前 qfq 重算旧基准。
- 输出样本量、Brier、reliability、ECE 以及按 regime/cluster/model 的分层结果；样本门槛未到时只显示 insufficient sample。

### P1-4：修复已重现的 provider 红灯

优先补最小回归，不扩展范围：

1. 历史财报：Sina 失败且备用表没有合格公告日时，必须拒绝 THS 当日摘要；不能因为备用表看起来有日期就默认满足历史资格。
2. 资金流 OOR：明确验证请求日期是否在数据覆盖范围内；不得返回其他日期并标成请求日。
3. 三股票八接口 smoke：先固定 provider payload/as-of fixture；真实网络 smoke 单独隔离，避免把供应商临时故障混成产品回归。

## 8. P2 施工任务：完成社交链

现有社交合同已经定义 `SentimentBundleV1` 的 status、`requested_as_of`、`cutoff_at`、`content_as_of`、`metric_as_of`、`direction_allowed`，archive schema 也明确 `published_at`、`first_seen_at`、`snapshot_at`、`ingest_at`。但实际目录缺少 provider/aggregator/classifier，不能宣布社交接入完成。

严格按已有 `docs/social_data/implementation_plan.md` Task 5–15：

1. `provider.py`：只读 archive、schema/lock/日期/future/coverage 防护；不得回写 archive。
2. `classifier.py`/`aggregator.py`：正文资格与互动指标资格分别判断；低覆盖时 status 为 partial/insufficient，不得补成 Neutral。
3. `SocialDataCollector`：在市场 `_fetch_all` 完成后独立短超时读取，不能加入原有市场线程池。
4. state/propagator/graph/API 三处挂载 `social_data_context`，ledger 及 `merge_data_gaps` 扫 social 状态。
5. 删除 social ToolNode 中的 `get_news`，news 与 social prompt 做 sentinel 隔离；当前 `social_media_analyst.py:38-71` 仍读 `news/zt_pool/hot_stocks`，active 后必须切断 legacy proxy。
6. disabled/shadow/active 严格分层；active 覆盖不足不得 fallback 到新闻；Gate 4 独立提交删除 `legacy_proxy` 后才算完成。
7. `credit_weighting_enabled` 仍保持 False，直到既有 H1b gate 输出 `ELIGIBLE_FOR_ACTIVATION`。

## 9. PIT 回归样本设计

三个必选样本都要采用离线冻结 fixture，不允许测试时访问当前实时接口。每个样本至少包含：raw payload、抓取/发布时间、请求 cutoff、source hash、字段语义、期末/单季标签、price_basis、expected result。

### R1 歌尔股份 `002241` / 2026-05-28

夹具内容：5 月 28 日前可见 OHLCV/成交量、VWMA 输入、EM/THS 资金两个不同语义字段、新闻/标准发布时间、至少一个未确认核心分歧。

断言：

- `VWMA` 仅为技术统计，输出不能出现主力成本/机构持仓断言。
- 高位放量滞涨为 `high_volume_stagnation_candidate`，没有独立确认不得命名洗盘。
- 资金流方向不可比时 `direction_allowed=false`，但不自动把整个市场方向改成 bear/neutral。
- 截止 5 月 28 日之后发布/首次可见的 AI 终端标准和新闻全部排除。
- `confirmation_state=UNRESOLVED` 时 `trade_action=WAIT`，执行层 `NO_TRADE`；不能生成次日自动开仓。
- 回测/统计记录不含未来复权因子。

### R2 工业富联 `601138` / 2026-07-30

夹具内容：7 月 30 日截止的行情、7 月 29 日可见的跨市场事件（若有可验证 snapshot）、7 月 9 日业绩预告、8 月 11 日以后半年报作为明确 future row、H1 累计字段、Q1 同口径字段（有/无两套 fixture）、回购期间/金额字段。

断言：

- 三个 price-derived analyst 只能贡献一个 independent cluster，不能机械显示 60% 独立证据。
- 大单只写分组净额，不写机构/主力身份。
- 7 月 29 日事件可见则进入 event coverage；8 月 11 日数据进入 `future` 且不得被任何多头、风控或报告字段使用。
- 7 月 9 日预告被识别为已公开事件，不得写“尚未出清”。
- H1 显示 `half_year_cumulative`；Q2 只有在同口径 Q1 存在时才 `single_quarter_derived`，否则 N/A。
- 资金/回购比较使用同一窗口和单位，不以单日大单替代累计执行证据。
- `capitulation_candidate` 可以转为 `WAIT`；有 cutoff 前确认 fixture 时可进入 staged entry，不能因为准入阈值一次未满足而永久 NO_TRADE。

### R3 蓝思科技 `300433` / 2026-05-06

夹具内容：七个 analyst/provider 返回 502 的 typed failure，且无任何有效价格、新闻、财务、资金 evidence。

断言：

- `failed_required_agents=7`、`analysis_status=INVALID_RUN` 或 `DATA_ERROR`。
- `direction=N/A`、`trade_action=NO_TRADE`、`risk_status=UNKNOWN/BLOCKED`；不输出 Neutral/Hold 作为观点。
- `confidence/probability/target/stop/upside/downside/numeric_ranges` 均为空或 null。
- 不出现 2%-3%、-8%-12% 等无证据数字；不把内部服务中断解释成流动性风险。
- 不进入 T+5 命中率、概率校准或历史 Neutral 统计。
- 恢复重跑必须经过最低 evidence gate；证据不足为 ABSTAIN/WAIT，不自动 BUY/SELL。

### 可选 R4/R5 蓝思科技 2026-06-12 / 2026-06-18

只有当新闻/公告原始 snapshot 和发布日期完整时才加入。断言重点是事件 cluster、业务 segment、直接影响/传导链、首次公开时间和已定价状态，而不是要求系统复制某个涨幅。

## 10. 测试和验收清单

### 10.1 单元和契约测试

- `test_run_integrity.py`：0/7、1/7、6/7、7/7 failure；可选源失败与必需源失败区分。
- `test_decision_status.py`：VALID Neutral、ABSTAIN、INVALID_RUN、DATA_ERROR、WAIT/NO_TRADE 组合合法性。
- `test_evidence_pit.py`：future、缺日期、非法日期、请求窗口和 cached_at 不能制造资格。
- `test_price_basis.py`：raw、cutoff_adjusted、unknown 三种模式；qfq/auto_adjust 不能在无 adjustment manifest 时进入历史证据。
- `test_financial_period_kind.py`：H1 累计、Q1、Q2 派生、范围/单位不一致拒绝。
- `test_news_event_coverage.py`：published_at/first_seen_at、重复新闻 cluster、漏项和不可解析时间。
- `test_fund_flow_semantics.py`：EM/THS 不同字段、同字段共识、legacy fallback、OOR。
- `test_evidence_cluster_weight.py`：同价冲击三报告只算一 cluster。
- prompt contract tests：禁止主力成本/机构身份/洗盘确定语句；允许事实候选和待验证。

### 10.2 端到端离线测试

- R1/R2/R3 三个样本通过后才允许进入 live smoke。
- API 返回同时检查 `analysis_status`、`direction`、`trade_action`、`risk_status`，不能只检查 `decision`。
- ReportDB 保存与 result JSON 相同的状态；旧客户端读取 `decision` 时有明确兼容映射，但统计读取新状态。
- backtest/calibration 对 invalid/abstain/no-trade 的排除计数和原因准确。
- 社交 disabled/shadow/active 与 news sentinel 隔离；archive 第二次导入相同 hash 不 UPDATE 旧快照。

### 10.3 全量和部署验收

1. 重新执行 `env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY .venv310/bin/python -m pytest tests --collect-only -q`，记录新的 collected 数，不接受旧的 2126 作为基线。
2. 先跑纯 fixture 定向集，必须 0 failure；再跑 provider smoke，供应商缺失只能形成明确 unavailable，不得污染产品回归。
3. `git diff --check`；确认 `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json` 和所有 untracked 用户文件不在施工提交边界。
4. 如需部署，另外验证目标 HEAD、`/healthz.commit_sha`、数据库/配置、端口/PID 和 focused tests；本次报告不授权部署、重启、数据库迁移或模型设置变更。

## 11. 建议的提交/依赖顺序

```text
P0-1 状态机 + run integrity
  → P0-2 EvidenceRecord / PIT firewall
    → P0-3 financial period_kind + provider red tests
      → P0-4 fund-flow semantics + cluster weighting
        → P0-5 prompt/schema 去人格化 + confirmation gate
          → P1-1 news event coverage
            → P1-2 capitulation/reversal + staged entry
              → P1-3 backtest/calibration isolation
                → P2 social Task 5–15 / Gate 0–4
```

建议每个 P0/P1 子任务独立提交并独立验收；`data_collector.py` 的时间/失败契约先于 analyst prompt，`evidence_verifier.py` 先于 manager prompt，API/DB 状态先于回测统计。不要把社交代码和数据/裁决修补混成一个 commit。

## 12. 交付边界和风险

- 不改辩论轮次、不打开信用加权、不覆盖用户模型绑定/密钥/3/1 设置。
- 不把所有资金冲突全局改成空或中性；只撤销冲突资金流的方向性证据资格。
- 不取消历史 snapshot refusal；不让实时/当前表补写历史。
- 不把失败的运行计入“中性表现”；这会改变已有历史统计，属于必要的数据清洗而不是模型准确率提升。
- schema migration 会影响旧 API/UI 和现有报告；应采用可选字段/兼容读取，再切换统计主键。
- 财务单季推导和新闻事件 cluster 会降低可用样本量；这是“有证据才显示”的可接受代价，必须在报告中公开 excluded counts。
- 目前没有证据证明这四个历史案例已经能在本地完整重放；在 fixture 未补齐前，验收结论只能是“设计可执行、历史重放待完成”，不能声称模型已修复。

## 13. 最终验收口径

只有同时满足以下条件，才可以把本轮称为完成：

1. R1/R2/R3 离线重放通过，且没有 future evidence 穿透任何方向或风控字段。
2. 蓝思 7/7 502 不再生成 Neutral/Hold 观点、不进校准、不生成伪数字。
3. Goertek 的高位放量滞涨、VWMA、资金分单冲突和未确认分歧分别保持各自语义。
4. Industrial FII 的 H1/Q2、7/9 预告、7/29 事件、8/11 future row 和 V 反转条件分别可审计。
5. 旧局部修补的 142 个纯离线核心测试保持通过；新 provider red tests 转绿；全量测试相对新基线无新增失败。
6. `analysis_status`、`trade_action`、`risk_status` 在 Graph、API、ReportDB、backtest、calibration 全链路一致。
7. 社交只在既有 Gate 通过后 active，且 `legacy_proxy` 已按独立 Gate 4 提交删除；在此之前不得宣布社交接入完成。

