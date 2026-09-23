# 文献证据台账

日期：2026-09-23。对应《TradingAgents 首个组织经验实验：文献与统计优化评议》。

本台账记录定向检索后的相关段落核读，不把“取得全文”表述为“逐字通读全文”。所有对本项目的收益预测仍为待验证假设。

## R1 · ER-20260923-finmem-01

- 来源：Yu et al. **FinMem: A Performance-Enhanced LLM Trading Agent with Layered Memory and Character Design**. 2023, arXiv v2. [全文](https://arxiv.org/html/2311.13743v2)。核读记忆设计与历史实验；限于其数据、模型与回测条件。
- 证据类型：preprint。
- 核读范围：§3 记忆设计、历史交易实验。
- 支持：分层表示和经验调用具有实现路径。
- 限制与禁止强化：架构启发；收益大小不可移植。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R2 · ER-20260923-fincon-02

- 来源：Yu et al. **FinCon: A Synthesized LLM Multi-Agent System with Conceptual Verbal Reinforcement for Enhanced Financial Decision Making**. 2024, arXiv v3. [全文](https://arxiv.org/html/2407.06567v3)。核读 §4.1–4.3；训练 2022-01 至 2022-10，测试至 2023-06；历史结果不是前瞻随机实验。
- 证据类型：preprint。
- 核读范围：§4.1–4.3 实验、重复运行及消融。
- 支持：经理/分析师与信念更新可作为后续研究对象。
- 限制与禁止强化：回测时期与 A 股前瞻研究不同。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R3 · ER-20260923-tradingagents-03

- 来源：Xiao, Sun, Luo, Wang. **TradingAgents: Multi-Agents LLM Financial Trading Framework**. 2024，2025-06 v7. [条目与版本](https://arxiv.org/abs/2412.20138)；[全文](https://arxiv.org/html/2412.20138v7)。核读 §5–6；作者信息按 arXiv 书目页核对。
- 证据类型：preprint。
- 核读范围：§5–6；arXiv 书目页。
- 支持：保留真实角色底座；明确历史评价范围。
- 限制与禁止强化：日级输入过滤不是参数无前视的证明。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R4 · ER-20260923-evomemory-04

- 来源：Wei et al. **Evo-Memory: Benchmarking LLM Agent Test-time Learning with Self-Evolving Memory**. 2025，2026-05 v2. [全文](https://arxiv.org/html/2511.20857v2)。核读 §3–4、局限与风险；数学、问答和交互环境，非金融收益验证。
- 证据类型：preprint。
- 核读范围：§3–4；Appendix C/I。
- 支持：区分经验检索、使用、更新及跨任务适应。
- 限制与禁止强化：非金融任务；反馈正确性比市场收益更清晰。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R5 · ER-20260923-memoryarena-05

- 来源：He et al. **Benchmarking Agent Memory in Interdependent Multi-Session Agentic Tasks**（MemoryArena）. 2026-02, v1. [全文](https://arxiv.org/html/2602.16313v1)。核读任务构造、评价及 §4.3；适用于记忆使用与基准设计的启发。
- 证据类型：preprint。
- 核读范围：§3–4.3；结论。
- 支持：记忆召回与后续行动能力应分别评价。
- 限制与禁止强化：非金融；外部记忆不是普遍有利。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R6 · ER-20260923-evomembench-06

- 来源：Wang et al. **EvoMemBench: Benchmarking Agent Memory from a Self-Evolving Perspective**. 2026-06, v2. [全文](https://arxiv.org/html/2605.18421v2)。核读 §3、§5；比较记忆作用范围、内容、成本及强基准，未验证本实验的市场增益。
- 证据类型：preprint。
- 核读范围：§3、§5。
- 支持：区分记忆作用范围、内容、迁移与成本。
- 限制与禁止强化：保持强原始上下文基准；不能外推收益。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R7 · ER-20260923-stale-07

- 来源：Chao et al. **STALE: Can LLM Agents Know When Their Memories Are No Longer Valid?** 2026-05, v1. [全文](https://arxiv.org/html/2605.06527v1)。核读 §4.2、结论及 Appendix A；400 个专家核验的合成冲突情景，不能当作真实金融分布。
- 证据类型：preprint。
- 核读范围：§4.2、§6、Appendix A。
- 支持：失效识别与实际策略修正应分开检查。
- 限制与禁止强化：合成个人状态冲突，不是市场状态变迁实测。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R8 · ER-20260923-agemem-08

- 来源：Yi Yu et al. **Agentic Memory: Learning Unified Long-Term and Short-Term Memory Management for Large Language Model Agents**. 2026-07, v3. [全文](https://arxiv.org/html/2601.01885v3)。核读训练与 §4.1；属于训练记忆操作策略的方法，首轮只作为后续方向。
- 证据类型：preprint。
- 核读范围：记忆操作/训练设计、§4.1、结论。
- 支持：后续可将记忆操作视为学习对象。
- 限制与禁止强化：涉及强化学习训练，本轮不能作为即插即用增益。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R9 · ER-20260923-forecastbench-09

- 来源：Karger et al. **ForecastBench: A Dynamic Benchmark of AI Forecasting Capabilities**. 2024，2025-02 v5. [全文](https://arxiv.org/html/2409.19839v5)。核读问题生成、预测与结算流程；本文仅借鉴前瞻封存和概率评价。
- 证据类型：preprint。
- 核读范围：§3 问题库、预测、结算。
- 支持：前瞻封存与真实标签结算可借鉴。
- 限制与禁止强化：未结算题代理评分不移入本协议。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R10 · ER-20260923-lap-10

- 来源：Gao, Jiang, Yan. **Detecting Lookahead Bias in LLM Forecasts**. 2026-06, v2；原题 *A Test of Lookahead Bias in LLM Forecasts*. [全文](https://arxiv.org/html/2512.23847v2)。核读 §2–3 的 LAP 与计量模型；依赖模型与测量假设，不是无泄漏认证。
- 证据类型：preprint。
- 核读范围：§2–3 LAP 定义和计量框架。
- 支持：参数污染值得诊断。
- 限制与禁止强化：检验依赖模型假设和接口，不认证零泄漏。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R11 · ER-20260923-proper-11

- 来源：Gneiting, Raftery. **Strictly Proper Scoring Rules, Prediction, and Estimation**. JASA, 2007. DOI: 10.1198/016214506000001437. [作者公开全文](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf)。支持概率评价原则；具体金融目标、缺失和相关性仍需另外定义。
- 证据类型：full paper。
- 核读范围：定义、适当评分理论、二次评分相关论述。
- 支持：使用 Brier 评价明确二元事件概率。
- 限制与禁止强化：不自动解决相关性、缺失或收益意义。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R12 · ER-20260923-diebold-12

- 来源：Diebold, Mariano. **Comparing Predictive Accuracy**. Journal of Business & Economic Statistics, 1995, 13(3):253–263. [大学公开全文](https://economia.uc3m.es/jgonzalo/teaching/PhdTimeSeries/DieboldMariano.pdf)。已核看原文 pp.253–254 的损失差、长期方差与假设；不将其单时间序列检验机械用于公司事件面板。
- 证据类型：full paper。
- 核读范围：原文 pp.253–254 已视觉核读。
- 支持：以配对损失差及长期方差比較预测。
- 限制与禁止强化：时序假设需验证；不是事件面板的现成检验。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R13 · ER-20260923-cluster-13

- 来源：Cameron, Miller. **A Practitioner's Guide to Cluster-Robust Inference**. Journal of Human Resources, 2015, 50(2):317–372. [作者公开稿](https://cameron.econ.ucdavis.edu/research/Cameron_Miller_JHR_2015_February.pdf)。核读聚类、少簇与多维聚类；“簇数足够”的判断有条件，不能固定一个普适数字。
- 证据类型：full paper / author manuscript。
- 核读范围：聚类基本理论、少簇、多维聚类。
- 支持：独立行标准误可能明显过度自信。
- 限制与禁止强化：少簇无统一安全门槛；交叉ID不等于多维聚类。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R14 · ER-20260923-corp-14

- 来源：Dimitriadis, Gneiting, Jordan. **Evaluating probabilistic classifiers: Reliability diagrams and score decompositions revisited**. 2020, arXiv v1. [本次读取全文](https://arxiv.org/html/2008.03033v1)。相关发表版本为 *Stable reliability diagrams for probabilistic classifiers*, PNAS 2021，DOI: [10.1073/pnas.2016191118](https://doi.org/10.1073/pnas.2016191118)。采用的是已读预印本的方法与独立性限制，不声称逐字核对最终版本。
- 证据类型：preprint; published metadata verified。
- 核读范围：§3–6，特别是 §4 限制。
- 支持：避免任意分箱解释校准；区间需有假设。
- 限制与禁止强化：独立/可交换条件不能直接套金融重叠事件。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R15 · ER-20260923-pead-15

- 来源：Lan, Xie, Mi, Zhang. **Post earnings announcement drift: A simple earnings surprise measure, the medium effect of investor attention and investing strategy**. International Review of Financial Analysis, 2024, 95. [发表元数据](https://ideas.repec.org/a/eee/finana/v95y2024ipbs1057521924003922.html)；[本次读取的较早预印本](https://www.projectnoir.eu/code/Post_Earnings_Announcement_Drift.pdf)。预印本研究中国 A 股 2011–2023、60 交易日漂移及隔夜反应，不能直接支持年度首次预告 T+10 效果；未将预印本数值认定为最终发表版逐项一致。
- 证据类型：preprint; published metadata verified。
- 核读范围：数据、ORJ/隔夜反应、60日目标。
- 支持：预期差和市场响应有金融研究动机。
- 限制与禁止强化：较早全文与2024发表版未逐字核对；不同目标期限。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R16 · ER-20260923-llmpead-16

- 来源：Hadlock, Roberts, Lee. **Enhancing Post Earnings Announcement Drift Measurement with Large Language Models**. FinNLP / EMNLP workshop, 2025. [论文](https://aclanthology.org/2025.finnlp-2.13.pdf)。核读 §5 与 Limitations；原文承认 10-Q 与业绩公告时点不一致、未计交易成本；用作时间契约警示及研究动机。
- 证据类型：full paper。
- 核读范围：§5.2–5.5、Limitations。
- 支持：公告、文本实际可用、市场反应时点要区分。
- 限制与禁止强化：10-Q 与公告非同时；收益未计真实交易摩擦。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R17 · ER-20260923-lookaheadbench-17

- 来源：Benhenda. **Look-Ahead-Bench: a Standardized Benchmark of Look-ahead Bias in Point-in-Time LLMs for Finance**. 2026-01, v1. [全文](https://arxiv.org/html/2601.13770v1)。核读 §3–5；范围小、跨期变化存在混杂，本文仅作批判性参考。
- 证据类型：preprint。
- 核读范围：§3–5。
- 支持：跨期衰减可提出诊断问题。
- 限制与禁止强化：五只股票/两时段；不能唯一归因参数污染。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## R18 · ER-20260923-glasserman-18

- 来源：Glasserman, Lin. **Assessing Look-Ahead Bias in Stock Return Predictions Generated By GPT Sentiment Analysis**. 2023-09, v1. [全文](https://arxiv.org/html/2309.17322v1)。核读数据方法及 §4；匿名化也会改变干扰效应，所以匿名化对照不是纯粹的泄漏检验。
- 证据类型：preprint。
- 核读范围：§2 方法及 §4 讨论。
- 支持：匿名化同时影响记忆与干扰。
- 限制与禁止强化：不能把匿名化效果当纯泄漏效应。
- 本文采用强度：支持研究设计或诊断动机，不支持本项目已经有效的结论。

## 未作为证据使用的材料

- 搜索引擎自动摘要只用于发现候选论文，未用于背书效果数字。
- PMC 的校准论文页面遇到验证页，改读该研究的 arXiv 全文并保留版本边界。
- NBER 预测比较页面本次返回的摘要与标题不一致，未使用该摘要；改用大学公开的 1995 年原文扫描件核看方法。
- TradingAgents HTML 开头存在异常作者文本；作者依据 arXiv 书目页核验，不照抄异常文本。
- 18项来源都未提供本实验 A 股、年度首次预告、固定 T+10 目标的直接前瞻效果证据。
