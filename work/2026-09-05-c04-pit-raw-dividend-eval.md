# C-04 切片 1：dividend + raw daily 的 PIT 风险与落点评估 (DAV-628)

> **制定日期**：2026-09-05  
> **责任角色**：资深开发1 (`6050b57e-f551-4756-8ad9-3af522d7d4e3`)  
> **基线分支**：`agent/1/4eca31d2d738`  
> **基线 SHA**：`d6f75dddb396d35e5102c66b67a5e13f8d0650bb`  
> **依据文件**：`work/2026-09-05-tushare-private-gateway-matrix.md` (DAV-618)  
> **唯一关注点**：冻结「如何用私有网关 `dividend` + raw daily 做 PIT/RAW，以及为何不能用当前 `adj_factor` 回填历史」。  
> **六大禁止红线**：
> 1. 严禁改动 `tradingagents/` 业务代码；
> 2. 严禁在代码、配置、文档、日志或评论中记录或打印任何真实 Token；
> 3. 严禁修改 providers 契约与实现；
> 4. 严禁启停或重启任何正在运行的 uvicorn 进程与服务；
> 5. 严禁实现复权计算引擎（本卡仅冻结只读评估与契约设计，不写数学复权引擎）；
> 6. 严禁混入 C-09（`daily_basic` 规模归一等）施工范围。

---

## 一、双通道剖析：raw 收盘价通道 vs 前复权（Vendor QFQ）

在量化投研与多智能体（Multi-Agent）金融决策体系中，价格序列不仅是数值走势的记录，更是撮合交易、特征工程、均线判定与风险敞口计算的数学基础。

### 1.1 概念定义与物理真实性对比

| 比较维度 | raw 收盘价通道（未复权 / Raw Daily） | 前复权通道（Forward-Adjusted / Vendor QFQ） |
|---|---|---|
| **物理本质** | 交易所撮合形成的**真实物理成交价格**，对应历史挂单簿真实交易。 | 以最新交易日为基准点，向前折算历史价格的**数学投影序列**。 |
| **时序稳定性** | **绝对时间不变性（Immutable across Time）**。<br>历史 $T$ 日的 Close 价格一经确定，未来任何时刻读取永远恒定。 | **时间动态漂移（Dynamic Instability）**。<br>未来任意时点发生分红送转，历史所有交易日的价格均会被重新折算缩放。 |
| **除权除息表现** | 表现为**真实的除权跳空缺口**（如 10 送 10 导致股价从 100 元跌至 50 元）。 | 人为平滑掉跳空缺口，保持价格序列在除权日的连续性。 |
| **挂单撮合能力** | **完全契合真实撮合机制**。<br>真实限价单、止损单必须基于当日真实价格判定是否触发。 | **无法用于真实限价撮合**。<br>前复权价格在历史上从未真实在交易所撮合系统中存在过。 |
| **Point-in-Time (PIT)** | **原生支持 PIT**。<br>不存在任何未来信息泄露风险。 | **严重破坏 PIT**。<br>存在显式的未来信息穿越（Lookahead Bias）。 |

### 1.2 前复权在时序决策中的致命缺陷（Lookahead Bias 机制分析）

前复权计算公式如下：
设当前评估日为 $T_{eval}$，历史某交易日为 $t$（$t < T_{eval}$），该股票在 $t$ 日至 $T_{eval}$ 之间发生了一次或多次除权事件。前复权价格序列表示为：
$$P_{qfq}(t \mid T_{eval}) = P_{raw}(t) \times \frac{F(t)}{F(T_{eval})}$$
其中 $F(t)$ 为累积复权因子。

此公式暴露出前复权用于历史回溯与量化回测时的三大致命缺陷：

1. **分母未来泄露（Denominator Future Leakage）**：
   分母 $F(T_{eval})$ 取决于未来最终评估时刻的累计因子。如果回测系统在模拟历史 $t$ 时刻的交易逻辑，而输入的价格是 $P_{qfq}(t \mid T_{eval})$，那么：
   - 算法实际上提前获知了在 $t$ 到 $T_{eval}$ 之间会发生的分红送转规模；
   - 在除权除息事件发生前，历史股价已被预先缩放，导致波动率、技术形态被人为重塑。
2. **技术指标与均线失真**：
   前复权序列平滑了除权除息造成的跳空。在真实历史中，均线可能因为除权跳空而出现死叉，导致系统执行止损；但若读取了前复权，均线依然保持平滑，系统错过了当时的真实交易决策点。
3. **特征与新闻事件错位**：
   在 Multi-Agent 架构中，基本面分析员（Fundamentals Analyst）与新闻事件分析员（News Analyst）读取的公告是在历史时间点 $t$ 发生的真实事件。如果技术分析员（Technical Analyst）读取的是未来折算后的前复权价格，会导致不同智能体在时间维度上的认知脱节，造成决策共识逻辑崩溃。

### 1.3 现有系统认知误区纠正（DAV-606 关键正名回顾）

在历史代码实现中，由于数据源（AkShare、Baostock）默认调用了前复权接口（如 `ak.stock_zh_a_hist(..., adjust="qfq")` 或 Baostock `adjustflag="2"`），但部分下游服务（`backtest_service.py`、`calibration_service.py`）却将输出字段默认标记为 `"raw"`。

**DAV-606 实施了关键拨乱反正**：
- 明确指出：**严禁把第三方前复权序列误标为 raw**；
- 引入具名常量 `PRICE_BASIS_VENDOR_QFQ = "vendor_qfq"`，将回测与校准的默认输出正名为 `vendor_qfq`；
- 确立规则：只有真正未复权的原生撮合价格才能声明为 `raw`。

本卡在此基础上进一步明确架构定位：
- `vendor_qfq` 仅作为前端展示或初级非严格时序分析的妥协通道；
- 面向严格 PIT、量化回测与校准的核心链路，必须全面演进至 **raw 收盘价通道 + dividend 事件驱动旁证** 架构。

---

## 二、`adj_factor` 风险审计：为何严禁回填历史，只可核验或自今日起归档

### 2.1 Tushare `adj_factor` 接口物理行为

Tushare 兼容网关提供 `adj_factor` 接口，输出包含 `ts_code`、`trade_date`、`adj_factor`。
- 其表现形式为一个阶梯形递增序列：在无除权交易日，`adj_factor` 保持不变；在除权除息日（Ex-Dividend Date），`adj_factor` 发生跳阶放大；
- 供应商在每日收盘后更新当天的 `adj_factor`。

### 2.2 严禁拿当前最新 `adj_factor` 回填历史的四大技术原因

1. **不可逆的时态污染（Irreversible Temporal Contamination）**：
   如果在今天从网关拉取全量历史 `adj_factor` 并将其洗入本地历史数据库，这批因子实际上是“站在 2026-09-05 视角下的全知全能状态”。如果在回溯 2024 年或 2025 年的切片时读取此表，系统就失去了获知“历史那一天因子到底是几”的真实 PIT 状态。
2. **更正与补充公告的后验性（Post-Hoc Adjustments & Restatements）**：
   在证券市场实践中，上市公司的分红送转方案可能经历预案、股东大会否决、方案调整、实施公告延期、除权日调整等变数。第三方数据供应商也偶有因除权日录入错误而在数日后回溯修正因子的情况。如果回填，历史回溯将无法捕捉真实市场中出现过的不确定性与修正震荡。
3. **跨源口径不一致与不可逆偏差**：
   不同数据源（Tushare、恒生聚源、东财、通达信）对送股、配股缴款、转增股的除权基准价与除数处理口径存在细微差异。直接把当前 Tushare 的因子强行回填到原本来自东财/网易的历史价格上，会导致不可逆的乘法误差放大。
4. **与事件流证据链脱节**：
   单纯的浮点数因子是一个“黑盒乘数”，缺乏事件因果解释。如果下游智能体只看到因子从 1.5 变成 3.0，而不知道是因为每 10 股送 10 股还是因为大额派现，智能体就无法生成合规的投研推理链。

### 2.3 `adj_factor` 的合法合规使用落地规范

针对私有网关的 `adj_factor`，确立不可违背的“二分法”工程纪律：

```
                    ┌────────────────────────────────────────┐
                    │ Tushare 私有网关 adj_factor 接口       │
                    └───────────────────┬────────────────────┘
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
    【用途 1：当日横向交叉核验】                  【用途 2：自上线日起按日只追加归档】
    • 仅取当日 T 截面因子                          • 每日收盘后 17:30 拉取当日快照
    • 与 AkShare/巨潮等渠道比对                    • 严格打上 created_at 物理时间戳
    • 校验当日是否存在未申报除权                  • 查询时强制限定 created_at <= as_of
    • 发现跳变则告警或触发旁证核实                • 【绝对禁止】批量覆写历史数据
```

1. **用途 1：当期横向交叉核验（Cross-Verification for Current Day Only）**：
   - 允许在每日收盘后拉取当日 T 的 `adj_factor`，计算当日相比前一交易日是否有跳阶：
     $$\Delta F_T = \frac{adj\_factor(T)}{adj\_factor(T-1)}$$
   - 若 $\Delta F_T \neq 1.0$，表明今日发生除权除息，以此触发对今日数据源的完整性校验。
2. **用途 2：自今日（上线日）起按日归档（Daily Snapshot Archiving）**：
   - 建立只追加（Append-Only）的日度快照归档表，字段规范包含：
     `(ts_code, trade_date, adj_factor, snapshot_date, recorded_at)`；
   - 每日仅拉取并写入当日记录，**绝对禁止向前覆盖或刷新更早历史日期的记录**；
   - 当回测引擎以 `as_of = T_hist` 回放时，只允许查询 `snapshot_date <= T_hist` 且 `recorded_at <= T_hist` 的记录，从根源上杜绝时空穿越。

---

## 三、除权除息事件驱动：`dividend` 接口作为 PIT/RAW 旁证

### 3.1 Tushare `dividend` 接口核心元数据字段

Tushare `dividend` 接口提供了 A 股上市公司完整的现金分红、送股与转增股本生命周期明细：

| 字段名称 | 业务含义 | PIT 关键时态特征 |
|---|---|---|
| `ts_code` | 股票代码 | 标的唯一标识。 |
| `end_date` | 分红年度/基准截止日 | 财报所属会计期间。 |
| `ann_date` | 预案公告日 (Announcement Date) | **市场首次获知预期**的物理时间点。在此之前该信息不可见。 |
| `div_proc` | 实施进度 (预案/通过/实施/结束) | 进度状态机标识。 |
| `stk_div` | 每股送红股比例 (股) | 增加股东股数，导致除权下折。 |
| `stk_bo_rate` | 每股转增股比例 (股) | 资本公积转增，导致除权下折。 |
| `cash_div` | 每股现金分红 (元，税前) | 派发现金，导致除息下折。 |
| `cash_div_tax` | 每股现金分红 (元，税后) | 扣税后现金流。 |
| `record_date` | 股权登记日 (Record Date) | 享权利的最终持仓交易日。 |
| `ex_date` | 除权除息日 (Ex-Dividend Date) | **二级市场价格发生物理跳空的准确日期**。 |
| `pay_date` | 派息日 (Cash Payment Date) | 现金实际到账日。 |
| `imp_ann_date` | 实施公告日 | 确立准确 `record_date` 与 `ex_date` 的正式公告日。 |

### 3.2 为什么 `dividend` 是 Raw Daily 的完美解药（旁证机制）

采用 Raw Daily 时，最大的痛点在于除权日当天股价会发生结构性断崖跳空。例如某股票 10 送 10 股，价格从 100 元物理降至 50 元：
- **没有旁证时的严重后果**：基于技术分析或价格阈值的智能体会误将 50% 的除权缺口识别为崩盘砸盘、暴跌破位，从而错误触发清仓或极端看空信号；
- **引入 `dividend` 旁证时的正确处理**：
  1. 系统在回放或分析到 `ex_date` 时，并行挂载 `dividend` 证据对象；
  2. 智能体识别到：今日发生除权除息，每股送转 $stk\_div + stk\_bo\_rate = 1.0$；
  3. 智能体计算出除权理论参考价（Ex-Rights Reference Price）：
     $$P_{ref} = \frac{P_{pre\_close} - cash\_div}{1 + stk\_div + stk\_bo\_rate}$$
  4. 实际开盘价与 $P_{ref}$ 的差值才是真正的市场博弈涨跌幅（贴权或填权），从而对跌幅做出完全客观、合规的解释，既保留了物理价格真实性，又避免了决策误判。

### 3.3 时态流转与信息隔离原则

```
时间轴 t ──►
───────┬───────────────────┬────────────────────┬─────────────────────►
       │                   │                    │
       ▼                   ▼                    ▼
   [ann_date]        [imp_ann_date]         [ex_date]
  董事会预案公布        实施公告发布 (明确日期)    除权除息实施日 (物理跳空)
  -----------------  --------------------  ----------------------
  • 仅作为利好预期     • 确定确切 ex_date     • raw 价格真实跳空
  • 不改动任何价格     • 进入除权准备窗口      • 挂载 dividend 旁证
  • 不触发除权计算     • 仍不改动任何价格      • 智能体正确识别填权/贴权
```

- **在 $t < ann\_date$**：接口严禁向该时点的分析提供任何分红字段；
- **在 $ann\_date \le t < imp\_ann\_date$**：作为“分红预案事件”注入证据链，供基本面或情绪智能体参考；
- **在 $imp\_ann\_date \le t < ex\_date$**：明确除权日即将到来；
- **在 $t = ex\_date$**：作为确凿的除权因果旁证，绑定在当天的行情元数据中。

---

## 四、对接现有 `price_basis`（DAV-606）标签体系

### 4.1 现有标签与元数据体系梳理

根据代码库静态审计，现有与 `price_basis` 相关的核心文件与结构如下：

1. **服务与回测层**（`api/services/backtest_service.py` & `api/services/calibration_service.py`）：
   - 提取了具名常量：
     ```python
     PRICE_BASIS_VENDOR_QFQ: str = "vendor_qfq"
     PRICE_BASIS_UNSPECIFIED: str = "unspecified"
     ```
   - 回测记录与校准输出显式注入 `"price_basis": PRICE_BASIS_VENDOR_QFQ`；
   - 严格约束：禁止未声明口径时输出 `"raw"`，禁止把前复权误标为 raw。
2. **报告元数据与影子信贷评估**（`api/services/report_service.py`、`tradingagents/agents/utils/shadow_credit.py`、`scripts/verify_h1b_gates.py`）：
   - 支持三元组队列（Cohort Triad）：
     $$\text{Cohort} = \text{decision\_model\_version} : \text{evidence\_contract\_version} : \text{price\_basis\_version}$$
   - 现有测试用例已支持且验证了以下版本标签：
     - `"price_basis.unspecified"`（当前历史报告默认缺省）
     - `"price_basis.vendor_qfq"`（第三方前复权）
     - `"price_basis.pit_adjusted"`（严格 PIT 调整因子构建的序列）

### 4.2 C-04 raw 与 dividend 接入时的标准对接规范

为了保证下游与历史数据的无缝兼容，C-04 演进应严格复用并扩展现有标签体系：

| 标签值 (price_basis) | price_basis_version 映射 | 数据构成与口径定义 | 证据链要求 |
|---|---|---|---|
| `vendor_qfq` | `price_basis.vendor_qfq` | 来自 AkShare/Baostock 的第三方前复权日线。 | 维持现有行为，标记可能含有未来信息偏差。 |
| `raw` | `price_basis.raw` | 来自 Tushare `daily` 或未复权数据源的纯原始日线。 | 基础撮合行情，不含事件旁证。 |
| `pit_raw` | `price_basis.pit_raw` | **原始日线（Raw Daily）+ 同期 `dividend` 除权事件旁证对象。** | **C-04 核心目标口径**。<br>必须在行情元数据或证据链中附带 `dividend_events` 列表。 |
| `pit_adjusted` | `price_basis.pit_adjusted` | 仅由日度快照归档引擎基于历史 $t \le T$ 因子重构的复权序列。 | 必须能追溯每个因子的 `snapshot_recorded_at` 时间戳。 |

### 4.3 标签对接交互契约规范（未来切片遵循准则）

1. **输入输出对齐检查**：
   在回测单次分析（`_run_single_analysis`）与批量回测任务（`_run_backtest`）中，若调用方传入带有 `price_basis="pit_raw"` 或 `"raw"` 的数据，系统必须予以保留，禁止被隐式回落为 `"vendor_qfq"`。
2. **双重防伪断言**：
   - 若数据序列中存在前复权处理（例如日线收盘价与交易所原始价不符），断言 `price_basis != "raw"` 且 `price_basis != "pit_raw"`；
   - 若声明为 `pit_raw`，系统校验在所有发生除权跳空的交易日是否均绑定了有效的 `dividend` 旁证。
3. **Cohort 版本继承**：
   生成的分析报告在写入 `result_data["price_basis_version"]` 时，根据所使用的价格通道准确赋予 `"price_basis.vendor_qfq"` 或 `"price_basis.pit_raw"`，确保 H1B 门禁与影子信贷（Shadow Credit）准确区分评估队列。

---

## 五、C-04 后续切片演进路线与架构约束声明

本卡作为 C-04 切片 1，严格贯彻“只读评估与风险冻结”的工程定位，后续切片推进路线规划如下：

```
[切片 1：DAV-628（本卡）] ───► 冻结 PIT 风险、对比 raw 与 QFQ、确立 dividend 旁证机制与标签规范（只读）
           │
           ▼
[切片 2：C-04-2 数据契约] ──► 封装私有网关 Tushare daily (raw) + dividend 读取契约与单元测试（不改业务流）
           │
           ▼
[切片 3：C-04-3 链路对接] ──► 在数据提供层支持 price_basis="pit_raw" 输出，对接回测与报告生成
```

### 5.1 六大纪律合规性逐项核验结果

根据资深开发行为准则与本卡任务描述，本卡执行严格落实了六大红线：

1. **零业务代码修改**：
   未修改 `tradingagents/`、`api/`、`tests/` 下的任何代码，`git status` 显示仅新增本评估文档。
2. **零 Token 打印与泄露**：
   全篇文档无任何真实 Token 字符串，无任何敏感 URL 凭据，恪守环境变量与安全凭据管理原则。
3. **未改动 providers 契约**：
   未对现有的 `cn_akshare_provider.py`、`cn_baostock_provider.py` 等做任何接口调整。
4. **未启停或重启任何服务**：
   未重启后台 uvicorn、nginx 或任何工作流进程。
5. **未实现复权引擎**：
   恪守切片边界，严禁在当前只读评估卡内编写数学复权计算或因子运算引擎。
6. **未混入 C-09 范围**：
   文档完全聚焦于 `dividend` 与 `raw daily`，绝未引入 `daily_basic`、换手率或流通盘归一化等 C-09 议题。
