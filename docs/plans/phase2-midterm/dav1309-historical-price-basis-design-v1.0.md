# DAV-1309 设计文档：历史回放价格口径切换到「分析日当时口径」v1.0

| 项 | 内容 |
|---|---|
| 任务 | DAV-1309（规格卡，低优，历史回放专用） |
| 状态 | 设计稿，待总控审；**不修代码、不回写历史报告、不改实时路径** |
| 起草 | 资深开发1，2026-10-15 |
| 核对基线 | `codex/dav-4-p2a-trunk` tip `ddec52f9bc95688bd228110721b5d297610abfd8`（`git ls-remote` 实测） |
| 实施排期 | 按卡面登记：**实施排到第二阶段回测之前**；本卡只交设计 |
| 关联 | DAV-1312（前复权偏移误拦量审计）、DAV-703/D-01（raw 日线通道契约）、DAV-715（分红旁证）、DAV-1142/1198/1199（price_ref 注册表与价格门）、F1 协议 v1.0.1 §3（阶段 H 要求"事件当时的复权价格"） |

## 0. 卡面登记依据（照录，写文档依据）

> 每日批为当日实时分析、当日取得的前复权价即当时口径；同窗口收益比值不受复权基准影响；T+5 回填已自做锚定——**测量期读数不受影响**；**实施排到第二阶段回测之前**。

展开：

1. **每日批（实时分析）不受影响**：当日跑分析时 vendor_qfq 就是"当时口径"——前复权的漂移只发生在除权事件发生之后，当日除权事件已含在当日因子内。因此切换范围必须且只需覆盖 `is_historical_analysis_date(trade_date) == True` 的路径。
2. **同窗口收益比值不受复权基准影响**：`(exit − entry) / entry` 在同一复权坐标系内对公共缩放因子不变。方向打分、H1b 读数不随口径切换改变。
3. **T+5 回填已自做锚定**：`scripts/backfill_tplus5_shadow.py` 的 `PriceSeriesFetcher` 用 `tushare.daily` + `adj_factor` 自建 `qfq_ratio_common_anchor` 序列（`open/close × factor / anchor`），不消费报告里的价位，也不需要生产代码提供截断复权。**测量期读数不受影响**。
4. **真正受影响面**：历史回放报告中的**绝对价位**（现价、支撑/压力/均线、目标价/止损）与当时盘面不可比；价格门（`price_basis_gate`）在历史模式下核对的坐标也是事后前复权值——这正是 DAV-1312 审计中 "(c) 前复权偏移" 类误拦的来源。

## 1. 现状口径链路（基线 `ddec52f9` 实测）

### 1.1 通道现状

- `CnAkshareProvider._fetch_hist_df(symbol, start, end, price_basis, as_of)`（`cn_akshare_provider.py:945`）已实现双通道：
  - `vendor_qfq`（默认）：EM `stock_zh_a_hist(adjust="qfq")` → Sina → 腾讯三级回退，ETF 走 fund_etf 系列。给出的是**拉取日**口径的前复权价。
  - `raw`：`_fetch_tushare_raw_daily`（`:3311`）走 Tushare 私有网关 `api_name="daily"` 不复权日线，带 PIT 守卫（`trade_date/start/end > as_of` 在发网前拒为 `date_exceeds_as_of`）、`token_missing` 无网失败闭合、required-fields 按名校验。D-01 契约明文禁止给 raw 通道传复权参数。
- `pit_raw` / `pit_adjusted` / `unspecified` 三个短标已登记在 `validate_price_basis_short_label`（`:87-117`），但在 provider 和 `DataCollector`/`_fetch_all`/`make_cache_key` 四处都是显式 `UnsupportedPriceBasisError` 失败闭合——**截断前复权通道目前不存在**。
- `DataCollector.collect(ticker, trade_date, horizons, price_basis)`（`data_collector.py:3374`）与 `_fetch_all`（`:2564`）已完整支持 `raw` 路径：`_fetch_raw_stock_data` 直连 provider、CSV 头部重写 `# price_basis: raw`、`results["price_basis"]` 透传。
- `api/services/backtest_service.py` 的 `_get_price_on` / `_get_price_after` 已按 `price_basis` 双路由（vendor_qfq→route_to_vendor，raw→cn_akshare provider，其余 fail-closed），回测 outcome 价格口径跟随 `final_state["price_basis"]`。
- `api/services/price_basis_labels.py` 已建好短标↔`price_basis.*` cohort 版本标的双向映射（含 `pit_adjusted`），加新口径=加一行映射。

### 1.2 缺口

- 主分析路径从不传 `price_basis`：`TradingAgentsGraph.propagate`（`trading_graph.py:705`）、`api/main.py` 三处 `collect()` 调用全部用默认值 `vendor_qfq`；历史/实时不分。
- `get_indicators`（provider `:1130`）与 `data_collector` 内本地指标计算（`:3214` 起，`stockstats` wrap）都直接吃 `_fetch_hist_df`/解析后的 df——**口径跟随输入序列**，只要序列换了口径指标自动一致，无需逐指标改造，但必须在文档/声明层写明"指标口径=序列口径"。
- `price_ref_registry` 的 `build_market_data_pool` 把 CSV 头部 `price_basis` 读进 `pool.stock_data_basis`，技术报告 refs 继承 `vendor_qfq` 是**硬编码继承**（`:1973` `provenance="technical_report:vendor_qfq"`），未见对 `raw`/截断口径的继承分支。
- `price_basis_gate._legal_execution_ref` 只认 `basis == vendor_qfq`；`price_basis_version` 只在 gate pass 时落 `price_basis.vendor_qfq`，否则 `unspecified`。
- prompts（`tradingagents/prompts/zh.py` 2/356/419/500/535/568/571 行等）把"技术执行坐标一律使用前复权（vendor_qfq）"写死进契约文本。

## 2. 目标语义与口径选型

需求原文（卡面）："历史日期分析改用分析日当时可见的价格口径：**不复权价，或只按分析日及之前的除权事件做复权**。报告要标明所用口径。"

### 2.1 候选口径

| 口径 | 语义 | 当时盘面等价 | 序列内可比性 | 供给 |
|---|---|---|---|---|
| `pit_raw`（分析日截断·不复权） | Tushare `daily` 原始 OHLC，`end_date=trade_date` | ✅ 逐 bar 即当时盘面价 | ⚠️ 跨除权日有跳空，"昨日收盘→今日开盘"涨跌幅在除权日断裂；均线类技术指标在除权点产生伪信号 | 已上线（`_fetch_tushare_raw_daily`） |
| `pit_qfq`（分析日截断·前复权，新增） | `daily × adj_factor(t) / adj_factor(min(trade_date, 最新因子日))`，等价 Tushare `pro_bar` 在 `end_date=trade_date` 时的输出 | ⚠️ 末根 bar 是当时的 qfq 价≠盘面成交价（有未兑现分红时偏低，如 600519 06-10 例：当时口径 qfq≈1246 vs 盘面 1276） | ✅ 全序列可比，指标语义与实时 vendor_qfq 一致 | 需新增：`daily`+`adj_factor` 两表本地合成 |
| `vendor_qfq`（现状） | 拉取日口径前复权 | ❌ 历史回放中失真（本卡问题） | ✅ | 已上线 |

### 2.2 裁定建议（供总控裁决，**推荐 A**）

**A. 历史模式默认 `pit_raw`，报告标明"不复权（当时盘面口径）"。**

理由：
1. 唯一与"当时盘面价位"逐点相等的口径，直接消掉 DAV-1309 的现象（支撑位/止损与当时盘面可比）与 DAV-1312 (c) 类误拦根因；
2. `raw` 通道已实现、有 PIT 守卫、失败闭合语义完整——改动面最小；
3. Tushare `daily` 本身就带 `pre_close`（前一交易日不复权收盘），pct_change 可由 `close/pre_close−1` 正确定义——除权日的跳空是**真实市场事件**，不是数据缺陷；需要"可比序列"的消费方（收益率比值）按卡面依据 2 本就不受基准影响；
4. 指标（均线/布林/ATR 等）在除权窗口含真实跳空。这是"当时分析者所见"的忠实还原——当时的技术派看的也是不连续盘面或自行复权图。**若总控判定技术指标必须连续口径，则退到 B**。

**B. 历史模式 `pit_qfq`（截断前复权）**，报告标明"按分析日截断的前复权"。优点是指标/收益率语义与实时路径完全同构，跨口径混算风险最低；代价是要新增合成通道（拉 `daily`+`adj_factor` 两个 API、锚定 `adj_factor(min(trade_date, 因子表最新日))`、除权缺口日历、字段校验），且**绝对价位仍不是当时盘面价**（只满足"只按分析日及之前除权事件复权"的字面口径，不满足"与盘面可比"的卡面动机）。建议仅当 A 被否时采用；若采用，标签建议 `pit_qfq` 而非复用 `pit_adjusted`（避免与"后复权"歧义），同步注册进 `price_basis_labels`。

**C. 不推荐**："报告用 pit_raw 展示、指标用 pit_qfq 算"的双口径混合——两套坐标并存正是 price_ref.v1 契约要防的混算源，门的复杂度和误拦面都会上升。

以下 §3–§6 按 **A 为主线** 设计；B 的差异点以「(B)」标注。

## 3. 切换设计

### 3.1 决策点（单一事实源）

新增唯一决策函数，建议落在 `data_collector.py` 或 `trade_calendar.py`：

```python
def resolve_price_basis(trade_date: str, config: Mapping | None = None) -> str:
    """历史回放 -> pit_raw(设计稿口径A)；当日/实时 -> vendor_qfq。"""
    if is_historical_analysis_date(trade_date):
        return PRICE_BASIS_PIT_RAW
    return PRICE_BASIS_VENDOR_QFQ
```

- 判定复用 `is_historical_analysis_date`（`trade_calendar.py:69`，Asia/Shanghai 当日之前为历史），与快照拒绝、实时行情屏蔽同一基准——**口径切换边界与现有 PIT 边界天然一致**，不需要第二条判定。
- 配置面：新增 `config["historical_price_basis"]`（缺省 `pit_raw`）供 B 方案或回退开关；**不提供**按请求 override 的 API 入参——历史口径是语义契约，不是用户选项。(B: 缺省 `pit_qfq`。)

### 3.2 接线点（全部调用点枚举，基线实测）

| 调用点 | 现状 | 改动 |
|---|---|---|
| `TradingAgentsGraph.propagate` `trading_graph.py:705` | `collect(name, trade_date)` 默认 qfq | 传 `price_basis=resolve_price_basis(trade_date)` |
| `propagate_async` `trading_graph.py:841` | 同上 | 同上 |
| `api/main.py:3525 / :4487 / :4720` 三处 `collect` | 同上 | 同上（或在 collect 内部默认代理解析，见下） |
| `DataCollector.get / ref / evict` | 默认 qfq key | 调用方同上；函数签名保持 |
| `backtest_service._run_analysis` `:508` | `final_state.get("price_basis") or vendor_qfq` | **不改**：`results["price_basis"]` 已透传，outcome 取价自动跟随 |
| `market_analyst._fetch_direct` 回退路径 | `get_stock_data`/`get_indicators` 走 route_to_vendor 默认 qfq | 见 §3.4 |
| `route_to_vendor` 默认链 | `_extract_as_of` 取 `end_date`(idx 2) | 不改路由；口径决策不进入 vendor 路由层 |

**更稳的接法（推荐）**：`DataCollector.collect` 的 `price_basis` 默认参数由 `PRICE_BASIS_VENDOR_QFQ` 改为 `None`，`None` 时内部 `resolve_price_basis(trade_date)`。这样 `propagate`/`api/main` 全部调用点**零改动**自动获得历史口径；显式传值（实验、脚本）仍可覆盖。`make_cache_key`/`get`/`ref`/`evict` 同步 `None`→resolve。(B: 同一接法，resolve 返回 `pit_qfq`。)

### 3.3 provider 层

A 方案：`pit_raw` 在 provider/collector 三处 `UnsupportedPriceBasisError` 白名单中放行，复用现有 `PRICE_BASIS_RAW` 分支（`raw` 与 `pit_raw` 在此场景语义相同：不复权 + `as_of=trade_date` PIT 守卫）。保留两个标签而非把 `pit_raw` 折叠进 `raw`：语义上 `raw` 是"通道标签"，`pit_raw` 带"截断"语义承诺；`price_basis_version` cohort 标需要区分以识别口径切换前后的样本群。

(B: 新增 `_fetch_pit_qfq_daily`：`daily` + `adj_factor` 两次网关调用，按 `scripts/backfill_tplus5_shadow.py:PriceSeriesFetcher` 已验证的 `factor/anchor` 公式合成，锚=`adj_factor(min(trade_date, 因子表最大日))`；任一表 `no_rows`/`missing_field`/token 缺失→失败闭合；`DuplicateBarConflictError` 透传不降级。)

### 3.4 旁路与兜底面

- **`market_analyst` 的 `_fetch_direct` 回退**（collector 未命中时直接 `route_to_vendor("get_stock_data")`/`get_indicators`）：历史模式下会拿回 vendor_qfq，与 pool 口径分裂。设计：回退路径同样经 `resolve_price_basis` 判定；`raw`/`pit_raw` 时拒绝走通用 vendor 链，直接调 `prov.get_stock_data(..., price_basis=..., as_of=current_date)`，失败闭合文案。(B 同理。)
- **ToolNode 里的 `get_stock_data`/`get_indicators` 工具**（`trading_graph.py:612/676`）：历史模式下 agent 直调工具仍会拿到拉取日 qfq。设计：历史分析中技术面工具已被 collector 池取代（注释 `:675` "normally uses data_collector"），残留调用加历史拒绝或在工具内路由到 pit 通道——倾向**在工具包装层判定 `end_date` 为历史时改走 provider pit 通道**，而不是拒绝（拒绝会把工具调用变成新的失败噪音）。
- **`get_indicators`（provider `:1130`）**：签名无 `price_basis`，内部 `_fetch_hist_df` 默认 qfq。设计：加 `price_basis: str | None = None` 参数，`None`→按 `curr_date` 走 `resolve_price_basis`——历史日期自动切、当日不变。

## 4. 一致性切换：指标 · 数据池 · basis 标签 · 报告声明

### 4.1 指标

本地指标（`:3214` 起）与 `_compute_vpa_indicators`/`compute_vpa_deterministic_features` 全部消费解析后的 `df`，**口径自动跟随序列**——这是本设计刻意保住的性质：指标不产生第二条价格通道。差异只需文档承诺：A 口径下 SMA/布林/ATR 在除权点含真实跳空（当时可见的信息）；B 口径下与实时完全同构。

### 4.2 数据池（pool）

`pool["stock_data"]` CSV 头部 `# price_basis:` 行、`results["price_basis"]`、`market_data_context["price_basis"]`（`:3156/:3159`）三处已由 `norm_price_basis` 单点驱动，新口径只要进了 `norm_price_basis` 就自动全链路透传。**唯一要改的是三处白名单放行 `pit_raw`(B: `pit_qfq`)**。

### 4.3 price_ref basis 标签（registry + gate）

这是切换中**唯一需要语义裁决**的层。现状：技术报告 refs 硬编码继承 `vendor_qfq`，gate 只认 `vendor_qfq` 可执行，cohort 版本标 `price_basis.vendor_qfq`。

设计（A 口径下）：

1. `build_market_data_pool`：`pool.stock_data_basis` 直接来自 CSV 头部，已正确——refs 桥接（C5）随 `basis_hint` 自动标 `pit_raw`/`raw`。
2. `audit_price_ref_registry` 技术报告继承：`:1973` 的 `technical_report:vendor_qfq` 改为按 `pool.stock_data_basis` 继承（registry 已持有 pool，见 `attach_price_ref_source`）。provenance 记 `technical_report:{basis}`。
3. `price_basis_gate`：
   - `_legal_execution_ref` 的合法集合由 `{vendor_qfq}` 改为"该 run 的执行坐标集"：从 `state`/`pool` 读 `run_coordinate_basis = stock_data 头部 basis`，判定 `ref.basis == run_coordinate_basis`（外加合法 conversion）。**不建议**写死 `{vendor_qfq, pit_raw}` 全集——那会让 raw 披露价在 pit_raw 坐标系下"合法化"，重新打开混算。
   - 契约语义不变，变的是"qfq 执行坐标"→"run 执行坐标"。披露价（大宗/龙虎榜 raw、增减持 pit_raw typed disclosure）在 A 口径下**与执行坐标恰好同族**（都是不复权）——这正是 DAV-1312 (c) 类误拦消失的方式，但要防止反向误放：typed disclosure 的 raw 是"某一披露事件的成交价"，仍是事件价不是连续坐标，门内 `executable_level_wrong_basis` 的判定需保留"disclosure 价不得直接充当可执行位"的规则（现行靠坐标系不同来挡；同族后需改按 `disclosure_type`/`provenance` 挡）。**这是 A 口径下最大的语义回归点，须在实施卡中单列测试。**
   - `price_basis_version`：pass 时落 `to_price_basis_version(run_coordinate_basis)`（`price_basis.pit_raw`），不再恒 `vendor_qfq`；blocked 仍 `unspecified`。`price_basis_labels.py` 映射表已就绪，零迁移。
   - prompts 契约文本（`zh.py` 多处"一律使用前复权（vendor_qfq）"）：历史 run 的口径声明改为由 `resolve_price_basis` 结果注入对应文字（"不复权（当时盘面口径）"/"按分析日截断的前复权"）；实时文案不动。实施时注意 prompt 与 gate 判定必须同源，不得两处各自硬编码。

### 4.4 报告"前复权"声明

- stock_data CSV 头部 `# price_basis: pit_raw`（机器可读，已有机制）；
- 报告正文：在 market_analyst 注入的 `【完整日线 OHLCV】` 前缀或 `market_data_context` 格式化块中加一行人读声明，文案随口径参数化："本报告价格为不复权价（当时盘面口径）" / (B) "本报告价格为按 YYYY-MM-DD 截断的前复权价"。不做模型自觉声明（DAV-1312 已证明文档级声明传递有漏），由确定性文本注入承担；
- `result_data.price_basis`/`price_basis_version`（cohort triad）按 §4.3.3 落值，`price_ref_contract_version` 需要升版（`price_ref.v1` 的"qfq 坐标"前提变了）→ 建议 `price_ref.v2` 并写进 gate 输出。

## 5. cache key 与 `market_data_context.daily.as_of` 语义

### 5.1 cache key

- 现行：`vendor_qfq` → `{ticker}_{trade_date}`；`raw` → `{ticker}_{trade_date}_raw`（`make_cache_key` :666）。
- 设计：`pit_raw` → `{ticker}_{trade_date}_pit_raw`（B: `_pit_qfq`）。**不得复用裸 key 或 `_raw`**——同一 (ticker, trade_date) 下三口径共存于 collector 进程内缓存时互不覆盖；历史 key 带口径后缀还使基线期 qfq 缓存与切换后缓存不可互污染。
- 语义注意：`{ticker}_{date}_{basis}` 的 key 表达的是"分析日"，对 vendor_qfq 而言底层数据还隐含第三维"拉取日"（key 不含拉取日，同 key 在不同日期 collect 会得到不同 qfq 值——这是 vendor_qfq 天然非幂等，现状即如此，本设计不解决也不必解决）。pit_raw/pit_qfq 以分析日截断后**幂等**：同 key 永远同数据，缓存反而更正确。

### 5.2 `daily.as_of` 语义

- 现行（`_build_daily_context` :701）：`df.date ≤ trade_date` 的最大 bar 日 + `completeness`。语义=**数据截止**（最后一根完整日线是哪天的），不是复权基准日。
- 切换后：`daily.as_of` **保持纯数据截止语义**，不改为"复权基准日"。口径基准由 `market_data_context["price_basis"]`（已存在）+ 新增可选 `market_data_context["daily"]["price_basis"]` 冗余标注承载。理由：as_of 已被 `report_service._b1_reference_close`（`:2126`，按 `daily.as_of` 定位 `stock_data.{date}.close`）、propagation cutoff（`propagation.py:129`）、horizon_run_metadata.cutoff 消费——这些都是"数据截止"语义，改义会连带三处。明确写进文档：`daily.as_of` = 最后一根 ≤ trade_date 的完整 bar 的日期；在 pit 口径下该 bar 的值是"当时口径价"，但 as_of 字段本身不承诺口径，口径看 `price_basis`。

## 6. 验证方法

实施时（非本卡）按以下清单验收；本设计只规定方法：

### 6.1 单测（全在 `.venv310` + `env -u PYTHONPATH`，`-p no:randomly`，隔离 DATABASE_URL）

1. `resolve_price_basis`：历史日→`pit_raw`、当日→`vendor_qfq`、非法日期→现有 `is_historical_analysis_date` 语义（False=实时）。
2. `make_cache_key`：三口径 key 两两不等；`None` 入参按 trade_date 解析。
3. provider：`pit_raw` 放行后走 `_fetch_tushare_raw_daily` 同一路径（mock `_tushare_post`，断言不传复权参数、`as_of` 守卫生效、`token_missing` 无网）。(B: `pit_qfq` 合成单测——给定 daily+adj_factor 造表，断言 `bar = raw × f(t)/f(anchor)`、anchor 日选择、缺失因子失败闭合；对照用例：**用同一对表在不同时点"拉取"结果不变=幂等性**。)
4. registry/gate：构造 pool `stock_data_basis=pit_raw` 的 run，断言技术 refs 继承 `pit_raw`、可执行位由 `pit_raw` ref 支撑合法、`price_basis_version=price_basis.pit_raw`；构造跨坐标混算断言仍被拦。
5. 回归门禁：现行 vendor_qfq 全部价格门/registry 测试在实时日期（非历史）下不变红。

### 6.2 实证对拍（离线，不碰生产库）

- 取 DAV-1309 卡面三只锚点股（000333.SZ/600519.SH/600036.SH，2026-06-10）：`pit_raw` 拉取的 close 应分别 ≈85.35 / 1276± / 38.90（当时盘面）；同窗口收益率与 vendor_qfq 路径对拍，比值差异应仅来自舍入（验证依据 2）。数据已在卡面登记，可复用作 fixture 期望。
- 40 档 DAV-1312 (c) 类语料回放：切口径后 gate violations 中 "executable_level_wrong_basis" 的 (c) 子集应清零（口径一致后不再是混算）；`(a)` 正确拦截不得消失——该审计脚本可直接复跑做差分。

### 6.3 端到端冒烟

- 历史日期 + TUSHARE_TOKEN：run 一次历史分析，`result_data.price_basis=pit_raw`、`price_basis_version=price_basis.pit_raw`、报告含口径声明行；
- 历史日期 + 无 token：stock_data 失败闭合（raw 通道无 akshare 回退——**已知代价：历史模式强依赖 Tushare token，与现状 raw 通道一致，实施卡需在失败文案中注明**），ledger 记 `raw daily fetch failed`；
- 当日日期：全链路输出与基线逐字段一致（除新增声明行/标签值外）。

## 7. 边界与风险登记

1. **不做**：不改实时路径行为；不回写/迁移历史 ReportDB；不动 `credit_weighting_enabled`、role_bindings/providers 等配置；不调用真实模型（本卡零模型调用）。
2. **历史报告口径混杂**：切换后 ReportDB 同时存在 `price_basis.vendor_qfq`（历史日期旧样本，实为事后口径）与 `price_basis.pit_raw` 样本——靠 `price_basis_version` cohort 标区分，任何跨群统计必须按 version 分群；`price_basis_isolation` manifest（`:49`）的 clean-cohort 判定（要求 `price_basis.vendor_qfq`）需同步加条款，否则新口径历史样本会被整体隔离。**实施卡必须带该文件的配套修订。**
3. **A 口径残余差异**：即便 pit_raw，"支撑位/止损是当时盘面价"成立，但报告里均线/指标在除权点含跳空——属"当时真实可见"，不归 bug；若总控要求指标连续则转 B。
4. **token 依赖**：pit_raw(B: pit_qfq) 只走 Tushare 私有网关，无 akshare 公开源回退。历史批量在无 token 环境整体失败闭合——这是现有 raw 通道的既定契约（D-01），本设计不放宽。
5. **`unspecified` 语义不变**：仍是失败闭合通道；本设计不给它新含义。
6. **实施排期**：按卡面登记排到第二阶段回测之前；实施前需总控裁决 §2.2 的口径选型（A/B）与本设计中的两处语义裁决点（§4.3 gate 合法集、§6.3 token 缺失失败文案）。

## 8. 变更文件清单（实施时预告）

| 文件 | 改动性质 |
|---|---|
| `tradingagents/graph/data_collector.py` | `resolve_price_basis` 或 collector 内默认解析；三处白名单放行；cache key 后缀 |
| `tradingagents/dataflows/providers/cn_akshare_provider.py` | `pit_raw` 放行（A）/ 新增 `_fetch_pit_qfq_daily`（B）；`get_indicators` 加 `price_basis` 参数 |
| `api/services/price_basis_labels.py` | （B 才需要）`pit_qfq` 短标+版本标注册 |
| `tradingagents/agents/utils/price_ref_registry.py` | 技术报告继承改为 `pool.stock_data_basis` 驱动 |
| `tradingagents/agents/utils/price_basis_gate.py` | 执行坐标集动态化；disclosure 价挡法从"坐标系不同"改为"disclosure_type 识别"；`price_ref.v2` |
| `tradingagents/prompts/zh.py`（及 en.py 对应段） | 契约文案按 run 口径参数化注入 |
| `api/main.py`、`tradingagents/graph/trading_graph.py` | 视接法：collect 传参或零改动 |
| `tradingagents/agents/utils/price_basis_isolation.py` | clean-cohort 判定条款同步 |
| `api/services/backtest_service.py` | `_get_price_on/_get_price_after` 白名单加 `pit_raw`(B:`pit_qfq`) 路由到 provider pit 通道 |
| `tests/` | §6.1 全部用例 |

---

*本文件为设计稿；标注「(B)」的段落在总控裁决走 B 口径时生效。代码位置行号以基线 `ddec52f9` 为准。*
