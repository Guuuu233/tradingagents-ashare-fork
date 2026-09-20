# 报告质量审计：6350b744（600873.SH，分析日 2026-09-10，生成版本 4a5206f）

来源：Codex 内容复核（2026-09-11，David 转交）；Claude 对库内 `reports.result_data` 只读核对，并用同一代码与分析日复现了基本面分析师的财报输入。未修改报告、代码、数据库或部署状态。

> 更正记录（2026-09-11）：本文先后有三处错误，已改正。①初版称「空方混用了单季资本开支与累计经营现金流」，实为基本面分析师写错期间，Codex 原描述正确；②初版称「期间语义模块零调用、未接线」，实为已接入 akshare 新浪链路（只在 akshare 取数时生效）；③第二版称「模型输入含半年度累计提示和 Q2 派生块，模型无视提示」，依据是未加载 `FUYAO_API_KEY`、降级到 akshare 的复现。DAV-810 核实生产运行的财报首选命中 Fuyao，其表头写死「单季度」，模型输入里没有累计提示。

## 结论

- 风控层面合格：`analysis_status=ABSTAIN`、`trade_action=NO_TRADE`、`risk_status=BLOCKED`，仓位 0。触发原因是研究总监自洽硬闸（部分采纳了证据覆盖率 50% < 67% 的 INV-4、INV-6）。
- 研究内容只能作为偏空风险备忘录，不能作为可执行建议，也不应标记为高质量研究报告。

## 逐条核对

| # | Codex 指出的问题 | 核对结果 | 出错层 | 证据位置 |
|---|---|---|---|---|
| 1 | H1 口径被算成「Q2 单季 FCF −7.46 亿」 | **成立**：生产运行的现金流输入来自 Fuyao（服务日志 `vendor=cn_fuyao status=hit`，见 DAV-810），表头为「现金流量表……（单季度，截至 2026-09-10）」；`fiscal_period=Q2` 行的 `act_cash_flow_net=3.8914e+08`、`pay_fixed_assets_etc_cash=1.13464e+09` 与新浪 H1 累计值逐项相同，即报告期累计值被标成单季度。基本面分析师据此把 11.35 亿写成「2026Q2 单季」，并把它与 Q1 相加当作「H1 累计 16.57 亿」（11.35 + 5.23；2025 年 10.87 + 5.53 = 16.40）；空方据此算出「单季 FCF −7.46 亿」 | 数据层 Fuyao 表头期间错标（主因）；缺少生成后期间校验 | `fundamentals_report`；`investment_debate_state.history`；`investment_debate_state.challenges[3].weakest_point`；`work/2026-09-11-dav810-source-ledger-audit.md` |
| 2 | 总负债被写成债务 / 有息债务 | **部分成立**：基本面报告「总负债 111.15 亿（资产负债率 41.49%）」用法正确；空方把发债解释为「高额负债周转」，把总负债当作待续借债务。库内文本未出现「有息负债 / 有息债务」，「反复」一说不成立 | 研究员论证语义 | `fundamentals_report`；`investment_debate_state.history` |
| 3 | 融资公告被过度解释 | **成立**：输入原文为「审议通过……议案，拟向交易商协会申请注册发行总额度不超过 20 亿元」；辩论改写为「公司注册发行 20 亿元债务融资工具，本质是应对现金流失血」 | 研究员综合（输入正确） | `market_data_context.event_coverage`；`investment_debate_state.history` |
| 4 | 因果与量化判断无证据链 | **成立**：(a) 情景概率 65% / 25% / 10% 出现在市场、主力资金、量价、新闻报告，宏观报告另给 60% / 20%，互不一致且未经校准；(b)「回升 200 元/吨 → 单季毛利增厚 1.5–2.0 亿」；(c)「PB 1.45 倍处于历史底部 10% 分位」；(d) 新闻报告称工业蒸汽采购价上涨约 5–8%，并标为「确定性：高（已发生事实的成本端映射）」 | 各分析师与研究员 | `news_report`、`market_report`、`smart_money_report`、`volume_price_report`、`macro_report`、`investment_debate_state.history` |
| 5 | 同一油价数据不一致 | **成立，源头在数据层**：新闻证据为布伦特 106.144 美元（+4.88%），行情表 `major_assets` 为 107.80（+5.96%）；正文混用「突破 107 美元」「105 美元附近」 | 数据层两源未统一时点与合约，分析师混用 | `market_data_context.event_coverage.clusters[41]`、`[46]`；`market_data_context.major_assets` |

## 系统侧发现了什么

- 已发现：claim 核验把 INV-4（技术破位）、INV-6（原油侵蚀毛利）判为覆盖率 50%，自洽硬闸据此阻断交易。
- 未发现：上表 5 类问题都出现在分析师报告与辩论叙述中；现有 claim 核验不检查期间口径、公告所处阶段、假设与事实的区分，也不检查跨来源数值是否一致。

## 代码根因（trunk `4a5206f`）

- 生产取数走 Fuyao：`tradingagents/default_config.py` 中 `fundamental_data` 首选 `cn_fuyao`，服务日志记录本报告财报调用 `vendor=cn_fuyao status=hit`。`tradingagents/dataflows/providers/cn_fuyao_provider.py:464-467` 按请求频率写死表头「单季度」，不看数据口径，而 Fuyao Q2 行是报告期累计值。这是本报告期间错误的主因。
- akshare 链路的期间提示只在降级时生效：`tradingagents/dataflows/providers/cn_akshare_provider.py` 在新浪财报链路调用 `financial_cutoff_header` 与 `derive_q2_from_h1_q1` / `format_q2_derivation_block`；本报告的实际输入没有经过这条链路。
- akshare 降级链路的 Q2 派生误判（DAV-809 P-1 候选 `f0d97dac6f2ee64fc8816feaad56e77adab5b822` 修复中）：`tradingagents/dataflows/financial_announce.py` 的 `SCOPE_COMPARISON_COL_KEYWORDS` 含「单位」，按子串匹配时命中金额科目「处置子公司及其他营业单位收到的现金净额」；600873.SH 该科目 H1 有值、Q1 为空，现金流 Q2 派生被判 `scope_mismatch`。
- 生成后没有期间校验：数据口径错标或模型写错期间时，没有确定性检查发现或纠正。
- 核验层：`tradingagents/agents/utils/evidence_verifier.py` 的 `normalize_period` 能识别「2026Q2」「H1」等期间，但 `_METRIC_CANONICAL_MAP` 把「单季营收」映射为「营收」，期间只在少数矛盾判断中使用。
- 来源台账误报（另一关注点）：DAV-810 已查明。Fuyao 财报输出不带可解析的实际数据日期，且为英文字段，`tradingagents/graph/data_collector.py` 识别不了，四项财报被记为 unavailable / refused；详见 `work/2026-09-11-dav810-source-ledger-audit.md`。

## 对应工作包

- #1：先 DAV-812（Fuyao 财报输出契约，修复生产主因），再 DAV-809 P-2（基本面期间合规校验）；DAV-809 P-1 修复 AkShare 降级链路的 Q2 派生误判（Codex 复审通过，2026-09-11 已合入主干）。
- #2、#3、#4：E-03b/c（命题假设不得升级为事实）与 E-04（公告阶段与预期修正分栏）。
- #4 中的情景概率：属于 L3 概率校准问题，未经校准的场景概率不应以精确数字呈现。
- #5：宏观行情统一口径（单一来源、时点、合约），计划中无对应工作包。
- 来源台账误报：DAV-810 调查已完成（`work/2026-09-11-dav810-source-ledger-audit.md`），修复见 DAV-812。
- 建议把本报告固定为回归样本（参照 `tests/golden/audit_20260823/`），作为上述各卡的红队 fixture。
