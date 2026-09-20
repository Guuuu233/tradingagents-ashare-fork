# DAV-810：财务源账本与 Fuyao 输出契约只读审查

审查日期：2026-09-11（Australia/Perth）

## 结论

已确认一个可复现的 source-ledger 契约缺陷：

1. Fuyao 是当前 fundamental_data 的首选源，且四个财务接口实际返回了包含数值表格的字符串；但输出没有携带可被采集器识别的、明确的实际数据日期。
2. 当前 data_collector 既不能把 Fuyao 表头中的请求截止日误当作实际数据日，这是正确的安全边界；同时又不能识别 Fuyao 表中的英文字段名与毫秒日期列。因此，含有真实财务数值的 Fuyao 返回值被记录为 status=unavailable、provenance_status=refused，形成“数据已返回但源账本拒绝”的假缺口。
3. 这不是“上游完全没有财务数据”的证据。无 Fuyao key 时，同一请求降级到 AkShare，返回了可解析的生效公告日 2026-08-15，并被记录为 available/verified；差异落在 Fuyao 输出格式与 provenance parser 契约。
4. 报告 6350b744 的最终边界是 ABSTAIN / NO_TRADE / BLOCKED，当前没有交易授权问题。但其基本面文字把 H1 累计现金流与资本开支写成 Q2 单季 FCF，且把总负债反复作为债务/融资压力证据使用；这些内容不能因报告最终不交易而视为已证实。

本文件是只读审查记录，不是代码修复、合并授权或部署授权。

## 审查边界与固定对象

- 任务：DAV-810，调查 data_collector.py 财务 source_provenance 被拒绝与实际返回值不一致的问题。
- 固定候选 checkout：/Users/davidliu/multica_workspaces_desktop-api.multica.ai/davidsworks-d70c6ff76b54/dav-810-7a8350d81522/workdir/tradingagents-ashare-fork
- 候选与服务当前 HEAD：4a5206f6e2d027dde8fb6da9bbbc1f38a7baca62
- 直接父提交：a630437c8e9b08ded0711a33572edc6c3beeb85d
- 服务 checkout：/private/tmp/ta-serve-trunk，HEAD 与上述候选相同；该 checkout 原有 uv.lock、work/h1b_gates_report.json 与 data 工作区变更均未触碰。
- 服务进程：PID 39573，工作目录 /private/tmp/ta-serve-trunk，监听 8000；只读 GET /api/health 返回 HTTP 200。
- 实际审查数据库：/private/tmp/ta-serve-trunk/data/tradingagents.db。服务路径与用户项目 data/tradingagents.db 为同一 inode 的解析结果。
- 代表性报告：6350b7449b8e4aeabec61914f6a3270b，600873.SH，分析日 2026-09-10。

本次未修改源代码、数据库、环境变量、服务进程、配置、任务看板或部署状态；没有执行合并、快进、重启或真实采集写入。服务重放只读取上游接口，Fuyao key 仅在进程内加载，未打印 key 值。

## 证据一：生产报告确实先命中 Fuyao

服务日志 /private/tmp/ta-serve-8000-20260911.log 记录了报告 6350b744 的四个财务调用：

- get_fundamentals 600873.SH / 2026-09-10：chain 的第一项为 cn_fuyao，随后 cn_fuyao status=hit。
- get_balance_sheet 600873.SH / quarterly / 2026-09-10：cn_fuyao status=hit。
- get_cashflow 600873.SH / quarterly / 2026-09-10：cn_fuyao status=hit。
- get_income_statement 600873.SH / quarterly / 2026-09-10：cn_fuyao status=hit。

对应日志区间为 03:34:58—03:34:59，报告完成日志为 03:38:38，报告 id 为 6350b7449b8e4aeabec61914f6a3270b。default_config.py:42-46 也明确把 fundamental_data 配置为 cn_fuyao,cn_akshare,cn_baostock,cn_investoday,yfinance。

因此，报告中的财务文字不能被简单解释为“Fuyao 没有命中”；日志证明它命中了首选源。数据库最终保存的是 provenance 和 failure ledger，不保存原始 Fuyao 财务表，所以“具体原始数值来自何源”的历史证据由服务日志和同 SHA 重放共同提供，而不是由 result_data 单独提供。

## 证据二：同 SHA 重放暴露了假缺口

在候选 checkout、Python 3.10、加载服务使用的 Fuyao 配置但不打印密钥的条件下，对 600873.SH / 2026-09-10 重放四个路由。每个返回值都是非空字符串，且包含同花顺 fuyao 标记；四个路由均满足以下结果：

| 路由 | 返回形状 | _extract_source_as_of | failure classification | financial field/value pair | provenance |
|---|---|---:|---:|---:|---|
| get_fundamentals | str，996 chars，含 report=2026-2 | None | None | False | unavailable / refused |
| get_balance_sheet | str，3546 chars，含 Fuyao 表格 | None | None | False | unavailable / refused |
| get_income_statement | str，3141 chars，含 Fuyao 表格 | None | None | False | unavailable / refused |
| get_cashflow | str，3314 chars，含 Fuyao 表格 | None | None | False | unavailable / refused |

以 get_cashflow 为例，当前实际输出头是：

    ## 现金流量表 (600873.SH) — 同花顺 fuyao /api/a-share/financials/cash-flow-statements（单季度，截至 2026-09-10）

Q2 表行同时包含以下原始字段和值：

- fiscal_year=2026，fiscal_period=Q2；
- report_date_ms=1786723200000，对应 Asia/Shanghai 日期 2026-08-15；
- period_end_ms=1782748800000，对应 2026-06-30；
- act_cash_flow_net=3.8914e+08；
- pay_fixed_assets_etc_cash=1.13464e+09。

这说明表格不是空返回，也不是显式 provider failure。问题在于：

- “截至 2026-09-10”是本次请求的 cutoff，不是源数据实际发布日期；当前 parser 没有把它当作实际日期，这是应保留的安全行为。
- Fuyao formatter 输出的是 report_date_ms / period_end_ms 等原始英文列，没有输出 as_of、actual_as_of 或生效公告日等结构化元数据。
- provider 内已有 _ms_to_date_str helper（cn_fuyao_provider.py:160-167），但财务 formatter（:418-468）没有使用它，也没有把转换后的日期写入返回值。
- data_collector.py:947-975 的日期模式没有 plain “截至 YYYY-MM-DD”这一模式，也没有 Fuyao 语义化字段处理或 report_date_ms / period_end_ms 转换。
- data_collector.py:1770-1785 的 _has_financial_field_value_pair 只识别一组中文财务字段及其相邻数值；Fuyao 的英文字段名位于 markdown 表头，数值在表格行中，当前规则返回 False。

因此 _build_source_provenance 的分支在 data_collector.py:1168-1225 中落到“无实际日期、也没有可识别财务字段/数值”的 unavailable/refused 分支，生成 gap：

    【数据获取失败】cashflow：未返回可验证数据日期

_fetch_all 在 data_collector.py:2105-2110 始终拉取四个财务接口；data_collector.py:2434-2457 把 source_provenance 的 gap 补入 data_failure_ledger；最终 data_collector.py:2503-2518 将两者写入 market_data_context。也就是说，假缺口会稳定地传播到报告边界。

### 对照组：无 Fuyao key 的降级链

仅在一个隔离进程中移除 Fuyao key、保持相同 ticker / cutoff，路由降级到 AkShare。cashflow 返回的头部包含：

    【财务数据截至 2026H1】（生效公告日 2026-08-15，分析日 2026-09-10；reported_period_label=2026H1, period_kind=half_year_cumulative, derivation_formula=not_derived；利润表/现金流量表半年度（0630）是1–6月累计值，禁止当作Q2单季使用（不是Q2单季））

同一次解析得到 actual_as_of=2026-08-15、status=available、provenance_status=verified。这个对照把问题隔离到 Fuyao 输出/解析契约，而不是“截至日期不存在”或“财务数据完全不可用”。

## 证据三：历史数据库与最近 30 条量化

### 报告 6350b744

数据库行和 result_data 的关键状态为：

- status=completed；
- analysis_status=ABSTAIN；
- risk_status=BLOCKED；
- trade_action=NO_TRADE；
- generated_by_commit_sha=4a5206f6e2d027dde8fb6da9bbbc1f38a7baca62；
- decision_status 的 hard gate 为 manager_consistency_hard_gate，并列出 INV-4、INV-6 的证据覆盖率 50.0% < 67%。

四个财务 source_provenance 均为：

- status=unavailable；
- provenance_status=refused；
- requested_as_of=2026-09-10；
- actual_as_of/as_of=null；
- gap_class=operational；
- gap=未返回可验证数据日期。

该报告的 data_failure_ledger 也有四个对应财务项，reason=unverified as-of。result_data 中没有 report_date_ms、period_end_ms、act_cash_flow_net、pay_fixed_assets_etc_cash 等原始 Fuyao 财务字段；因此不能从数据库原文反推出原始 provider 表格，只能确认最终账本状态。

### 最近 30 条 completed 报告

查询固定为：reports.status='completed'，按 created_at DESC，LIMIT 30。对每条报告读取顶层 market_data_context.source_provenance.cashflow、cashflow ledger，并把七个分析报告文本拼接后进行可复现的文本计数。结果如下：

- 30/30：cashflow status=unavailable，actual_as_of=null；
- 28/30：provenance_status=refused；另 2 条较早记录没有保存 provenance_status 字段；
- 30/30：存在 cashflow data_failure_ledger 条目；
- 28/30：文本中出现财务现金流/Capex/FCF 类标记，且标记附近 100 字符内有数字；
- 27/30：同时出现“经营现金流/CFO”数值和“资本开支/Capex/FCF”数值；
- 28/30：出现 Capex/FCF 类数值标记；
- 该 30 条记录跨多个历史生成 commit；6350b744 自身固定为上述 4a5206f SHA。

所以“27/30”只有在明确采用“同一报告文本内同时出现经营现金流数值与 Capex/FCF 数值，窗口半径 100 字符”这一严格口径时才成立。若问题是“有多少条报告含有现金流相关的数值文字”，可复现结果是 28/30；若问题是“多少条已被源账本验证”，是 0/30。三者不能混写。

## 证据四：下游影响

### 用户可见的数据缺口

api/services/report_service.py:1999-2050 的 merge_data_gaps 会消费 failure ledger 中 status 为 failed、timeout、unavailable、refused、error 的条目，并把显式 gap 合并到报告输出。6350b744 的 data_gaps 因此包含四个财务“未返回可验证数据日期”。

### 证据校验的可用性集合

tradingagents/agents/utils/evidence_verifier.py:482-550 会从 data_failure_ledger、source_provenance 和 data_gaps 收集 unavailable sources；status 为 unavailable/refused，或 provenance_status 为 unverified/refused/future，都会进入不可用集合。后续 source-unavailable 检查位于同文件 :552 起。

这会对提及这些 source 的证据声明施加拒绝/不可用约束，形成“文本中有财务数字、但证据层把财务源视为不可用”的不一致。6350b744 的 manager_verdict.source_unavailable_evidence 没有填充；该报告实际记录的 hard gate 是 INV-4/INV-6 的证据覆盖率问题，而不是财务 source_provenance 直接触发的交易阻断。不能把“报告最终 NO_TRADE”倒推为“账本问题没有影响”。

6350b744 的利用率也反映了这一边界：

- fundamentals_utilization=5/63=0.0794；
- seven_reports_utilization=48/274=0.1752；
- field_completeness 为 incomplete，confidence、probability、target_price、stop_loss_price 均缺失。

## 报告内容复核：可作为风险备忘录，不能作为已证实交易依据

报告文本对梅花生物 600873.SH 的主要风险方向是可读的，但以下问题需要在任何修复后重跑与复核中单独处理：

1. 报告在“2026Q2 单季”栏目中使用 CFO=3.89 亿元、资本开支=11.35 亿元并计算 FCF=-7.46 亿元。相同数值在报告其他段落又写成 2026H1；AkShare 对照输出明确说明 0630 现金流为 1—6 月累计值，不能直接当作 Q2 单季。没有 Q1 数值相减，Q2 单季 FCF 结论未被建立。
2. “总负债 111.15 亿元”是负债合计，不等于有息债务或债务融资余额；报告把它与 20 亿元债务融资工具申请并置，容易把会计负债、融资需求和债务余额混为一谈。
3. 原油/蒸汽/运输成本传导、成本占比、单位涨价、产能、PB 区间、政策时滞和 65/25/10 概率等多项内容没有在报告中给出足够的字段级来源或可复算弹性模型；新闻、基本面、宏观段落中的油价数值也存在不一致。
4. 报告缺少可逐项回指 provider/source 的 inline source id。结构化 source_provenance 不能替代每个财务数字的期间口径、公告日和字段证据。

因此，6350b744 应被理解为“在数据缺口和一致性 hard gate 下生成的偏空风险假设/研究备忘录”，其 ABSTAIN / NO_TRADE 是合规边界；不应把基本面段落中的 Q2 FCF 或因果判断当作已经验证的交易信号。

## 建议修复范围（未执行）

只建议在以下最小范围内修复，未对这些文件做改动：

- tradingagents/dataflows/providers/cn_fuyao_provider.py
- tradingagents/graph/data_collector.py
- tests/test_cn_fuyao_provider.py
- tests/test_data_collector.py
- tests/test_financial_as_of.py

建议契约：

1. Fuyao 财务返回值必须携带非歧义的 source actual_as_of / report date 元数据，优先由 report_date_ms 转为 YYYY-MM-DD；不能把请求头中的“截至 curr_date”直接当成 actual_as_of。
2. 结构化返回应同时保留报告期间字段，例如 period_end_ms / fiscal_period，并明确其是报告期结束日还是生效公告/可见日期；两者不得混用。
3. 若仍使用纯文本兼容层，必须输出机器可解析的字段，例如 source_as_of=2026-08-15 或生效公告日=2026-08-15，并保留原始表格；不能只输出“同花顺 fuyao + 截至请求日”。
4. parser 应识别 Fuyao 表中允许的英文财务字段及数值行，但不能仅因为出现 report_date_ms 或请求截止日就把数据判为已验证。没有实际日期但有真实财务字段/数值时，至少应落到既有的 available_unverified_as_of，而不是 unavailable/refused。
5. period_kind 必须在财务 provider 或下游 derivation 层明确；对现金流/利润表 0630 半年度累计值，禁止自动生成 Q2 单季 FCF。若要生成 Q2 单季，必须有同口径 Q1 数据和可审计的相减公式。

建议验收测试：

- mock Fuyao cashflow/balance/income/fundamentals 返回值，覆盖 Q2/H1 行、report_date_ms、period_end_ms、英文财务字段与数值；
- 四个真实路由形状经 provenance builder 后得到 actual_as_of=2026-08-15、status=available、provenance_status=verified，且不产生“未返回可验证数据日期”；
- 只有“截至 2026-09-10”的请求 cutoff、没有 source actual date 时，不得被解析成 2026-09-10 的 verified 数据；
- report_date_ms 晚于 requested_as_of 时必须 fail closed 为 future/不可用；
- no-key AkShare fallback 的 2026H1 / half_year_cumulative / Q2 禁止误用约束保持；
- report_service.merge_data_gaps 和 evidence_verifier 对真实失败、无日期但有数值、已验证日期三种状态分别保持现有语义；
- 修复后重跑本文件列出的两组回归测试，并增加一条从 Fuyao output 贯通到 source_provenance 的集成形状测试。

## 测试基线

在固定候选 checkout、Python 3.10、env -u PYTHONPATH 下执行：

- tests/test_data_collector.py
- tests/test_financial_as_of.py
- tests/test_cn_fuyao_provider.py
- tests/test_financial_announce_cutoff.py
- tests/test_financial_period_kind.py

结果：164 passed，3 deselected，4.97s。

另执行：

- tests/test_report_data_gaps.py
- tests/test_evidence_verifier_fairness.py
- tests/test_research_manager_run_integrity.py
- tests/test_research_manager_claim_evidence_coverage_gate.py

结果：55 passed，0.88s。

这些是修复前基线，不是修复完成证据。现有通过项覆盖通用 as-of、AkShare/模拟 provider、失败账本、报告缺口合并和 evidence verifier 行为；没有覆盖当前 live-shaped Fuyao 财务 markdown 进入 _extract_source_as_of、_has_financial_field_value_pair 和 _build_source_provenance 的完整链路。

## 最终状态边界

- DAV-810 调查：已完成只读证据收集与根因定位。
- 代码修复：未执行。
- 数据库修复或回写：未执行。
- 看板状态/评论/分配：未修改。
- 合并、快进、部署、服务重启：未执行。
- “允许合入”或“允许部署”：本文件不产生任何授权。
