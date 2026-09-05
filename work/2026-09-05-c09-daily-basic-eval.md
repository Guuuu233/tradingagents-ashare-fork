# C-09 切片 1：daily_basic 规模指标与资金流规模归一落点评估 (DAV-631)

> **制定日期**：2026-09-05  
> **责任角色**：资深开发1 (`6050b57e-f551-4756-8ad9-3af522d7d4e3`)  
> **基线分支**：`origin/codex/dav-4-p2a-trunk`  
> **基线 SHA**：`d4d145fae714a21bd919fad3ad66dba7fa1ae852`（已包含 C-05d `b42bb50893ec135c9f365f98f12255a18728f696` 与 C-04 文档 `work/2026-09-05-c04-pit-raw-dividend-eval.md`）  
> **依据文件**：`work/2026-09-05-tushare-private-gateway-matrix.md` (DAV-618)、`work/2026-09-05-c04-pit-raw-dividend-eval.md` (DAV-628)  
> **唯一关注点**：冻结如何用私有网关 `daily_basic` 接口进行成交额、自由流通股本与流通市值的规模归一化，以及 Point-in-Time (PIT) 字段时点约束、缺列上报规范与防未来回填红线。  
> **六大禁止红线**：
> 1. 严禁改动 `tradingagents/` 业务代码（纯只读评估卡）；
> 2. 严禁在代码、配置、文档、日志或评论中记录或打印任何真实 Token；
> 3. 严禁修改 providers 契约与实现；
> 4. 严禁接线生产与启停后台服务；
> 5. 严禁混入 C-04 复权计算引擎；
> 6. 严禁混入 C-05（巨潮/公告/事件旁证）施工范围。

---

## 一、背景与核心痛点：资金流“大小盘不可比”与规模归一必要性

在现有数据体系中，`tradingagents/dataflows/providers/cn_akshare_provider.py` 已经接入并规范化了来自东方财富（`moneyflow_dc`）与同花顺（`moneyflow_ths`）的资金流数据。其输出的主力净流入额（`net_amount`）、超大单买入额（`buy_lg_amount`）等均为以“万元”或“亿元”计量的绝对货币金额。

### 1.1 绝对金额对比引发的“市值规模偏差（Size Bias）”

在 A 股多标的横向比较与多智能体（Multi-Agent）投研决策中，直接使用绝对资金流入金额会引发严重的认知失真：

1. **大盘权重股的资金钝化**：
   - 以流通市值数千亿元至上万亿元的超大盘权重股（如工商银行、中国移动、贵州茅台）为例，单日主力净流入 1 亿元，仅占其流通市值的 $0.01\% \sim 0.05\%$，占单日总成交额的比例可能不足 $3\%$；
   - 这种流入属于日常流动性吞吐的常规噪音，并不代表主力资金在进行强烈的方向性攻击。
2. **小微盘个股的极端敏感**：
   - 对流通市值仅 20 亿～50 亿元的小盘成长股或题材股而言，单日主力净流入 1 亿元已占其流通盘的 $2\% \sim 5\%$，往往占其当日总成交额的 $25\% \sim 40\%$ 以上；
   - 这反映出极其罕见的建仓抢筹或强庄锁仓行为，预示着巨大的短期价格弹性。
3. **模型评分与仲裁失衡**：
   - 若上层量化因子或多智能体分析系统缺乏规模基准，直接基于净流入绝对金额排序或设定全局固定阈值，系统将持续向大盘权重股倾斜（产生虚假的大单信号），而系统性忽视小微盘股的高动量异动。

### 1.2 解决路径：多维度规模归一化（Scale Normalization）

为了使不同市值体量、不同股权流动性特征的标的在资金流维度具备客观可比性，必须通过引入标的每日基本面与交易规模基准，将资金流从“绝对金额”映射为“相对渗透强度”。私有网关提供的 `daily_basic` 接口正是实现该规模归一化的官方基础数据源。

---

## 二、`daily_basic` 规模归一字段全景与数学落点设计

依据官方 Tushare API 规范及私有兼容网关能力定义（参考 [Tushare 每日指标文档](https://tushare.pro/document/2?doc_id=32)），`daily_basic` 接口提供了标的在交易日维度的股本结构、市值规模及交易活跃度指标。

### 2.1 接口输出字段与物理含义审计

| 字段名称 | 类型 | 官方单位 | 物理含义与口径 | 在 C-09 规模归一中的角色 |
|---|---|---|---|---|
| `ts_code` | str | — | Tushare 证券代码（如 `600519.SH`） | 标的唯一代码核验键 |
| `trade_date` | str | `YYYYMMDD` | 交易日期 | PIT 时间截面锚定键 |
| `close` | float | 元/股 | 当日物理收盘价 | 价格基准，用于股本与市值折算 |
| `circ_mv` | float | **万元** | **流通市值**（无限售条件股份 $\times$ 当日收盘价） | **核心规模分母 1（市场流通盘）** |
| `total_mv` | float | 万元 | 总市值（总股本 $\times$ 当日收盘价） | 宏观资本规模基准（含限售股） |
| `free_share` | float | **万股** | **自由流通股本**（剔除持股 5% 以上大股东及限售股等非活跃流通股） | **核心规模分母 2（真实博弈股本）** |
| `float_share` | float | 万股 | 传统流通股本（无限售条件总股本） | 传统流通盘口径基准 |
| `total_share` | float | 万股 | 总股本 | 标的全部发行在外股本 |
| `turnover_rate` | float | % | 换手率（当日成交量 $\div$ 流通股本 $\times 100$） | 传统活跃度基准，可用于校验成交额 |
| `turnover_rate_f` | float | % | **自由流通换手率**（当日成交量 $\div$ 自由流通股本 $\times 100$） | **真实筹码换手率**，衡量博弈烈度 |
| `volume_ratio` | float | 倍 | 量比（当日成交量 $\div$ 过去 5 日每分钟均量） | 放量/缩量异常度辅助指标 |
| `pe` / `pe_ttm` | float | 倍 | 静态 / TTM 滚动市盈率 | 估值分位辅助，不参与规模归一 |
| `pb` / `ps` | float | 倍 | 市净率 / 市销率 | 估值分位辅助，不参与规模归一 |
| `dv_ratio` / `dv_ttm` | float | % | 静态 / 滚动股息率 | 红利收益率指标，不参与规模归一 |
| `limit_status` | int | 枚举 | 涨跌停状态（0平, 1涨未停, 2涨停, 3一字涨停, 4跌未停, 5跌停, 6一字跌停） | 流动性枯竭/封单校验（辅助权重） |

### 2.2 核心规模归一落点数学定义

针对主力净流入资金（`net_amount`，单位：万元），冻结以下三大核心规模归一标准算法：

#### 1. 流通市值归一化主力净流入（Circulating Market Value Normalized Net Inflow）

- **定义**：主力资金净买入额占全市场无限售流通市值的比例。
- **数学公式**：
  $$Ratio_{circ\_mv} = \frac{\text{net\_amount (万元)}}{\text{circ\_mv (万元)}}$$
- **量纲**：无量纲比例（通常表示为百分比 $\%$ 或基点 $bp$，$1\% = 100 bp$）。
- **业务意义**：度量主力资金对上市公司流通资产的吞吐强度。例如：
  - $Ratio_{circ\_mv} > +0.5\%$：全天主力买入达流通盘千分之五，对中小盘股构成强烈的价格推升动能；
  - $Ratio_{circ\_mv} \in [-0.05\%, +0.05\%]$：主力资金处于平衡观望区间。

#### 2. 自由流通市值归一化主力净流入（Free-Float Market Value Normalized Net Inflow）

- **痛点对齐**：A 股上市公司中，大量“流通股”实际上由控股母公司、国有法人或战略股东长期锁定，平时根本不在二级市场流通。传统 `circ_mv` 往往虚高。
- **自由流通市值推导**：
  $$\text{free\_float\_mv (万元)} = \text{free\_share (万股)} \times \text{close (元/股)}$$
  *(单位推导：$10,000\text{ 股} \times 1\text{ 元/股} = 10,000\text{ 元} = 1\text{ 万元}$，因此 `free_share * close` 在量纲上恒等为万元)*。
- **数学公式**：
  $$Ratio_{free\_float\_mv} = \frac{\text{net\_amount (万元)}}{\text{free\_share (万股)} \times \text{close (元/股)}}$$
- **量纲**：无量纲比例（$\%$ 或 $bp$）。
- **业务意义**：精确度量主力资金在**真实活跃交易盘**中的净吸筹或净出货比重，是量化机构衡量筹码集中的核心指标。

#### 3. 成交额归一化主力净流入（Turnover Amount Normalized Net Inflow）

- **定义**：主力资金净额占当日股票全天真实总成交额的比例。
- **数据来源与双重核验机制**：
  - **首选源**：日线行情（`daily`）的 `amount` 字段（官方单位为千元，折算万元：$amount_{wan} = amount_{qian} \div 10$）；
  - **`daily_basic` 交叉核验估算源**：根据换手率与流通市值反推的成交额基准：
    $$\widehat{amount}_{circ} = \text{circ\_mv} \times \frac{\text{turnover\_rate}}{100}$$
    $$\widehat{amount}_{free} = (\text{free\_share} \times \text{close}) \times \frac{\text{turnover\_rate\_f}}{100}$$
    *(注：由于日内成交价呈加权均价 VWAP 分布，按收盘价计算的估算成交额与交易所真实成交额存在微小价差，但可作为极佳的横向冗余校验与防伪基准)*。
- **数学公式**：
  $$Ratio_{turnover} = \frac{\text{net\_amount (万元)}}{\text{amount (万元)}}$$
- **量纲**：无量纲比例（$\%$），取值理论区间在 $[-100\%, +100\%]$ 之间。
- **业务意义**：衡量主力资金在当日多空博弈中的“主导权”。若 $Ratio_{turnover} > +30\%$，表明当日成交额近三分之一为主力主动买入，表明极其强势的单边净流入；若成交额高达数十亿但 $Ratio_{turnover}$ 仅 $1\%$，表明资金对倒激烈但无明确净方向。

#### 4. 换手率与量比联合置信度加权（Liquidity & Volume Contextual Weighting）

结合 `turnover_rate_f`（自由流通换手率）与 `volume_ratio`（量比）：
- **缩量吸筹识别**：低换手率（如 $turnover\_rate\_f < 2\%$）伴随持续中等 $Ratio_{free\_float\_mv} > 0$，反映资金隐蔽吸筹；
- **高位放量对倒识别**：极高换手率（如 $turnover\_rate\_f > 20\%$）且量比激增，但 $Ratio_{turnover}$ 趋近于 0，提示主力剧烈分歧或高位诱多出货。

---

## 三、Point-in-Time (PIT) 字段时点与时序边界约束

在多智能体系统与量化投研框架中，**时间截面的一致性与真实性（Point-in-Time, PIT）**是防范未来信息穿越（Lookahead Bias）的核心准则。本切片确立不可违背的时点约束。

### 3.1 核心纪律：`trade_date` 必须 $\le$ 分析截止日（`as_of`）

无论是实时诊断、历史事件复盘、回测（Backtest）还是离线校准（Calibration）：

1. **时间戳截断原则**：
   对于任何给定分析请求，设其指定的分析截止日为 $T_{as\_of}$。系统在向私有网关请求 `daily_basic` 时，必须强制指定：
   $$\text{end\_date} = T_{as\_of} \quad \text{或} \quad \text{trade\_date} \le T_{as\_of}$$
2. **严禁越界读取未来指标**：
   在分析 $T_{as\_of}$ 这一天的市场状况时，**绝对禁止**读取任何 $\text{trade\_date} > T_{as\_of}$ 的 `daily_basic` 记录。
3. **盘中时态与盘后发布时间窗口隔离**：
   - **数据生成客观规律**：交易所每日 15:00 收盘后，数据网关在 15:00～17:00 期间进行盘后清算，计算并落库当日的 `daily_basic`（包括当日收盘价、换手率、各口径总市值及流通市值）；
   - **盘中分析场景（$T$ 日 09:30～15:00）**：
     - 若智能体在 $T$ 日盘中运行，此时物理世界中 $T$ 日的 `daily_basic` 尚未生成；
     - 此时必须使用 $T-1$ 交易日的 `daily_basic` 作为股本与市值基准（股本在交易日之间具有高度稳定性），或在证据链中显式声明使用的是前一交易日基准，严禁使用尚未清算出来的未来截面；
   - **盘后分析与历史回放场景（$T$ 日 17:00 后或历史回测）**：
     - 允许使用 $T$ 日当天的 `daily_basic`，因为当日清算已经完成，数据已进入已知事实集。

```
时间流转与 PIT 时序栅栏：
───────────────┬───────────────────────────┬──────────────────────►
               │                           │
               ▼                           ▼
        [T 日 10:30 盘中]            [T 日 17:00 盘后]
        • 当日 daily_basic 物理未生成  • 当日 daily_basic 清算入库
        • 仅可取 trade_date <= T-1   • 允许取 trade_date == T
        • 严禁穿透读取未来收盘数据     • 严禁取 trade_date > T
```

---

## 四、核心红线剖析：为何绝对禁止用最新截面回填历史市值

在量化工程中，最容易犯下的致命错误之一是：**为了简化计算，从数据库或 API 拉取当前最新一天的股票市值/流通盘，然后直接去除历史全时段的资金流序列**。

本评估明确定义该做法为**重大架构违规**，并从四大机理揭示其危害：

### 4.1 股本变动带来的未来信息泄露（Lookahead Leakage）

上市公司的股本和流通盘在历史上并不是一成不变的，而是频繁受到以下资本运作的动态重塑：
1. **转增送股与拆分**：每 10 股送转 10 股会使流通股数翻倍；
2. **定向增发与配股**：机构配售股份限售期届满上市，导致流通股本大幅扩容；
3. **股权激励与可转债转股**：导致总股本和流通股持续微量递增；
4. **股份回购并注销**：减少上市公司总股本与流通股本。

**违规危害机理**：
假设某公司在 2024 年 6 月实施了 10 送 10，自由流通股本从 1 亿股变为 2 亿股。
- 若用 2026 年最新的 2 亿股去回填并归一化 2024 年 3 月的历史资金流：
  - 2024 年 3 月原本发生的主力净买入 5000 万元，在当时占自由流通市值的比例为 $5\%$；
  - 但因分母被未来扩容后的股本错误放大了一倍，计算出的渗透率被虚假压缩为 $2.5\%$；
  - 这意味着算法系统提前“预知并透支”了未来 6 月份的送转事件，使量化特征完全失真。

### 4.2 价格大幅变迁导致的幸存者与缩放偏差（Scaling & Survivorship Bias）

股票市值是“股价 $\times$ 股本”的乘积，股价在历史长周期中可能发生数倍乃至数十倍的上涨或下跌：
- **成长大牛股**：某新能源股票在 2020 年市值仅 50 亿元，到 2026 年成长为 2000 亿元巨头。若用 2026 年的 2000 亿市值去归一化 2020 年初的资金流，当时数十亿级别的大资金抢筹建仓（占当时市值 $20\%$ 以上的惊天异动），会被摊薄到不足 $0.5\%$，导致系统无法捕捉到最具价值的历史关键转折点；
- **熊市暴跌/退市股**：某 ST 股票市值从 500 亿元崩塌至 10 亿元。若用最新的 10 亿元市值回填，其历史上平淡无奇的 5000 万日常波动，会被错误放大为占流通市值 $5\%$ 的虚假极端异动，产生严重的假阳性（False Positive）噪点。

### 4.3 历史时态事实完整性（Audit Integrity）要求

量化多智能体系统的每一次决策推理，都必须能够经受“法庭级”的追溯审计：
- 决策报告必须证明：**在历史决策时点 $T$，智能体所依据的每一个数字（包括流通盘、收盘价和市值），都是在当时时刻真实存在且唯一的已知公开信息**；
- 采用最新截面回填历史市值，彻底破坏了回溯测试的因果律，导致模型线下表现完美，实盘上线后迅速失效崩溃。

---

## 五、缺列上报与异常分类规范（Failure Categorization & Fallback）

针对私有网关调用、网络抖动、停牌以及字段缺失等现实异常，结合仓库既有错误处理标准（`tradingagents/dataflows/providers/cn_akshare_provider.py` 中成熟的 `_tushare_error` 与类型化判定设计），确立严密的缺列上报机制。

### 5.1 类型化错误分类体系（Typed Errors）

调用私有网关 `daily_basic` 接口时，系统必须对返回进行精细化拦截与分类，禁止抛出未捕获的宽泛异常：

```
                              ┌───────────────────────────────────┐
                              │  daily_basic 接口响应与字段校验   │
                              └─────────────────┬─────────────────┘
                                                │
         ┌──────────────────┬───────────────────┼───────────────────┬──────────────────┐
         ▼                  ▼                   ▼                   ▼                  ▼
  [鉴权与传输异常]    [业务状态异常]      [结构/空行异常]     [关键列缺失异常]   [数据无效值异常]
  • token_missing     • 403/permission    • no_rows (停牌)    • missing_field    • zero_or_negative
  • transport_timeout • rate_limited      • json_shape        • (circ_mv缺失)    • nan_or_null
  • transport_error   • api_code!=0       • fields_missing    • (free_share缺失) • div_by_zero_risk
```

1. **传输与网关层错误**：
   - `tushare.daily_basic:token_missing`：运行环境缺少 `TUSHARE_TOKEN` 配置，直接拦截，禁止外发网络请求；
   - `tushare.daily_basic:permission_denied`：网关返回 403 或业务错误码 2001/2002/40101；
   - `tushare.daily_basic:rate_limited`：网关触发 429 或限频错误码 2003/40203；
   - `tushare.daily_basic:transport_timeout` / `transport_error`：网络连接超时或套接字断开。
2. **数据完整性与空行错误**：
   - `tushare.daily_basic:no_rows`：接口返回成功但 `items` 为空列表（通常因标的当日停牌、未上市或周末非交易日导致）；
   - `tushare.daily_basic:json_shape:fields_missing`：网关响应缺少 `fields` 或 `items` 根节点。
3. **关键列缺失（Missing Field Detection）**：
   - 当响应 `fields` 中缺少核心规模字段时，抛出明确的字段级错误：
     - `tushare.daily_basic:missing_field:circ_mv`
     - `tushare.daily_basic:missing_field:free_share`
     - `tushare.daily_basic:missing_field:turnover_rate`
     - `tushare.daily_basic:missing_field:close`

### 5.2 降级上报与证据链标记规范（Graceful Degradation with Evidence）

在计算规模归一化指标时，若遇到数据缺失，系统必须按以下规范实施安全降级，并必须在产出的证据链元数据中据实上报：

| 异常情况 | 降级策略 | 证据链状态与元数据记录 | 下游智能体感知 |
|---|---|---|---|
| **缺少 `free_share`**（万股） | 优雅降级为 `circ_mv`（流通市值）归一化 | `scale_normalized: true`<br>`scale_basis: "circ_mv"`<br>`scale_fallback: "free_share_missing"` | 明确知晓自由流通盘不可得，当前使用的是普通流通盘归一 |
| **缺少 `circ_mv`**（万元） | 降级为日线成交额 `amount` 归一化 | `scale_normalized: true`<br>`scale_basis: "turnover_amount"`<br>`scale_fallback: "circ_mv_missing"` | 明确知晓仅进行了成交额占比计算，无市值占比 |
| **全量规模字段均缺失或停牌** | 放弃归一化，保留原始资金流绝对金额 | `scale_normalized: false`<br>`scale_gap_reason: "no_daily_basic_rows_or_fields"`<br>`scale_missing_fields: ["circ_mv", "free_share"]` | 警示：当前为未归一化绝对金额，禁止跨标的横向比较强度 |
| **字段值为 0 或负数**（异常脏数据） | 严禁执行除法（防止 `ZeroDivisionError`），直接标记脏数据并降级 | `scale_normalized: false`<br>`scale_gap_reason: "invalid_zero_denominator"` | 告警数据质量问题，避免程序崩溃 |

---

## 六、与现有数据流架构及后续切片的对接规划

本卡作为 C-09 切片 1，严格贯彻“只读评估与规范冻结”纪律，不改动业务代码，后续演进路线规划如下：

```
[切片 1：DAV-631（本卡）] ───► 冻结 daily_basic 字段口径、PIT 约束、防回填红线与缺列上报标准（只读评估）
           │
           ▼
[切片 2：C-09-2 接口读取] ───► 在 cn_akshare_provider 封装 _fetch_tushare_daily_basic 契约与单测
           │
           ▼
[切片 3：C-09-3 归一注入] ───► 在资金流证据对象中挂载 scale_metrics 归一字段，供多智能体决策消费
```

### 6.1 拟定接入函数契约（仅规划，供下一切片参考）

```python
# 拟定契约原型（仅供架构参考，本卡不实现）：
async def _fetch_tushare_daily_basic(
    self,
    symbol: str,
    trade_date: str,  # 格式 YYYYMMDD，必须满足 trade_date <= as_of
) -> tuple[dict | None, str | None, str | None]:
    """
    从私有网关拉取指定交易日的 daily_basic 指标。
    
    返回:
        (basic_row, error_str, failure_category)
        - basic_row 包含: close, turnover_rate, turnover_rate_f, volume_ratio, 
                         free_share, circ_mv, total_mv 等
        - 发生缺少字段时上报明确的 missing_field 类型
        - 停牌或空数据时返回 no_rows
    """
    ...
```

---

## 七、合规性与六大红线逐项核验证明

资深开发1 在此严正声明，本卡工作与产出物严格符合只读卡要求：

1. **业务代码零修改**：`tradingagents/`、`api/`、`tests/`、`frontend/` 等业务代码目录保持零改动，`git status` 仅包含本评估文件；
2. **零 Token 打印与泄露**：全篇文档无任何真实 Token 字符串，无任何带敏感密钥的 URL 查询参数，遵循安全规范；
3. **未改动任何 providers 契约与代码**；
4. **未接线生产，未启停/重启任何 uvicorn 进程或后台服务**；
5. **未混入 C-04 复权引擎实现**：严格聚焦于 `daily_basic` 规模指标与归一逻辑，未引入复权数学计算；
6. **未混入 C-05 施工**：未涉及巨潮公告、新闻事件、财报预约披露等 C-05 相关模块；
7. **坚守一个 commit，push 到远端分支，输出 40 位 SHA**。
