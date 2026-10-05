# P1 历史结构研究报告（DAV-1478）

> **状态**：脚本与方法已按总控批准的 spec（`p1-study-spec.md`，提交 `1a680bab`）实现并经端到端验证；**全量样本计算由已提交脚本一条命令复现，本文件的汇总数字为方法验证用小样本（2024-06-01 ~ 2024-09-30，200 只样本股）的实测输出，全量区间数字待补**。
> 复现命令见文末。

## 0 口径与执行摘要

- **样本**：主口径 2019-06-03 起；示例区间见各表头（本文数字为方法验证用小样本）。
- **标签（预测口径，P2 裁定）**：T+1 开盘入场 → 信号日起第 40 个交易日收盘退出，`r_rel = r_stock − r_SW`，`y_rel = 1[r_rel > 0]`；`vendor_qfq` 前复权纯价格收益。
- **股票池**：全 A（`.SH`/`.SZ`，剔除 `.BJ`），信号日 PIT 剔除 ST/*ST（`namechange` 区间）、上市<60 交易日、T+1 不可执行（停牌/无量/一字涨停）。
- **行业归属**：`resolve_sw_l1_benchmark` 同口径（`index_member_all` Y/N 两批，`in_date <= T < out_date`），歧义记 missing，不静默补齐。
- **缺失类型化**：`pending_due / suspension / unexecutable_entry / data_missing / provider_failure`，逐条台账在仓外缓存。

## 1 同日成对相关（交付物 1）

估计单元：同一信号日、同组内两只股票 `r_rel` 的 z 标准化两两乘积均值，跨日聚合；**不从单日单股一对标量估相关**。组 = {全体, 同行业(l1_code), 同风格桶(规模×估值×动量×波动五分位向量), 同周}；同周为报告分层（配对单元仍同日）。

| 组 | mean ρ | 加权 ρ | 天数 | 中位对数 |
|---|---|---|---|---|
| 全体 | −0.0058 | −0.0058 | 42 | 14706 |
| 同行业 | −0.2218 | −0.1017 | 1134 | 10 |
| 同风格桶 | （见输出 JSON `paircorr.by_style_bucket`） | | | |
| 同周 | −0.0058 | −0.0058 | 42 | 14706 |

*小样本区间（42 信号日 × 200 股）数值仅示方法；全量数字由脚本产出。*

## 2 日级横截面统计量自相关结构（交付物 2）

零模型分数 = 公开因子合成（动量/估值/波动/规模 z 分等权），**非系统能力**；假设 IC 情景另列。

- 合成因子日级 Spearman IC：mean **0.237**、std 0.049（小样本区间；合成分数本身有真 IC 作方法演示）。
- ACF（lag 1–120）、Ljung–Box、Newey–West SE（带宽 {40,60,80,120}）、长程方差有效样本量 `n_eff` 均由 `stats.acf / ljung_box / newey_west_se / effective_n` 输出，保留原交易日索引（insufficient 日记缺失不补）。
- **HAC 带宽/移动块长不在实测前定死**：报告对 {40,60,80,120} 全列；推荐值按实测 ACF 衰减与覆盖率在全量结果中裁定。

## 3 风格暴露解释力（交付物 3）

- 每日横截面回归 `r_rel ~ z(规模)+z(估值)+z(动量)+z(波动)` 的 mean/median R²；风格中性化后残差 IC 与原始 IC 并列（`ic_series_neutralised`）；跨时段样本外 R²（前 2/3 估系数、后 1/3 预测）在输出 JSON `style` 段。

## 4 分行业历史基率（交付物 4）

- `p_base_rel_t40` 依据：分行业分年 `y_rel` 均值，**只用截至 T 已成熟标签**（退出日 ≤ T）；Beta–Binomial 向全市场收缩（m ∈ {50,200,500} 敏感性，主报 m=200）；缺失行业不填 50%。
- 明细在输出 JSON `base_rate_by_industry.by_industry_year`；报告只含汇总（全市场均值、分行业分年 p_hat / p_shrunk 表）。

## 5 分红偏差（总控限定项）

`vendor_qfq` 个股收益含分红调整，`sw_daily` 为不含分红的价格指数，`r_stock − r_SW` 在窗口含除权时系统性偏高。量化（小样本示例）：窗口内 `adj_factor` 发生变化的样本占比 **21.2%**；含除权样本 `r_rel` 均值 −0.034，不含者 +0.051。全量报告将给出该偏差对各项结论的影响；raw 口径与总收益口径作敏感性对照。

## 6 信息量—成本前沿（交付物 5）

对当时沪深300+中证500 子集做行业分层随机抽 N ∈ {10,20,30,40}，对照全市场随机。列：年新增有效信息量（`n_eff`，按实测相关结构折算，非"股票数×天数÷40"）、token、参数化费用、延迟、行业覆盖、排序精度。

### 成本模型（参数化，token 单位，非人民币）

| N | 双档 token/日（gemini 参考） | 单档 token/日（估，×0.5） | 延迟 min @并发4 |
|---|---|---|---|
| 10 | 13.0M | 6.5M | 62 |
| 20 | 26.0M | 13.0M | 125 |
| 30 | 39.0M | 19.5M | 188 |
| 40 | 52.0M | 26.0M | 250 |

- 单档占比 ∈ {0.35,0.5,0.65}×130 万为情景；130 万为 gemini 双档历史参考（PROJECT_STATE 09-27），不能直接当中线单档用量。
- 延迟参数：一份双档约 25 分钟、并发上限 4（PROJECT_STATE swe-2 试跑）；gemini 通道待实测。
- 费用需输入/输出/缓存三价，未编造人民币金额。

## 7 两项冻结口径（交付物 6）

- `primary_cross_sectional_metric`：建议**日级 Spearman 排序 IC 为主、高低分组差为辅**；并列值/常量分数/分组大小影响已在 `daily_ic` 输出 `frac_tied_score`、`n_score_nonnull` 字段，全量结果中列敏感性。
- `min_daily_cross_section_n`：**不推荐单一数值**；对 10/20/30/40 报有效天数覆盖、行业覆盖、IC 方差与分组差稳定性（输出 JSON `min_n_sensitivity`），由总控据此在 M2 结果暴露前冻结。

## 8 局限与缺失台账

- 行业归属缺失逐条记 `reason` 于仓外台账，不用其他来源静默补齐。
- 退市股保留；窗口未完成记 `pending_due`。
- `index_weight` 月度发布滞后：T 日取最近一期 `trade_date ≤ T` 名单，滞后天数分布列入局限。
- 删除退市/窗口未完成样本的方向性偏差按实测报分母与排除计数。
- `sw_daily` 早期年份若覆盖缺口，台账报告不外推。

## 9 复现（D-036）

```bash
# one command; raw market data cached off-repo at ~/Documents/TradingAgents-AShare-cache/phase2/
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
    scripts/phase2/p1_study.py --fetch --consolidate \
    --report docs/research/phase2-midterm/p1-report.md
# synthetic validation: --selftest ; offline re-plot: no --fetch reuses cache
```

缓存 `manifest.json` 记录每个数据文件的 api/参数/行数/sha256。报告只含汇总统计，不含行情原始数据行（D-040）。
