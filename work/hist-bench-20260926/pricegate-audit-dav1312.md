# DAV-1312 价格门拦截精确率审计（2026-09-26）

语料：`work/hist-bench-20260926/audit-corpus-pricegate.txt`（sha256 591b5cde…），46 档 = 日常 6 + 历史回放 40，共 282 条 violation。
脚本：`scripts/dav1312_pricegate_audit.py`（只读 result_data，可复跑）；1309 对照价获取 `work/dav1312/fetch_prices.py`。

## 汇总（按档位：去掉全部 (b)(c) 类违规后不再被拦 = 误拦档位）

| 组 | 档位 | 误拦档位 | 非误拦 | 误拦率 | violations a/b/c/d |
|---|---|---|---|---|---|
| 日常 daily | 6 | 5 | 1 | 83.3% | 2 / 23 / 0 / 0 |
| 历史回放 batch | 40 | 37 | 3 | 92.5% | 6 / 233 / 4 / 14 |
| 合计 | 46 | 42 | 4 | 91.3% | 8 / 256 / 4 / 14 |

非误拦档位：
- 日常 `[1] 603156.SH`：(a) 47.50 元历史套牢区价位无口径可证，正确拦截（同档另有 (b) 误报）。
- 历史 `[36] 603288.SH`：(d) 39.60/37.00 元为真实大宗成交价 raw 标注正确，与前复权现价并列未标『不可直接比较』，按契约 §3 确属违规（非抽取误报；该股 07-22 后无除权非 1309）。
- 历史 `[43] 600519.SH`：(a) 1616 元投行目标价转述无口径可证（同档含 4 条 (c) 1309 类）。
- 历史 `[45] 000333.SZ`：(a) 77.64/81.50 元冲突价位模型自述不可核验，正确拦截。

(b) 误报主因（256/282 = 90.8%）：百分数截断（±x%→x.0）、非价数字（PE/PB 倍数、仓位%、均线周期、日期片段、论据编号、年份、差额）、非本股价（煤价/丙二醇/原油/节税额/分红额）、typed_disclosure provenance 误标、文档已声明前复权未传递、derived_estimate 漏标、真实价位漏登记。

## DAV-1308 / DAV-1309 可消除量

- **DAV-1308**：本语料 46 档的 violation 中**没有一条**由 as_of>分析日直接引起；唯一命中是 `[5] pr-060` as_of 被记成解禁日 2026-10-08，但其违规由 pit_raw 误标驱动，修 as_of 不消除。**预计消除误拦档位 0、违规 0**。
- **DAV-1309**：仅 `[43]` 档 pr-113（1218.61 真实大宗折价价=当时不复权收盘 1315×(1-7.33%)）4 条 violation 属 (c)；该档仍含 (a) 违规。**预计消除误拦档位 0、违规 4 条**（另 `[32] pr-117` 增发定价为 1309 相邻项，但该违规由 pr-116 误标主导，计 (b)）。

## 优先级建议

- 两卡**均不必提优先级**（就门精确率而言）：合计可消除误拦档位 0。DAV-1309 仍有独立价值（历史报告价位与当时盘面一致性），只是对拦截误报几乎无贡献。
- 误拦的绝对主因是 price_ref 提取器精度（(b) 类 90.8%），建议另开卡：①百分数/倍数/日期/编号/仓位等非价数字不得登记 price_ref；②文档级『一律前复权』声明向句内价位传播 basis；③typed_disclosure provenance 误挂；④估值推演句归 derived_estimate；⑤坐标语境内真实价位漏登记回补。

## 逐条表

|#|report|symbol|trade_date|horizon|grp|kind|field|类|子类|说明|
|-|-|-|-|-|-|-|-|-|-|-|-|
|0|de6cbbc1|000657.SZ|2026-09-24|short_term|daily|decision_driving_unspecified_basis|investment_plan|b|derived_estimate_miss|50.0 来自 MANAGER_VERDICT excluded_evidence『压力测试测算…底部估值42-50元』，是带测算口径的估值推演值，应归 derived_estimate；且被拒证据本不该算决策驱动|
|0|de6cbbc1|000657.SZ|2026-09-24|short_term|daily|decision_driving_missing_as_of|investment_plan|b|derived_estimate_miss|50.0 来自 MANAGER_VERDICT excluded_evidence『压力测试测算…底部估值42-50元』，是带测算口径的估值推演值，应归 derived_estimate；且被拒证据本不该算决策驱动|
|1|bdbf6c56|603156.SH|2026-09-24|short_term|daily|decision_driving_unspecified_basis|news_report|a|model_price_no_basis|8月24日套牢区 45.50-47.50 元为模型自述的历史价位断言，即便补上日期仍无 basis 可归（归为 raw 又会落入坐标混用），门按契约拦下正确|
|1|bdbf6c56|603156.SH|2026-09-24|short_term|daily|decision_driving_missing_as_of|news_report|a|model_price_no_basis|8月24日套牢区 45.50-47.50 元为模型自述的历史价位断言，即便补上日期仍无 basis 可归（归为 raw 又会落入坐标混用），门按契约拦下正确|
|1|bdbf6c56|603156.SH|2026-09-24|short_term|daily|decision_driving_unspecified_basis|news_report|b|non_price_dividend_per_share|『每10股派5元』每股股利被当成股价登记|
|1|bdbf6c56|603156.SH|2026-09-24|short_term|daily|decision_driving_missing_as_of|news_report|b|non_price_dividend_per_share|『每10股派5元』每股股利被当成股价登记|
|2|f4f0cac6|601398.SH|2026-09-24|short_term|daily|decision_driving_unspecified_basis|fundamentals_report|b|derived_estimate_miss|7.23 为 0.65x PB 估值推演值，应归 derived_estimate 而非决策驱动价格|
|2|f4f0cac6|601398.SH|2026-09-24|short_term|daily|decision_driving_missing_as_of|fundamentals_report|b|derived_estimate_miss|7.23 为 0.65x PB 估值推演值，应归 derived_estimate 而非决策驱动价格|
|3|d35e2c70|601398.SH|2026-09-24|short_term|daily|unbacked_executable_level|trader_investment_plan|b|level_unbacked_executable_level|『工行前复权股价跌破8.00…回踩200日线（7.45元）』同句有前复权口径，真实价位漏登记|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|decision_driving_missing_as_of|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|4|1db525e8|601398.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|non_stock_price+mislabel|『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|目标价 52.00 元；final_trade_decision 明文『第二止盈位：前复权 52.00 元』，声明口径未传递到 ref|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|目标价 52.00 元；final_trade_decision 明文『第二止盈位：前复权 52.00 元』，声明口径未传递到 ref|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|mislabel_provenance+date_1308|『按当前48.03元股价折算』是当时现价，被 private_placement provenance 误标 pit_raw，且 as_of 被记成解禁日 2026-10-08（>分析日，1308 同类日期误读）|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|mislabel_provenance+date_1308|『按当前48.03元股价折算』是当时现价，被 private_placement provenance 误标 pit_raw，且 as_of 被记成解禁日 2026-10-08（>分析日，1308 同类日期误读）|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|mislabel_provenance+date_1308|『按当前48.03元股价折算』是当时现价，被 private_placement provenance 误标 pit_raw，且 as_of 被记成解禁日 2026-10-08（>分析日，1308 同类日期误读）|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|mislabel_provenance+date_1308|『按当前48.03元股价折算』是当时现价，被 private_placement provenance 误标 pit_raw，且 as_of 被记成解禁日 2026-10-08（>分析日，1308 同类日期误读）|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|mislabel_provenance+date_1308|『按当前48.03元股价折算』是当时现价，被 private_placement provenance 误标 pit_raw，且 as_of 被记成解禁日 2026-10-08（>分析日，1308 同类日期误读）|
|5|2f821b1c|601088.SH|2026-09-24|medium_term|daily|cross_basis_coordinate_mix|news_report|b|mislabel_provenance+date_1308|『按当前48.03元股价折算』是当时现价，被 private_placement provenance 误标 pit_raw，且 as_of 被记成解禁日 2026-10-08（>分析日，1308 同类日期误读）|
|6|1312d27a|600036.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|non_price_bare_number|MANAGER_VERDICT excluded_evidence 中的孤立数字『7』被当成价格 7.0|
|6|1312d27a|600036.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_bare_number|MANAGER_VERDICT excluded_evidence 中的孤立数字『7』被当成价格 7.0|
|6|1312d27a|600036.SH|2026-07-08|medium_term|batch|executable_level_wrong_basis|investment_plan|b|level_executable_level_wrong_basis|excluded_evidence 中孤立数字 7 被当成可执行价位|
|7|a747371c|600276.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|-6.5% 跌幅百分数被截成价格 6.0|
|7|a747371c|600276.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|-6.5% 跌幅百分数被截成价格 6.0|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|fundamentals_report|b|non_price_year|2026Q1 年份被当成价格 2026.0|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|fundamentals_report|b|non_price_year|2026Q1 年份被当成价格 2026.0|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|149.50-150.50 入场价，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|149.50-150.50 入场价，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|147.50 元，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|147.50 元，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|150.50 元，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|150.50 元，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|non_price_position_pct|仓位 12.0% 被当成价格|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|non_price_position_pct|仓位 12.0% 被当成价格|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|non_price_position_pct|仓位 4.0% 被当成价格|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|non_price_position_pct|仓位 4.0% 被当成价格|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|164.00 止盈价，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|164.00 止盈价，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|目标价 164.00 元，文档声明一律前复权|
|8|84c5dfc8|688981.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|目标价 164.00 元，文档声明一律前复权|
|9|e50b30f3|600030.SH|2026-07-08|short_term|batch|decision_driving_unspecified_basis|fundamentals_report|b|non_price_pb|1.05-1.15 倍 PB 被当成价格|
|9|e50b30f3|600030.SH|2026-07-08|short_term|batch|decision_driving_missing_as_of|fundamentals_report|b|non_price_pb|1.05-1.15 倍 PB 被当成价格|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|+5.7% 涨幅被截成价格 5.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|-4.3% 跌幅被截成价格 4.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|-4.3% 跌幅被截成价格 4.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|-4.3% 跌幅被截成价格 4.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|-4.3% 跌幅被截成价格 4.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|-4.3% 跌幅被截成价格 4.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|pct_trunc|-4.3% 跌幅被截成价格 4.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|pct_trunc|-4.3% 跌幅被截成价格 4.0|
|10|d93a03ef|002594.SZ|2026-07-08|medium_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『准入仓位0%』仓位数被当成可执行价位|
|11|f48dd119|600900.SH|2026-07-08|short_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『回撤1.5倍日内ATR』倍数被当成可执行价位|
|11|f48dd119|600900.SH|2026-07-08|short_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『盘口5档』档数被当成可执行价位|
|12|f48dd119|600900.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|non_price_fraction|减仓 1/3 的分数被当成价格 1.0|
|12|f48dd119|600900.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|non_price_fraction|减仓 1/3 的分数被当成价格 1.0|
|12|f48dd119|600900.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|non_price_fraction|减仓 1/3 的分数被当成价格 1.0|
|12|f48dd119|600900.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|non_price_fraction|减仓 1/3 的分数被当成价格 1.0|
|12|f48dd119|600900.SH|2026-07-08|medium_term|batch|executable_level_wrong_basis|trader_investment_plan|b|level_executable_level_wrong_basis|『减仓1/3』分数被当成可执行价位|
|12|f48dd119|600900.SH|2026-07-08|medium_term|batch|executable_level_wrong_basis|final_trade_decision|b|level_executable_level_wrong_basis|『减仓1/3』分数被当成可执行价位|
|13|426bab77|001979.SZ|2026-07-08|short_term|batch|decision_driving_unspecified_basis|investment_plan|b|non_price_claim_id|论据编号 INV-4 被当成价格 4.0|
|13|426bab77|001979.SZ|2026-07-08|short_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_claim_id|论据编号 INV-4 被当成价格 4.0|
|13|426bab77|001979.SZ|2026-07-08|short_term|batch|unbacked_executable_level|investment_plan|b|level_unbacked_executable_level|position_pct=0 被当成可执行价位|
|13|426bab77|001979.SZ|2026-07-08|short_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『前5个交易日』日数被当成可执行价位|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|decision_driving_unspecified_basis|news_report|b|pct_trunc|股息率 4.5% 被截成价格 4.0|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|decision_driving_missing_as_of|news_report|b|pct_trunc|股息率 4.5% 被截成价格 4.0|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标|
|14|827ba5c1|600036.SH|2026-06-10|short_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『超过8个交易日』日数被当成可执行价位|
|15|fe47cd30|300750.SZ|2026-06-10|short_term|batch|unbacked_executable_level|trader_investment_plan|b|level_unbacked_executable_level|『回踩370.00元区间』（200日线370.07附近）为真实价位；文档声明全部坐标前复权，漏登记|
|15|fe47cd30|300750.SZ|2026-06-10|short_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『反抽401.00-404.36元受阻』为真实价位；文档声明全部坐标前复权，漏登记|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+18.8% 涨幅被截成价格 18.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+18.8% 涨幅被截成价格 18.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+16.2% 涨幅被截成价格 16.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+16.2% 涨幅被截成价格 16.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+16.2% 涨幅被截成价格 16.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+16.2% 涨幅被截成价格 16.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|16|fe47cd30|300750.SZ|2026-06-10|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|pct_trunc|-5.7% 跌幅被截成价格 5.0|
|17|aa6d45a5|600276.SH|2026-06-10|short_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|+3.2% 涨幅被截成价格 3.0|
|17|aa6d45a5|600276.SH|2026-06-10|short_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|+3.2% 涨幅被截成价格 3.0|
|17|aa6d45a5|600276.SH|2026-06-10|short_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|+3.2% 涨幅被截成价格 3.0|
|17|aa6d45a5|600276.SH|2026-06-10|short_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|+3.2% 涨幅被截成价格 3.0|
|17|aa6d45a5|600276.SH|2026-06-10|short_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|non_price_position_pct|委托量 10% 被当成价格|
|17|aa6d45a5|600276.SH|2026-06-10|short_term|batch|decision_driving_missing_as_of|final_trade_decision|b|non_price_position_pct|委托量 10% 被当成价格|
|18|b0cdfe45|000333.SZ|2026-06-10|medium_term|batch|unbacked_executable_level|trader_investment_plan|b|level_unbacked_executable_level|『约14倍PE』估值倍数被当成可执行价位|
|19|21e993fd|001979.SZ|2026-06-10|short_term|batch|cross_basis_coordinate_mix|fundamentals_report|b|mislabel_provenance|LPR 3.00% 指标句被挂 private_placement provenance 并登记为 pit_raw 价格 1.0|
|19|21e993fd|001979.SZ|2026-06-10|short_term|batch|cross_basis_coordinate_mix|fundamentals_report|b|mislabel_provenance|LPR 3.00% 指标句被挂 private_placement provenance 并登记为 pit_raw 价格 1.0|
|20|e8ef1647|600519.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+3.5%~+5.7% 涨幅被截成价格 3.0|
|20|e8ef1647|600519.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+3.5%~+5.7% 涨幅被截成价格 3.0|
|20|e8ef1647|600519.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|-8.0%~-9.6% 回撤被截成价格 8.0|
|20|e8ef1647|600519.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|-8.0%~-9.6% 回撤被截成价格 8.0|
|21|fd45ac18|688981.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+11.2% 涨幅被截成价格 11.0|
|21|fd45ac18|688981.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+11.2% 涨幅被截成价格 11.0|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|44.40 挂单带，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|44.40 挂单带，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|44.40 挂单带，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|44.40 挂单带，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|44.40 挂单带，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|44.40 挂单带，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|44.10 挂单价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|44.10 挂单价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|43.95 挂单价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|43.95 挂单价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|declared_basis_miss|44.40 门槛价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|declared_basis_miss|44.40 门槛价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|44.40 挂单带，同句声明前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|44.40 挂单带，同句声明前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|43.90-44.40 区间，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|43.90-44.40 区间，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|44.15 建仓中轴，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|44.15 建仓中轴，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|non_price_diff|0.65 元为止损敞口差额，非价位|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|non_price_diff|0.65 元为止损敞口差额，非价位|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|44.40 挂单带，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|44.40 挂单带，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|44.15 挂单价，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|44.15 挂单价，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|43.95 挂单价，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|43.95 挂单价，同句标注前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|46.00 保本止盈触发价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|46.00 保本止盈触发价，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|44.40 挂单带，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|44.40 挂单带，文档声明一律前复权|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|unbacked_executable_level|trader_investment_plan|b|level_unbacked_executable_level|『止盈位下调至46.00-46.50元』为真实价位；文档声明全部坐标前复权，漏登记|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|executable_level_wrong_basis|final_trade_decision|b|level_executable_level_wrong_basis|0.65 元为止损敞口差额，非价位|
|22|b03c6a97|601088.SH|2026-07-22|medium_term|batch|executable_level_wrong_basis|final_trade_decision|b|level_executable_level_wrong_basis|46.00 保本触发价，文档声明一律前复权|
|23|9477ba36|601899.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|fundamentals_report|b|non_price_amount|『市值底部支撑区间 3,840~4,480 亿元』中 3,840 被截成价格 3.0|
|23|9477ba36|601899.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|fundamentals_report|b|non_price_amount|『市值底部支撑区间 3,840~4,480 亿元』中 3,840 被截成价格 3.0|
|24|3150f7d0|002594.SZ|2026-07-22|medium_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『收紧至94.00元（前复权）』同句有口径，漏登记|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|decision_driving_unspecified_basis|investment_plan|b|non_price_ma_period|『以 10 EMA 动态生命线为刚性止损点』的均线周期数 10 被当成价格|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_ma_period|『以 10 EMA 动态生命线为刚性止损点』的均线周期数 10 被当成价格|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|6.78 为 10EMA 真实均线值，被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|6.78 为 10EMA 真实均线值，被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|6.78 为 10EMA 真实均线值，被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|6.78 为 10EMA 真实均线值，被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|6.78 为 10EMA 真实均线值，被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|6.78 为 10EMA 真实均线值，被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|non_price_ma_period+mislabel|『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance|
|25|dfc4662b|001979.SZ|2026-07-22|short_term|batch|executable_level_wrong_basis|investment_plan|b|level_executable_level_wrong_basis|『10 EMA』周期数被当成可执行价位|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|macro_report|b|non_stock_price|『现货煤价中枢向1000元/吨』商品价格，非本股价|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|macro_report|b|non_stock_price|『现货煤价中枢向1000元/吨』商品价格，非本股价|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|-6.1% 回撤被截成价格 6.0|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|-6.1% 回撤被截成价格 6.0|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|declared_basis_miss|45.70 入场触发区间均值，final_trade_decision 声明一律前复权|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|declared_basis_miss|45.70 入场触发区间均值，final_trade_decision 声明一律前复权|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|executable_level_wrong_basis|final_trade_decision|b|level_executable_level_wrong_basis|45.70 入场均值，文档声明一律前复权|
|26|acbb32d0|601088.SH|2026-06-10|medium_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『盘口5档』档数被当成可执行价位|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|27|127108b6|603288.SH|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|news_report|b|mislabel_provenance|35.00 元模型压力位被误挂 block_trade provenance 标为 raw（同违规其余 ref：b/mislabel_provenance）|
|28|f088d66a|000063.SZ|2026-07-08|short_term|batch|unbacked_executable_level|final_trade_decision|b|level_unbacked_executable_level|『布伦特原油突破85美元/桶』商品价格，非本股价|
|29|d4489983|600760.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+20.6% 涨幅被截成价格 20.0|
|29|d4489983|600760.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+20.6% 涨幅被截成价格 20.0|
|29|d4489983|600760.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|折价1.3%/溢价0.2% 被截成价格 1.0|
|29|d4489983|600760.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|折价1.3%/溢价0.2% 被截成价格 1.0|
|30|0c1d9966|601012.SH|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|fundamentals_report|b|non_price_year|2026Q1 年份被当成价格 2026.0|
|30|0c1d9966|601012.SH|2026-07-08|medium_term|batch|decision_driving_missing_as_of|fundamentals_report|b|non_price_year|2026Q1 年份被当成价格 2026.0|
|31|eb610daf|600309.SH|2026-07-08|short_term|batch|decision_driving_unspecified_basis|investment_plan|b|non_price_claim_id|论据编号 INV-7 被当成价格 7.0|
|31|eb610daf|600309.SH|2026-07-08|short_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_claim_id|论据编号 INV-7 被当成价格 7.0|
|32|ad72af61|002027.SZ|2026-07-08|medium_term|batch|decision_driving_unspecified_basis|fundamentals_report|b|derived_estimate_miss|5.60-6.20 元为 18-20 倍 PE 折合估值推演，应归 derived_estimate|
|32|ad72af61|002027.SZ|2026-07-08|medium_term|batch|decision_driving_missing_as_of|fundamentals_report|b|derived_estimate_miss|5.60-6.20 元为 18-20 倍 PE 折合估值推演，应归 derived_estimate|
|32|ad72af61|002027.SZ|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|fundamentals_report|b|mislabel_provenance|『当前股价4.67元』=前复权值，被误挂 private_placement provenance 标为 pit_raw（同违规其余 ref：b/mislabel_provenance）|
|32|ad72af61|002027.SZ|2026-07-08|medium_term|batch|cross_basis_coordinate_mix|fundamentals_report|b|mislabel_provenance|『当前股价4.67元』=前复权值，被误挂 private_placement provenance 标为 pit_raw（同违规其余 ref：b/mislabel_provenance）|
|33|7afcc2d6|600760.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|non_price_position_pct|『3%+3%+4%』建仓预算百分数被当成价格 3.0|
|33|7afcc2d6|600760.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|non_price_position_pct|『3%+3%+4%』建仓预算百分数被当成价格 3.0|
|33|7afcc2d6|600760.SH|2026-06-10|medium_term|batch|executable_level_wrong_basis|final_trade_decision|b|level_executable_level_wrong_basis|『3%+3%+4%』建仓预算百分数被当成可执行价位|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|pct_trunc|+3.0% 被截成价格 3.0|
|34|07480d15|601012.SH|2026-06-10|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|pct_trunc|+3.0% 被截成价格 3.0|
|35|239d8af8|600309.SH|2026-06-10|short_term|batch|cross_basis_coordinate_mix|news_report|b|non_stock_price|丙二醇 9766.67 元/吨商品报价被登记为 raw 价格|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|36|3fc1beba|603288.SH|2026-07-22|short_term|batch|cross_basis_coordinate_mix|news_report|d|genuine_raw_unlabeled_dual|39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）（同违规其余 ref：d/genuine_raw_unlabeled_dual）|
|37|3fc1beba|603288.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+8.4% 涨幅被截成价格 8.0|
|37|3fc1beba|603288.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+8.4% 涨幅被截成价格 8.0|
|37|3fc1beba|603288.SH|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+16.6% 涨幅被截成价格 16.0|
|37|3fc1beba|603288.SH|2026-07-22|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+16.6% 涨幅被截成价格 16.0|
|38|73100827|000063.SZ|2026-07-22|short_term|batch|decision_driving_unspecified_basis|investment_plan|b|non_price_ma_period|SMA50 均线周期数被当成价格 50.0|
|38|73100827|000063.SZ|2026-07-22|short_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_ma_period|SMA50 均线周期数被当成价格 50.0|
|38|73100827|000063.SZ|2026-07-22|short_term|batch|executable_level_wrong_basis|investment_plan|b|level_executable_level_wrong_basis|SMA50 周期数被当成可执行价位|
|39|73100827|000063.SZ|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|non_price_date_frag|日期片段 07-22 被当成价格 7.0|
|39|73100827|000063.SZ|2026-07-22|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_date_frag|日期片段 07-22 被当成价格 7.0|
|39|73100827|000063.SZ|2026-07-22|medium_term|batch|unbacked_executable_level|trader_investment_plan|b|level_unbacked_executable_level|『跨越1个交易日』日数被当成可执行价位|
|40|8c028552|600309.SH|2026-07-22|short_term|batch|unbacked_executable_level|trader_investment_plan|b|level_unbacked_executable_level|『止盈目标下调至73.80元』为真实价位；文档声明全部坐标前复权，漏登记|
|41|360d917b|002027.SZ|2026-07-22|medium_term|batch|decision_driving_unspecified_basis|final_trade_decision|b|non_price_ma_period|10日EMA/10.5亿被当成价格 10.0|
|41|360d917b|002027.SZ|2026-07-22|medium_term|batch|decision_driving_missing_as_of|final_trade_decision|b|non_price_ma_period|10日EMA/10.5亿被当成价格 10.0|
|42|3f0e273e|600036.SH|2026-05-20|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|non_price_pb|0.75x PB 被当成价格|
|42|3f0e273e|600036.SH|2026-05-20|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|non_price_pb|0.75x PB 被当成价格|
|42|3f0e273e|600036.SH|2026-05-20|medium_term|batch|executable_level_wrong_basis|investment_plan|b|level_executable_level_wrong_basis|0.75x PB 估值倍数被当成可执行价位|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|decision_driving_unspecified_basis|macro_report|a|model_price_no_basis|『向上修复至1616元附近』系转述投行目标价，无登记口径/日期可证，正确拦截|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|decision_driving_missing_as_of|macro_report|a|model_price_no_basis|『向上修复至1616元附近』系转述投行目标价，无登记口径/日期可证，正确拦截|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|decision_driving_missing_as_of|investment_plan|b|mislabel_provenance|1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance 标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|news_report|c|genuine_pit_raw_price|1218.61 元为真实大宗折价成交价（=当时不复权收盘 1315×(1-7.33%)），raw 标注正确；违规只因报告坐标是事后前复权(qfq 1284.6)，1309 修复后消失|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|news_report|c|genuine_pit_raw_price|1218.61 元为真实大宗折价成交价（=当时不复权收盘 1315×(1-7.33%)），raw 标注正确；违规只因报告坐标是事后前复权(qfq 1284.6)，1309 修复后消失|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|news_report|c|genuine_pit_raw_price|1218.61 元为真实大宗折价成交价（=当时不复权收盘 1315×(1-7.33%)），raw 标注正确；违规只因报告坐标是事后前复权(qfq 1284.6)，1309 修复后消失|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|news_report|c|genuine_pit_raw_price|1218.61 元为真实大宗折价成交价（=当时不复权收盘 1315×(1-7.33%)），raw 标注正确；违规只因报告坐标是事后前复权(qfq 1284.6)，1309 修复后消失|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|mislabel_provenance|1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance 标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|mislabel_provenance|1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance 标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|mislabel_provenance|1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance 标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|mislabel_provenance|1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance 标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|mislabel_provenance|1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance 标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|cross_basis_coordinate_mix|investment_plan|b|mislabel_provenance|1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance 标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）|
|43|fdc9352b|600519.SH|2026-05-20|short_term|batch|unbacked_executable_level|investment_plan|b|level_unbacked_executable_level|『TTM PE跌至19.46倍』估值倍数被当成可执行价位|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|+21.2% 涨幅被截成价格 21.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|+21.2% 涨幅被截成价格 21.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_unspecified_basis|investment_plan|b|pct_trunc|-5.4% 跌幅被截成价格 5.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_missing_as_of|investment_plan|b|pct_trunc|-5.4% 跌幅被截成价格 5.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|+21.2% 涨幅被截成价格 21.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|+21.2% 涨幅被截成价格 21.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_unspecified_basis|trader_investment_plan|b|pct_trunc|-5.4% 跌幅被截成价格 5.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|decision_driving_missing_as_of|trader_investment_plan|b|pct_trunc|-5.4% 跌幅被截成价格 5.0|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|unbacked_executable_level|investment_plan|b|level_unbacked_executable_level|『42倍PE』估值倍数被当成可执行价位|
|44|78fe0ecb|600276.SH|2026-05-20|medium_term|batch|unbacked_executable_level|trader_investment_plan|b|level_unbacked_executable_level|『42倍PE』估值倍数被当成可执行价位|
|45|e29fd742|000333.SZ|2026-05-20|short_term|batch|decision_driving_unspecified_basis|final_trade_decision|a|conflicting_unverifiable|模型自述材料中 77.64 与 81.50 元冲突且复权规则缺失，价位不可核验，正确拦截|
|45|e29fd742|000333.SZ|2026-05-20|short_term|batch|decision_driving_missing_as_of|final_trade_decision|a|conflicting_unverifiable|模型自述材料中 77.64 与 81.50 元冲突且复权规则缺失，价位不可核验，正确拦截|
|45|e29fd742|000333.SZ|2026-05-20|short_term|batch|decision_driving_unspecified_basis|final_trade_decision|a|conflicting_unverifiable|同 pr-209，同一冲突价位的第二次登记，正确拦截|
|45|e29fd742|000333.SZ|2026-05-20|short_term|batch|decision_driving_missing_as_of|final_trade_decision|a|conflicting_unverifiable|同 pr-209，同一冲突价位的第二次登记，正确拦截|
