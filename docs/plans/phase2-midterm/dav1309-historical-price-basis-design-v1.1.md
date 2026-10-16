# DAV-1309 设计文档：历史回放价格口径切换到「按分析日截断的前复权（pit_qfq）」v1.1

| 项 | 内容 |
|---|---|
| 任务 | DAV-1309（规格卡，低优，历史回放专用） |
| 状态 | v1.1，按总控 10-16 三项裁定修订；**只改文档，不实施；不修代码、不回写历史报告、不改实时路径** |
| 起草 | 资深开发1：v1.0 于 2026-10-15，v1.1 于 2026-10-16 |
| 核对基线 | `codex/dav-4-p2a-trunk` tip `5bfd6a82cfe08b58456af40a2df123ea16f55131`（`git ls-remote` 实测；引用代码行号在 v1.0 基线 `ddec52f9` 与 `5bfd6a82` 间未变） |
| 实施排期 | 按卡面登记：**实施排到第二阶段回测之前**；本卡只交设计 |
| 关联 | DAV-1312（前复权偏移误拦量审计）、DAV-703/D-01（raw 日线通道契约）、DAV-715（分红旁证）、DAV-1142/1198/1199（price_ref 注册表与价格门）、DAV-2045（缺凭据口径）、F1 协议 v1.0.1 §3（阶段 H 要求"事件当时的复权价格"） |

**v1.1 修订记录**：口径裁定落实为 **B（`pit_qfq`，按分析日截断的前复权）**，删去 A 方案主线；更正 v1.0 §2.1 中「600519 例 1246 vs 1276」的表述错误（见 §2.3）；按裁定新增两条必做验证（§6.2 锚定实测、§6.3 每日批存档对拍）；§4.3 执行坐标合法集改为 `run_coordinate_basis` 动态判定；§6.4 缺凭据改为进计算前报错退出。

## 0. 卡面登记依据（照录，写文档依据）

> 每日批为当日实时分析、当日取得的前复权价即当时口径；同窗口收益比值不受复权基准影响；T+5 回填已自做锚定——**测量期读数不受影响**；**实施排到第二阶段回测之前**。

展开：

1. **每日批（实时分析）不受影响，且正是 `pit_qfq` 的语义定义**：当日实时取到的 `vendor_qfq`，就是按当日截断的前复权——前复权的漂移只发生在除权事件发生之后，当日除权事件已含在当日因子内。**回放要还原的是系统当日实时运行时的输入，`pit_qfq` 与之同构**（总控 10-16 裁定依据，本设计的全部一致性论证由此出发）。因此切换范围必须且只需覆盖 `is_historical_analysis_date(trade_date) == True` 的路径。
2. **同窗口收益比值不受复权基准影响**：`(exit − entry) / entry` 在同一复权坐标系内对公共缩放因子不变。方向打分、H1b 读数不随口径切换改变。
3. **T+5 回填已自做锚定**：`scripts/backfill_tplus5_shadow.py` 的 `PriceSeriesFetcher` 用 `tushare.daily` + `adj_factor` 自建 `qfq_ratio_common_anchor` 序列（`open/close × factor / anchor`），不消费报告里的价位，也不需要生产代码提供截断复权。**测量期读数不受影响**。
4. **真正受影响面**：历史回放报告中的**绝对价位**（现价、支撑/压力/均线、目标价/止损）按拉取日口径前复权，与"系统若在当日实时运行会看到的序列"不一致（分析日之后的分红/送转会把历史 bar 再压低）；价格门（`price_basis_gate`）在历史模式下核对的坐标也是事后前复权值——这正是 DAV-1312 审计中 "(c) 前复权偏移" 类误拦的来源。

## 1. 现状口径链路（基线 `ddec52f9` 实测，`5bfd6a82` 复核未变）

### 1.1 通道现状

- `CnAkshareProvider._fetch_hist_df(symbol, start, end, price_basis, as_of)`（`cn_akshare_provider.py:945`）已实现双通道：
  - `vendor_qfq`（默认）：EM `stock_zh_a_hist(adjust="qfq")` → Sina → 腾讯三级回退，ETF 走 fund_etf 系列。给出的是**拉取日**口径的前复权价。
  - `raw`：`_fetch_tushare_raw_daily`（`:3311`）走 Tushare 私有网关 `api_name="daily"` 不复权日线，带 PIT 守卫（`trade_date/start/end > as_of` 在发网前拒为 `date_exceeds_as_of`）、`token_missing` 无网失败闭合、required-fields 按名校验。D-01 契约明文禁止给 raw 通道传复权参数。
- `pit_raw` / `pit_adjusted` / `unspecified` 三个短标已登记在 `validate_price_basis_short_label`（`:87-117`），但在 provider 和 `DataCollector`/`_fetch_all`/`make_cache_key` 四处都是显式 `UnsupportedPriceBasisError` 失败闭合——**截断前复权通道目前不存在**。
- `DataCollector.collect(ticker, trade_date, horizons, price_basis)`（`data_collector.py:3374`）与 `_fetch_all`（`:2564`）已完整支持 `raw` 路径（`_fetch_raw_stock_data` 直连 provider、CSV 头部重写 `# price_basis:`、`results["price_basis"]` 透传）——`pit_qfq` 复用同一接线骨架。
- `api/services/backtest_service.py` 的 `_get_price_on` / `_get_price_after` 已按 `price_basis` 双路由（vendor_qfq→route_to_vendor，raw→cn_akshare provider，其余 fail-closed），回测 outcome 价格口径跟随 `final_state["price_basis"]`。
- `api/services/price_basis_labels.py` 已建好短标↔`price_basis.*` cohort 版本标的双向映射；`pit_qfq` 按总控裁定**新增注册**（一行短标 + 一行版本标 + 映射表项，见 §4.3）。

### 1.2 缺口

- 主分析路径从不传 `price_basis`：`TradingAgentsGraph.propagate`（`trading_graph.py:705`）、`api/main.py` 三处 `collect()` 调用全部用默认值 `vendor_qfq`；历史/实时不分。
- `get_indicators`（provider `:1130`）与 `data_collector` 内本地指标计算（`:3214` 起，`stockstats` wrap）都直接吃 `_fetch_hist_df`/解析后的 df——**口径跟随输入序列**，序列换 `pit_qfq` 指标自动同口径，无需逐指标改造；文档承诺见 §4.1。
- `price_ref_registry` 的 `build_market_data_pool` 把 CSV 头部 `price_basis` 读进 `pool.stock_data_basis`，但技术报告 refs 继承是**硬编码 `vendor_qfq`**（`:1973` `provenance="technical_report:vendor_qfq"`），未见对其他口径的继承分支。
- `price_basis_gate._legal_execution_ref` 只认 `basis == vendor_qfq`；`price_basis_version` 只在 gate pass 时落 `price_basis.vendor_qfq`，否则 `unspecified`。
- prompts（`tradingagents/prompts/zh.py` 2/356/419/500/535/568/571 行等）把"技术执行坐标一律使用前复权（vendor_qfq）"写死进契约文本。
- **`adj_factor` 表尚无 provider 级通道**：`pit_qfq` 需要的因子表现在只在 `scripts/backfill_tplus5_shadow.py` 内以裸 `requests` 直查（`:259` 起，`api_name="adj_factor"`，`fields=ts_code,trade_date,adj_factor`，独立节流、session trust_env=False、凭证不外泄的错误归类）。该脚本已验证供给面可用，但实现进 provider 时须按 `_tushare_post` 的既有通道规范重写（typed 错误分类、PIT 守卫、禁凭证回退），不得 copy 裸请求。

## 2. 口径裁定：B（`pit_qfq`），总控 10-16 已裁

### 2.1 裁定

**历史模式默认 `pit_qfq`（按分析日截断的前复权）**，报告标明"前复权（按分析日 YYYY-MM-DD 截断）"。总控裁定依据照录：*回放要还原的是系统当日实时运行时的输入。当日实时取到的 `vendor_qfq`，就是按当日截断的前复权，与 `pit_qfq` 同构；技术指标连续，价格门控的语义回归面最小。*

落选方案登记（不再展开）：

- A `pit_raw`（截断不复权）：绝对价位=当时盘面价、改动面最小，但技术指标在除权点断裂、与"还原当日实时输入"的目标不符，且披露价（raw 族）与执行坐标同族反而要求门改挡法——按裁定不采用。
- C 双口径混合（raw 展示 + qfq 指标）：两套坐标并存正是 price_ref.v1 契约要防的混算源，否决。

### 2.2 `pit_qfq` 口径定义（实施契约）

序列合成公式（对每根 bar t，复用 `backfill_tplus5_shadow.py` 已验证的 `qfq_ratio_common_anchor` 公式）：

```
adj_close(t) = raw_close(t) × adj_factor(t) / adj_factor(anchor)
anchor       = min(trade_date, max(adj_factor 表中的因子日))
```

对 open/high/low 同样乘以 `f(t)/f(anchor)`；volume/amount 保持 raw（与 vendor_qfq 通道行为一致——成交量在 akshare qfq 输出中本就未缩放，保持一致以免下游量指标分裂）。

**锚点恒等式（v1.1 新增，§6.2 的实测对象）**：若 `anchor = trade_date`（即 trade_date 当日因子表有行——A 股除权日当日因子总是已更新），则 `f(anchor)=f(trade_date)`，末根 bar `adj_close(trade_date) = raw_close(trade_date)` × 1 = **当日不复权收盘价**。也就是：*`pit_qfq` 序列的末根 K 线价格恒等于当日盘面价*；离 trade_date 越远的 bar 才随历次除权事件被压低。这正是"前复权"的语义——把历史价折算到最新因子口径，而"最新"在这里被截断到分析日。

### 2.3 v1.0 表述更正（裁定二-1）

v1.0 §2.1 曾写「末根 bar 是当时的 qfq 价≠盘面成交价（……600519 06-10 例：当时口径 qfq≈1246 vs 盘面 1276）」。**此说法与 §2.2 锚定规则矛盾，予以更正**：

- 1246.38 是**以 2026-09 拉取日为锚**的 vendor_qfq 序列在 06-10 那一根的值——即事后口径，把 06-10 之后（至 9 月）发生的除权事件也折了进去，不是"当时口径"。
- 按 §2.2 的锚定规则（anchor=min(2026-06-10, 因子表最新日)），06-10 当日因子已含当日除权（若有），`pit_qfq` 末根收盘 = 1276 左右的不复权盘面价，**不是 1246**。
- 更正后的准确表述：`pit_qfq` 的**中间历史 bar**（trade_date 之前、且早于 trade_date 之后任除权日）与当时盘面价仍可能不等（它们被折算到 trade_date 因子口径），但**末根 bar 与当时盘面价相等**，整条序列与"当日实时运行会拿到的 vendor_qfq 序列"在舍入内一致（§6.3 对拍验证该命题）。

## 3. 切换设计

### 3.1 决策点（单一事实源）

新增唯一决策函数，建议落在 `data_collector.py`（紧邻 `make_cache_key`）或 `trade_calendar.py`：

```python
def resolve_price_basis(trade_date: str, config: Mapping | None = None) -> str:
    """历史回放 -> pit_qfq（按分析日截断的前复权）；当日/实时 -> vendor_qfq。"""
    if is_historical_analysis_date(trade_date):
        return PRICE_BASIS_PIT_QFQ
    return PRICE_BASIS_VENDOR_QFQ
```

- 判定复用 `is_historical_analysis_date`（`trade_calendar.py:69`，Asia/Shanghai 当日之前为历史），与快照拒绝、实时行情屏蔽同一基准——**口径切换边界与现有 PIT 边界天然一致**，不需要第二条判定。
- 配置面：新增 `config["historical_price_basis"]`（缺省 `pit_qfq`）作为回退开关（例如因子表供源故障时总控可切回 vendor_qfq 应急）；**不提供**按请求 override 的 API 入参——历史口径是语义契约，不是用户选项。

### 3.2 接线点（全部调用点枚举，基线实测）

| 调用点 | 现状 | 改动 |
|---|---|---|
| `TradingAgentsGraph.propagate` `trading_graph.py:705` | `collect(name, trade_date)` 默认 qfq | 传 `price_basis=resolve_price_basis(trade_date)`，或靠下方零改动接法 |
| `propagate_async` `trading_graph.py:841` | 同上 | 同上 |
| `api/main.py:3525 / :4487 / :4720` 三处 `collect` | 同上 | 同上 |
| `DataCollector.get / ref / evict` | 默认 qfq key | 与 collect 同一解析，签名保持 |
| `backtest_service._run_analysis` `:508` | `final_state.get("price_basis") or vendor_qfq` | **不改**：`results["price_basis"]` 已透传，outcome 取价自动跟随（`_get_price_on/_get_price_after` 白名单需加 `pit_qfq` 分支，见 §8） |
| `market_analyst._fetch_direct` 回退路径 | `get_stock_data`/`get_indicators` 走 route_to_vendor 默认 qfq | 见 §3.4 |
| `route_to_vendor` 默认链 | `_extract_as_of` 取 `end_date`(idx 2) | 不改路由；口径决策不进入 vendor 路由层 |

**更稳的接法（推荐）**：`DataCollector.collect` 的 `price_basis` 默认参数由 `PRICE_BASIS_VENDOR_QFQ` 改为 `None`，`None` 时内部 `resolve_price_basis(trade_date)`。这样 `propagate`/`propagate_async`/`api/main` 全部调用点**零改动**自动获得历史口径；显式传值（实验、脚本）仍可覆盖。`make_cache_key`/`get`/`ref`/`evict` 同步 `None`→resolve。

### 3.3 provider 层：新增 `_fetch_pit_qfq_daily`

按裁定 B 实施，结构镜像 `_fetch_tushare_raw_daily`（同一网关、同一 typed 错误分类、同一 PIT 守卫模式），差异仅在"两表合成"：

1. **两次网关调用**：`api_name="daily"`（fields 同 `_TUSHARE_DAILY_REQUIRED_FIELDS`）+ `api_name="adj_factor"`（`fields=ts_code,trade_date,adj_factor`）。`start_date/end_date` 两表同窗（合成窗口 [start, trade_date]；因子表为了锚定需要覆盖到 trade_date，不必外延）。
2. **PIT 守卫**：两表同规则——`trade_date > as_of` 行丢弃（复用逐行过滤），`start/end > as_of` 发网前拒为 `date_exceeds_as_of`；`as_of` 默认 = `end_date` = `trade_date`。
3. **锚定**：`anchor = min(trade_date, max(因子表因子日))`；`f(anchor) = adj_factor(anchor)`。anchor 行缺失（trade_date 不是交易日、或因子表最新日早于 trade_date 时取最新日）按"因子表实际最新日"落锚——`min` 语义即"不晚于分析日的最新因子"。
4. **合成**：`adj_x(t) = raw_x(t) × f(t) / f(anchor)`（open/high/low/close）；任一 bar 因子缺失或非正值→该 bar 丢弃并记 gap；因子表整体 `no_rows`/`missing_field`/`token_missing`→失败闭合（不降级 akshare、不回落 vendor_qfq）。
5. **标签**：`out.attrs["price_basis"] = "pit_qfq"`；CSV 头部 `# price_basis: pit_qfq`（复用 `_fetch_all` 的 provenance 改写骨架，§4.2）。
6. **成交量**：不缩放（与 vendor_qfq 通道一致，见 §2.2）。
7. **失败闭合语义**：`pit_qfq` 通道**只允许 Tushare 私有网关**（daily+adj_factor 同源才能保证因子与 bar 对齐），无 akshare 公开源回退——token 缺失即失败闭合（§6.4）。

### 3.4 旁路与兜底面

- **`market_analyst` 的 `_fetch_direct` 回退**（collector 未命中时直接 `route_to_vendor("get_stock_data")`/`get_indicators`）：历史模式下会拿回 vendor_qfq，与 pool 口径分裂。设计：回退路径同样经 `resolve_price_basis` 判定；`pit_qfq` 时拒绝走通用 vendor 链，直接调 `prov.get_stock_data(..., price_basis="pit_qfq", as_of=current_date)`，失败闭合文案。
- **ToolNode 里的 `get_stock_data`/`get_indicators` 工具**（`trading_graph.py:612/676`）：历史模式下 agent 直调工具仍会拿到拉取日 qfq。设计：在工具包装层判定 `end_date` 为历史时改走 provider `pit_qfq` 通道，而不是拒绝（拒绝会把残留工具调用变成新的失败噪音；注释 `:675` 已注明"normally uses data_collector"，直调是边角路径）。
- **`get_indicators`（provider `:1130`）**：签名无 `price_basis`，内部 `_fetch_hist_df` 默认 qfq。设计：加 `price_basis: str | None = None` 参数，`None`→按 `curr_date` 走 `resolve_price_basis`——历史日期自动切、当日不变。

## 4. 一致性切换：指标 · 数据池 · basis 标签 · 报告声明

### 4.1 指标

本地指标（`:3214` 起）与 `_compute_vpa_indicators`/`compute_vpa_deterministic_features` 全部消费解析后的 `df`，**口径自动跟随序列**——本设计刻意保住的性质：指标不产生第二条价格通道。`pit_qfq` 下指标语义与实时 vendor_qfq **完全同构**（同一公式、同一连续序列形态），这正是裁定 B 的核心收益。文档承诺：历史 run 的 SMA/EMA/RSI/MACD/布林/ATR/VWMA 与 VPA 特征 = "当日实时运行会算出的值"（舍入内，由 §6.3 对拍担保）。

### 4.2 数据池（pool）

`pool["stock_data"]` CSV 头部 `# price_basis:` 行、`results["price_basis"]`、`market_data_context["price_basis"]`（`:3156/:3159`）三处已由 `norm_price_basis` 单点驱动，`pit_qfq` 只要进了 `norm_price_basis` 就自动全链路透传。**要改的是**：①三处 `UnsupportedPriceBasisError` 白名单放行 `pit_qfq`；②`_fetch_all` 的 provenance 改写分支补 `pit_qfq` 情形（现行只有 vendor_qfq/raw 两个互斥分支）。

### 4.3 price_ref basis 标签（registry + gate）——按总控 10-16 裁定

1. `build_market_data_pool`：`pool.stock_data_basis` 直接来自 CSV 头部，已正确——refs 桥接（C5）随 `basis_hint` 自动标 `pit_qfq`。
2. `audit_price_ref_registry` 技术报告继承：`:1973` 的 `technical_report:vendor_qfq` 改为按 `pool.stock_data_basis` 继承（registry 经 `attach_price_ref_source` 已持有 pool）。provenance 记 `technical_report:{basis}`。
3. `price_basis_gate`（裁定三）：
   - **执行坐标合法集动态化**：`_legal_execution_ref` 由硬编码 `basis == vendor_qfq` 改为 `basis == run_coordinate_basis`（取自 pool 头部 `stock_data_basis`，即本次运行的执行坐标），外加合法 conversion；**不写死任何并集**。
   - **披露价规则保留**：大宗/龙虎榜 raw、增减持/回购/定增 pit_raw 等 typed disclosure **不得直接充当可执行价位**——这条不因同族化而放开（pit_qfq 坐标下 raw 仍是异坐标，天然被挡；但规则须在代码中显式以 `disclosure_type`/`provenance` 判定，不依赖坐标差碰巧存在），并**单列测试**（§6.1-4）。
   - `price_basis_version`：pass 时落 `to_price_basis_version(run_coordinate_basis)`——历史 run 落 `price_basis.pit_qfq`；blocked 仍 `unspecified`。
   - **`pit_qfq` 标签注册**：`api/services/price_basis_labels.py` 新增 `PRICE_BASIS_PIT_QFQ="pit_qfq"` 与 `PRICE_BASIS_VERSION_PIT_QFQ="price_basis.pit_qfq"` 及双向映射（provider 侧 `_KNOWN_PRICE_BASIS_SHORT_LABELS` 同步）。
   - **契约升版**：`price_ref_contract_version` → `price_ref.v2`（v1 的"qfq 执行坐标"前提变为"run 执行坐标"），写进 gate 输出与 `result_data`。
   - prompts 契约文本（`zh.py` 多处"一律使用前复权（vendor_qfq）"）：历史 run 的口径声明由 `resolve_price_basis` 结果注入对应文字（"前复权（按分析日截断）"）；实时文案不动。prompt 与 gate 判定必须同源，不得两处各自硬编码。

### 4.4 报告"前复权"声明

- stock_data CSV 头部 `# price_basis: pit_qfq`（机器可读，已有机制）；
- 报告正文：在 market_analyst 注入的 `【完整日线 OHLCV】` 前缀或 `market_data_context` 格式化块中加一行人读声明，文案随口径参数化："本报告价格为按 YYYY-MM-DD 截断的前复权价"（实时 run 维持"前复权"既有表述）。不做模型自觉声明（DAV-1312 已证明文档级声明传递有漏），由确定性文本注入承担；
- `result_data.price_basis`/`price_basis_version` 按 §4.3 落值，`price_ref_contract_version="price_ref.v2"`。

## 5. cache key 与 `market_data_context.daily.as_of` 语义

### 5.1 cache key

- 现行：`vendor_qfq` → `{ticker}_{trade_date}`；`raw` → `{ticker}_{trade_date}_raw`（`make_cache_key` :666）。
- 设计：`pit_qfq` → `{ticker}_{trade_date}_pit_qfq`。**不得复用裸 key**——同一 (ticker, trade_date) 下多口径共存于 collector 进程内缓存时互不覆盖；历史 key 带口径后缀还使基线期 qfq 缓存与切换后缓存不可互污染。
- 语义注意：`{ticker}_{date}_{basis}` 的 key 表达的是"分析日"，对 vendor_qfq 而言底层数据还隐含第三维"拉取日"（key 不含拉取日，同 key 在不同日期 collect 会得到不同 qfq 值——vendor_qfq 天然非幂等，现状即如此，本设计不解决也不必解决）。`pit_qfq` 以分析日截断后**幂等**：同 key 永远同数据，缓存反而更正确。

### 5.2 `daily.as_of` 语义

- 现行（`_build_daily_context` :701）：`df.date ≤ trade_date` 的最大 bar 日 + `completeness`。语义=**数据截止**（最后一根完整日线是哪天的），不是复权基准日。
- 切换后：`daily.as_of` **保持纯数据截止语义**，不改为"复权基准日"。口径基准由 `market_data_context["price_basis"]`（已存在）+ 新增可选 `market_data_context["daily"]["price_basis"]` 冗余标注承载。理由：as_of 已被 `report_service._b1_reference_close`（`:2126`，按 `daily.as_of` 定位 `stock_data.{date}.close`）、propagation cutoff（`propagation.py:129`）、horizon_run_metadata.cutoff 消费——这些都是"数据截止"语义，改义会连带三处。明确写进文档：`daily.as_of` = 最后一根 ≤ trade_date 的完整 bar 的日期；在 `pit_qfq` 口径下该 bar 的值恰等于当日盘面收盘（§2.2 锚点恒等式），但 as_of 字段本身不承诺口径，口径看 `price_basis`。

## 6. 验证方法

实施时（非本卡）按以下清单验收；本设计只规定方法。全部单测在 `.venv310`（`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`）+ `env -u PYTHONPATH` 下执行，`-p no:randomly`，`DATABASE_URL` 指隔离临时库。

### 6.1 单测

1. `resolve_price_basis`：历史日→`pit_qfq`、当日→`vendor_qfq`、非法日期→现有 `is_historical_analysis_date` 语义（False=实时）。
2. `make_cache_key`：三口径 key 两两不等；`None` 入参按 trade_date 解析。
3. provider `_fetch_pit_qfq_daily`（mock `_tushare_post` 造 daily+adj_factor 两表）：
   - 合成公式 `bar = raw × f(t)/f(anchor)`、anchor=`min(trade_date, 因子表最新日)` 的选择逻辑（含 trade_date 非交易日、因子表滞后两情形）；
   - PIT 守卫：`end > as_of` 拒、逐行 `trade_date > as_of` 过滤；
   - `token_missing` 无网、任一表 `no_rows`/`missing_field` 失败闭合、`DuplicateBarConflictError` 透传不降级；
   - **幂等性**：同一对输入表在不同时点"拉取"结果不变。
4. registry/gate：构造 `stock_data_basis=pit_qfq` 的 run——技术 refs 继承 `pit_qfq`、可执行位由 `pit_qfq` ref 支撑合法、`price_basis_version=price_basis.pit_qfq`；跨坐标混算仍被拦；**披露价（typed disclosure raw/pit_raw）直接充当可执行位仍被 `executable_level_wrong_basis` 拦**（单列测试，裁定三）。
5. 回归门禁：现行 vendor_qfq 全部价格门/registry 测试在实时日期（非历史）下不变红。

### 6.2 锚点恒等式实测（裁定二-1 必做）

对锚定规则 `anchor=min(trade_date, 因子表最新日)` 实测验证：**`pit_qfq` 末根 K 线收盘价 == 当日不复权收盘价**。

- 取若干 (symbol, trade_date) 样本（含至少一例 trade_date 即除权日、一例 trade_date 前有近期除权）：同一会话拉 `daily`（raw）与 `adj_factor`，按 §2.2 合成 `pit_qfq`，断言 `pit_qfq[-1].close == daily[trade_date].close`（`f(anchor)/f(anchor)=1`，恒等式应精确成立，非舍入近似）；
- 同时记录 `pit_qfq` 中间 bar 与 raw 的差异（应有差异=折算生效的证明）；
- 该实测同时背书 §2.3 更正：600519 @2026-06-10 的 `pit_qfq` 末根应为 ≈1276（盘面价），而 vendor_qfq（9 月拉取口径）同根为 1246.38——两者对照即本卡问题与本设计的直接证据。**若实测不成立（如 trade_date 因子缺失导致锚到更早日使末根偏离盘面价），实施卡须报告该边界情形及 anchor 选择修正，不得带病交付。**

### 6.3 每日批存档对拍（裁定二-2 必做）

取近期**有每日批 `stock_data` 存档**的若干日期（这些存档本身就是当日实时取到的 vendor_qfq 序列=当日截断的前复权）：对同一 (ticker, trade_date) 拉 `pit_qfq`，与存档序列**逐根比对**，open/high/low/close 各字段在舍入误差（建议 ≤0.01 元或相对误差 ≤1e-4，取严）以内一致；不一致的 bar 须逐根归因（存档源与 Tushare 源的原始价差异、因子表版本差异等），归因不了的差异即设计失败信号。对拍覆盖至少一只高除权密度股与一只长期无除权股（后者应零差异）。

### 6.4 缺凭据（裁定四）

历史模式**没有 `TUSHARE_TOKEN`** 时，**在进入计算前报错退出**，文案写明「历史模式需要 Tushare 凭据，无公开源回退」；**不回落到 akshare**，与 DAV-2045 的口径一致（该脚本在 token 缺失时即拒绝取数运行）。落实点：`resolve_price_basis` 判出历史→`pit_qfq` 后，collector 在 `_fetch_all` 派发前检查 `TUSHARE_TOKEN`，缺失则整个 collect 失败闭合（不等 stock_data 单项失败后流入 data_gaps）——历史模式没有第二价格通道，失败应显式且早。单测：无 token + 历史日期 → collect 抛指定错误文案；无 token + 当日日期 → 不受影响（vendor_qfq 无 token 依赖）。

### 6.5 端到端冒烟

- 历史日期 + TUSHARE_TOKEN：run 一次历史分析，`result_data.price_basis="pit_qfq"`、`price_basis_version="price_basis.pit_qfq"`、报告含"按分析日截断的前复权"声明行；
- 历史日期 + 无 token：collect 在进入计算前报错退出（§6.4）；
- 当日日期：全链路输出与基线逐字段一致（除新增声明行/标签值外）。

## 7. 边界与风险登记

1. **不做**：不改实时路径行为；不回写/迁移历史 ReportDB；不动 `credit_weighting_enabled`、role_bindings/providers 等配置；不调用真实模型（本卡零模型调用）。
2. **历史报告口径混杂**：切换后 ReportDB 同时存在 `price_basis.vendor_qfq`（历史日期旧样本，实为事后口径）与 `price_basis.pit_qfq` 样本——靠 `price_basis_version` cohort 标区分，任何跨群统计必须按 version 分群；`price_basis_isolation` manifest（`price_basis_isolation.py:49`）的 clean-cohort 判定（要求 `price_basis.vendor_qfq`）需同步修订，否则新口径历史样本会被整体隔离。**修订要求按本条执行（裁定三）：实施卡必须带该文件的配套修订与测试。**
3. **`pit_qfq` 与盘面的残余差异**：中间历史 bar 仍可能偏离当时盘面价（折算到 trade_date 口径）——这是"还原当日实时输入"的忠实语义而非缺陷；末根恒等盘面价（§2.2/§6.2）。报告的"支撑位/目标价"可读性依赖末根与近端 bar，语义恢复成立。
4. **token 依赖**：`pit_qfq` 只走 Tushare 私有网关，无 akshare 公开源回退；历史批量在无 token 环境**整体失败闭合**（§6.4，裁定四，与 DAV-2045 口径一致）。
5. **`unspecified` 语义不变**：仍是失败闭合通道；本设计不给它新含义。
6. **实施排期**：按卡面登记排到第二阶段回测之前；实施前需总控对本 v1.1 复审签字。**复审通过后 1309、2108 置 done**（裁定五）。

## 8. 变更文件清单（实施时预告）

| 文件 | 改动性质 |
|---|---|
| `tradingagents/graph/data_collector.py` | `resolve_price_basis`；三处白名单放行 `pit_qfq`；cache key 后缀；无 token 前置失败（§6.4） |
| `tradingagents/dataflows/providers/cn_akshare_provider.py` | 新增 `_fetch_pit_qfq_daily`（daily+adj_factor 两表合成）；`pit_qfq` 放行；`get_indicators` 加 `price_basis` 参数 |
| `api/services/price_basis_labels.py` | `pit_qfq` 短标 + `price_basis.pit_qfq` 版本标注册及双向映射 |
| `tradingagents/agents/utils/price_ref_registry.py` | 技术报告继承改为 `pool.stock_data_basis` 驱动 |
| `tradingagents/agents/utils/price_basis_gate.py` | 执行坐标集动态化（`run_coordinate_basis`，不写死并集）；披露价挡法按 `disclosure_type`/`provenance` 显式保留；`price_ref.v2` |
| `tradingagents/prompts/zh.py`（及 en.py 对应段） | 契约文案按 run 口径参数化注入 |
| `api/main.py`、`tradingagents/graph/trading_graph.py` | 视接法：collect 传参或零改动（推荐后者） |
| `tradingagents/agents/utils/price_basis_isolation.py` | clean-cohort 判定条款同步修订（§7.2） |
| `api/services/backtest_service.py` | `_get_price_on/_get_price_after` 白名单加 `pit_qfq` 路由到 provider pit 通道 |
| `tests/` | §6.1 全部用例 + §6.1-4 披露价单列测试 |

---

*本文件为设计稿 v1.1，按总控 2026-10-16 三项裁定修订；复审只核改动部分（§2、§4.3、§6.2–6.4 及全文 B 口径落实）。代码位置行号以基线 `ddec52f9`/`5bfd6a82` 为准。*
