# C-04 切片 1（只读）：dividend + raw daily 的 PIT 风险与落点评估 (DAV-667)

> **制定日期**：2026-09-05<br>
> **责任角色**：资深开发2 (`5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc`)<br>
> **基线分支**：`origin/codex/dav-4-p2a-trunk`<br>
> **基线 SHA**：`c83881809da88686c30f097b1c3872187a5733ca` (经 `git fetch origin` 严格校验)<br>
> **依据文件**：`work/2026-09-05-tushare-private-gateway-matrix.md` (DAV-618，commit `141c702`；注：任务提示中提及文件名 `work/2026-09-05-tushare-gateway-matrix.md`，经主干路径实核，仓库中实际存在且已冻结的文件名为 `work/2026-09-05-tushare-private-gateway-matrix.md`)<br>
> **唯一关注点**：冻结「如何用私有网关 `dividend` + raw daily 做 PIT/RAW，以及为何不能用当前截面 `adj_factor` 回填历史」。<br>
> **执行红线（全量遵守）**：
> 1. 严禁改动 `tradingagents/`、`api/`、`frontend/`、`tests/` 代码；
> 2. 严禁修改 Token、`.env`、`role_bindings`、`providers` 权重或配置；
> 3. 严禁启停或重启任何正在运行的 uvicorn 进程与后台服务；
> 4. 严禁实现复权计算引擎（本卡仅冻结只读评估、契约与工程纪律，不写数学复权引擎）；
> 5. 严禁接线 `dividend` / `adj_factor` / raw `daily` 业务代码；
> 6. 严禁混入 C-09-3 或 C-05 范围（C-05 采集/覆盖度诚实路径已合入主干）；
> 7. 严禁直连官方 `api.tushare.pro`，严禁在文档、日志或评论中打印任何真实 Token；
> 8. 严禁 Fast-Forward 或部署生产；严禁 push 主干；本卡评论禁止 @独立代码审核员、不要自建审核卡。

---

## 一、主干现网行情与价格通道审计：前复权 vs 未接入 Raw 收盘价通道

通过对当前主干（commit `c838818`）进行全量静态 grep 审计，现网行情通道与价格基础声明的真实代码状态如下：

### 1.1 主干代码静态 Grep 证据链（带精确路径与行号）

1. **现网行情默认硬编码为前复权（`adjust="qfq"`）**：
   - 路径：`tradingagents/dataflows/providers/cn_akshare_provider.py`
   - 行号：`829-895`（在主函数 `get_stock_daily` 中，所有 A 股/ETF 日线数据获取源均明确传入 `adjust="qfq"`）：
     - 行 `832`：`df = ak.fund_etf_hist_em(..., adjust="qfq")`（ETF 日线前复权）
     - 行 `852`：`df = ak.stock_zh_a_hist(..., adjust="qfq")`（东财主源前复权）
     - 行 `870`：`df = ak.stock_zh_a_daily(..., adjust="qfq")`（新浪备源前复权）
     - 行 `886`：`df = ak.stock_zh_a_hist_tx(..., adjust="qfq")`（腾讯备源前复权）
2. **Baostock 备用源同样硬编码为前复权**：
   - 路径：`tradingagents/dataflows/providers/cn_baostock_provider.py`
   - 行号：`84`：`adjustflag="2"`（Baostock 官方协议中 `"2"` 明确代表前复权）
3. **回测与校准服务默认价格基础标定为 `vendor_qfq`**：
   - 路径：`api/services/backtest_service.py`
     - 行 `38`：`PRICE_BASIS_VENDOR_QFQ: str = "vendor_qfq"`
     - 行 `39`：`PRICE_BASIS_UNSPECIFIED: str = "unspecified"`
     - 行 `258`：`price_basis = final_state.get("price_basis") or PRICE_BASIS_VENDOR_QFQ`
     - 行 `464`：`price_basis = analysis.get("price_basis") or PRICE_BASIS_VENDOR_QFQ`
   - 路径：`api/services/calibration_service.py`
     - 行 `34-35, 41-42`：导入并导出 `PRICE_BASIS_VENDOR_QFQ`
     - 行 `756`：`"price_basis": PRICE_BASIS_VENDOR_QFQ`
4. **单元测试锁定防伪断言**：
   - 路径：`tests/test_backtest_calibration_isolation.py`
     - 行 `407-409`：测试断言常量定义
     - 行 `411-425`：`test_single_analysis_defaults_to_vendor_qfq_and_never_raw` 严格断言：
       ```python
       assert res["price_basis"] == "vendor_qfq"
       assert res["price_basis"] != "raw"
       ```
5. **真正未复权（Raw Daily）收盘价通道尚未接入**：
   - 静态检索结果：全库 `git grep -n '"daily"' tradingagents/dataflows/providers/` 仅命中上述 AKShare 的 `period="daily"`（前复权），没有任何从 Tushare 或其他源拉取未复权日线（Raw Daily）的函数或请求入口；
   - 结论：**真正不复权 raw 收盘价通道在现网尚未接入**。

### 1.2 前复权（Vendor QFQ）vs Raw 收盘价的时序差异与 Lookahead Bias 机理

| 维度 | raw 收盘价通道（未复权 / Raw Daily） | 前复权通道（Vendor QFQ / Forward-Adjusted） |
|---|---|---|
| **物理本质** | 交易所撮合系统形成的**真实成交价格**。 | 以未来最新日为基准向前折算的**数学投影序列**。 |
| **时序不变性 (PIT)** | **严格具备时间不变性**：历史 $t$ 日价格一经成交永不改变。 | **动态漂移**：未来任意时点发生除权，历史全序列被重算缩放。 |
| **除权跳空表现** | **客观呈现物理跳空缺口**（如 10 送 10 价格从 100 元断崖至 50 元）。 | **人为平滑跳空缺口**，抹杀真实跳空。 |
| **撮合逻辑相符度** | **真实撮合**：限价单、止损线基于真实挂单簿判定。 | **虚假撮合**：前复权价格在历史上从未在撮合系统中存在。 |
| **Lookahead Bias** | **0 未来信息泄露**。 | **严重未来信息穿越**：分母依赖未来累计因子。 |

**数学证明（Lookahead Bias 泄露机制）**：
设评估时点为 $T_{eval}$，历史交易日为 $t$（$t < T_{eval}$）。前复权序列表示为：
$$P_{qfq}(t \mid T_{eval}) = P_{raw}(t) \times \frac{F(t)}{F(T_{eval})}$$
其中 $F(t)$ 为累积复权因子。分母 $F(T_{eval})$ 包含了自 $t$ 日至 $T_{eval}$ 之间发生的所有分红送转事件。若在历史 $t$ 时刻的模拟交易中向智能体提供 $P_{qfq}(t \mid T_{eval})$：
1. 算法隐式获知了未来是否会发生大比例送转或分红；
2. 均线在历史除权日不会发生真实破位，导致技术分析师智能体的止损策略无法按真实市场反应触发；
3. 基本面与新闻智能体读取的是当时 $t$ 的历史未发生公告，而技术面读取的是未来折算价，导致 Multi-Agent 决策共识崩溃。

---

## 二、`adj_factor` 风险审计：矩阵可用但未进代码，严禁回填历史

### 2.1 主干代码与网关矩阵静态 Grep 证据链

1. **`_TUSHARE_REQUEST_FIELDS` 现网代码未进 `adj_factor` 与 raw `daily`**：
   - 路径：`tradingagents/dataflows/providers/cn_akshare_provider.py`
   - 行号：`307-333`：
     ```python
     _TUSHARE_REQUEST_FIELDS = {
         _TUSHARE_DC_API: (
             "ts_code,trade_date,net_amount,buy_sm_amount,buy_md_amount,"
             "buy_lg_amount,buy_elg_amount"
         ),
         _TUSHARE_THS_API: (
             "ts_code,trade_date,net_amount,buy_sm_amount,buy_md_amount,"
             "buy_lg_amount"
         ),
         _TUSHARE_DAILY_BASIC_API: (
             "ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,"
             "free_share,circ_mv,total_mv,amount"
         ),
         _TUSHARE_FORECAST_API: (
             "ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
             "net_profit_min,net_profit_max,last_parent_net,first_ann_date,"
             "summary,change_reason"
         ),
         _TUSHARE_REPURCHASE_API: (
             "ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit"
         ),
         _TUSHARE_DISCLOSURE_DATE_API: (
             "ts_code,ann_date,end_date,pre_date,actual_date,modify_date"
         ),
     }
     ```
     静态实核：字典中包含资金流（`_TUSHARE_DC_API`, `_TUSHARE_THS_API`）、`daily_basic`、`forecast`、`repurchase`、`disclosure_date` 共 6 个接口；**明确不含 `dividend`、`adj_factor`、raw `daily`**。
   - 路径：全库静态 grep：`git grep -n "adj_factor" tradingagents/` 返回 0 条记录，证明代码库完全未接线 `adj_factor`。
2. **网关矩阵状态记录**：
   - 路径：`work/2026-09-05-tushare-private-gateway-matrix.md`
     - 行 `54`：`| adj_factor | 可用 | 未接入。后续归入 C-04。关键纪律：仅允许作为当日核验或自上线起按日归档，严禁使用当前截面最新复权因子回填历史破坏 PIT。 |`
     - 行 `82`：`| adj_factor | 未调用 | 无（后续建议 C-04 因子核验） | 暂无（尚未封装） | 尚未接入 |`
     - 行 `128-140`：明确要求「绝对禁止在回测、离线评估或历史事件回溯中，直接拿当前最新截面的 `adj_factor` 去覆写或回填历史日线」。

### 2.2 为何严禁使用当前最新截面 `adj_factor` 回填历史

即使私有网关的 `adj_factor` 接口可用，**绝对禁止全量拉取当前因子回填历史数据库**，技术根因如下：

1. **不可逆的时态污染（Irreversible Temporal Contamination）**：
   当前拉取的 `adj_factor` 序列是站在 2026-09-05 视角的最终计算结果。若将其回填历史，历史切片 $T_{hist}$（如 2024 年）将丢失“在 2024 年当时已知因子为多少”的真实 PIT 状态，构成了全知全能的作弊视角。
2. **更正与补充公告的后验性（Post-Hoc Restatements）**：
   上市公司送转方案存在预案调整、股东大会否决、实施公告延期、除权日临时变更等现实情况，供应商也常在数日后修正录入错误。直接回填抹平了市场真实经历过的信息不确定性。
3. **跨源口径不可逆偏差（Cross-Vendor Basis Mismatch）**：
   不同数据服务商对转增股、配股除权参考价计算公式中的除数取整与保留小数存在细微差异。将 Tushare 当期因子强行乘在原本来自东财的历史价格上，会引入复合乘法误差。
4. **因果解释链缺失（Loss of Causal Chain）**：
   因子仅仅是浮点数乘数，缺乏因果解释。智能体只看到价格被缩放，却无法获知送转股数与派现金额，无法形成具有逻辑说服力的研报。

### 2.3 `adj_factor` 合法使用工程纪律（二分法）

```
                         ┌────────────────────────────────────┐
                         │ Tushare 网关 adj_factor 接口       │
                         └─────────────────┬──────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    ▼                                             ▼
       【途径 1：当期横向交叉核验】                  【途径 2：自今日起按日只追加归档】
       • 仅取当日 T 截面因子                          • 每日收盘后定时拉取当日快照
       • 与外部数据源交叉比对                        • 记录 (ts_code, trade_date, adj_factor,
       • 发现因子突变即报警触发除权核实                 snapshot_date, recorded_at)
       • 【严禁】向历史库覆写                        • 查询强制约束: snapshot_date <= as_of
                                                     • 【严禁】向前回填历史历史记录
```

1. **途径 1：当期横向交叉核验（Current-Day Cross-Verification）**：
   每日收盘后仅请求当日截面因子，计算 $\Delta F_T = \frac{adj\_factor(T)}{adj\_factor(T-1)}$。若发生跳变，触发对当日除权除息公告与行情的交叉核验，告警潜在数据异常。
2. **途径 2：自今日起按日归档（Append-Only Daily Archiving）**：
   自本功能上线日起，每日定时追加归档当天快照，打上物理时间戳 `recorded_at`。当系统回放历史切片 $T_{hist}$ 时，查询条件必须硬性限定 `snapshot_date <= T_{hist} AND recorded_at <= T_{hist}`，从根源杜绝数据穿越。

---

## 三、`dividend` 接口作为除权事件旁证的 PIT 规范

### 3.1 核心字段与 PIT 关键时态特征

依据 Tushare 规范与私有网关定义，`dividend` 接口核心列名如下：

| 字段名称 | 业务含义 | PIT 时态角色 |
|---|---|---|
| `ts_code` | 股票代码 | 标的唯一识别码。 |
| `end_date` | 分红年度/基准截止日 | 会计年度截止日（**严禁用于时态截断**）。 |
| `ann_date` | 预案公告日 | 董事会首次披露分红预案日（预期形成时点）。 |
| `div_proc` | 实施进度 | 方案状态（预案/股东大会通过/实施中/完结）。 |
| `stk_div` | 每股送红股比例 (股) | 除权下折核心参数。 |
| `stk_bo_rate` | 每股转增股比例 (股) | 资本公积转增下折核心参数。 |
| `cash_div` | 每股现金分红 (元，税前) | 除息下折核心参数。 |
| `record_date` | 股权登记日 | 享权最终持仓交易日。 |
| `ex_date` | 除权除息日 | **二级市场物理价格发生跳空的交易日**。 |
| `pay_date` | 派息日 | 现金红利到账日。 |
| `imp_ann_date` | 实施公告日 | **确立确切除权除息日与方案最终落地的法定公告日**。 |

### 3.2 作除权事件旁证的 PIT 日期字段该用哪一列（按列名，禁止位置切片）

1. **除权事件物理跳空对齐列：必须使用 `ex_date`**：
   - 只有在交易日 `trade_date == ex_date` 当天，二级市场撮合成交价才会发生断崖下折；
   - 智能体在处理 Raw Daily 行情时，必须且只能将日线的 `trade_date` 与分红记录中的 `ex_date` 建立主外键对齐。
2. **PIT 信息可见性时态边界列：必须使用 `imp_ann_date`（实施公告日）**：
   - 在历史回放或回测评估到时点 $T_{eval}$ 时，只有满足 `imp_ann_date <= T_{eval}` 的分红记录，智能体才能获知其确切的 `ex_date` 和实施参数；
   - 若 $T_{eval} < imp\_ann\_date$，该次除权的具体实施尚未对市场公开，严禁将该记录中的 `ex_date` 提前注入时序；
   - 预案阶段（`ann_date <= T_{eval} < imp_ann_date`）仅能作为无确切除权日的“分红预期事件”存在，严禁触发除权价计算；
   - **严格禁止使用 `end_date` 作为时态判断依据**（同主干 C-05 契约 `tradingagents/dataflows/providers/cn_akshare_provider.py:2942` 要求）。
3. **字段访问规范：严格按列名访问，禁止位置切片**：
   - 必须通过具名键值提取（如 `row["ex_date"]`、`row["imp_ann_date"]`、`df["ex_date"]`）；
   - **绝对禁止使用位置切片索引**（例如 `row[9]`、`df.iloc[:, 9]`），防止网关因底层字段顺序变动或版本迭代引发列错位灾难（schema drift）；
   - 若返回数据中缺少 `ex_date` 或 `imp_ann_date` 等核心列，必须判定为 `schema_drift` 或 `missing_field` 并显式记录，严禁盲目按索引 fallback。

### 3.3 诚实性原则：不得把空表写成确认无分红

结合主干 C-05 巨潮与旁证的覆盖度诚实性设计（`tradingagents/dataflows/cninfo_disclosure.py:10-24, 685`；`tradingagents/dataflows/news_event_evidence.py:287, 1343`；`tradingagents/dataflows/providers/cn_akshare_provider.py:2944, 3192`）：

- **严禁把网关返回的空表（0 行）写成「确认无分红（confirmed no dividend）」**：
  网关返回空结果可能源于：
  1. 标的历史数据尚未同步或网关接口限流；
  2. 网络抖动或接口临时降级；
  3. 私有网关对特定代码 coverage 存在盲区；
  4. 标的在指定区间内确实无分红。
- **正规落地点**：
  - 当查询结果为空时，必须分类为 `collateral_empty` 或 `status="unknown"`；
  - 严禁将 `is_confirmed_empty` 置为 `True`，严禁向下游交付 `confirmed_no_dividend = True`；
  - 必须诚实向评估与智能体上下文输出“数据源未见分红记录（未确认无分红）”，防止系统在数据缺失状态下，将未捕获的除权跳空误判为基本面崩盘或恶性砸盘。

---

## 四、与 DAV-606 `price_basis` 标签体系严格对接

### 4.1 现网状态回顾与红线

在 DAV-606 中，已确立了以下核心工程规范（`api/services/backtest_service.py:38`、`tests/test_backtest_calibration_isolation.py:416`）：
- 现网由于底层依赖 AkShare / Baostock 前复权数据源，回测与校准的默认输出必须正名为 `PRICE_BASIS_VENDOR_QFQ = "vendor_qfq"`；
- 单元测试明确断言：系统输出严禁伪称 `"raw"`。

### 4.2 未接入 Raw 时不得声称 `raw` / `PIT_ADJUSTED`

**不可突破的声明红线**：
1. **未接入真正未复权日线前，严禁输出 `raw`**：
   只要当前底层数据仍然走 `ak.stock_zh_a_hist(adjust="qfq")` 或 Baostock `adjustflag="2"`，无论是单次分析、批量回测还是影子信贷评估，`price_basis` 必须诚实声明为 `"vendor_qfq"`。任何在现网前复权数据上打上 `"raw"` 标签的行为均属严重的数据造假与伪证。
2. **未建立日度只追加因子归档前，严禁声称 `PIT_ADJUSTED`**：
   `price_basis_version = "price_basis.pit_adjusted"` 仅能赋予完全基于日度快照归档引擎（强制 `recorded_at <= as_of`）重构的历史序列。在归档引擎与 raw daily 建立前，严禁声明该标签。

### 4.3 未来接入时的标准映射矩阵

| 标签值 (price_basis) | price_basis_version 映射 | 真实底层数据结构 | 准入前置条件 |
|---|---|---|---|
| `vendor_qfq` | `price_basis.vendor_qfq` | 第三方前复权日线（现网）。 | **现网默认缺省**。 |
| `raw` | `price_basis.raw` | 交易所未复权撮合价序列。 | 接入真正的 Tushare/主源 Raw Daily 通道。 |
| `pit_raw` | `price_basis.pit_raw` | **未复权日线（Raw Daily）+ 同期 `dividend` 除权事件旁证对象。** | **C-04 核心演进目标**：需接入 Raw Daily 并完成 `dividend` 事件软对齐。 |
| `pit_adjusted` | `price_basis.pit_adjusted` | 严格由日度追加因子序列重构的 PIT 调整价。 | 需建立严格的 `recorded_at` 日度归档库。 |

---

## 五、建议下一刀（只写计划，本卡不施工）

根据架构解耦与小步演进原则，针对后续切片提供客观规划建议：

### 5.1 方案比选：单独开「只读探针」vs「只接线 dividend 旁证」

- **方案 A：单独开「只读探针」卡**
  - 内容：编写独立的单测或探针工具（如 `scripts/probe_tushare_dividend_gateway.py`），对私有网关的 `dividend`、`adj_factor`、`daily` 进行只读调用测试，核验真实 HTTP 状态、业务返回码、字段列名与空表结构。
  - 优点：先探明私有网关真实环境表现，发现潜在的 schema 差异或权限限制。
  - 缺点：占用一个完整迭代卡片周期，但未向业务链路交付任何数据管道代码。
- **方案 B：直接开「只接线 dividend 旁证」卡（推荐）**
  - 内容：遵循主干 C-05 结构化旁证成熟模式（参考 `_fetch_tushare_forecast_records` 在 `cn_akshare_provider.py:2938` 的设计），在 `cn_akshare_provider.py` 中新增 `_fetch_tushare_dividend_records`，将其接入 `_TUSHARE_REQUEST_FIELDS` 与数据采集器（`data_collector.py`），同时在单测中配套 mock 测试与条件跳过的真实网关探针用例。
  - 论据：C-05 已验证了旁证接入范式的稳定性（不改动日线主链路、不涉及复权引擎），直接挂载 `dividend` 旁证风险最低且收益最高。

### 5.2 实施路线规划（三步走）

```
[切片 1：DAV-667（本卡）] ──► 冻结 PIT 风险、对比 raw 与 QFQ、确立 dividend 旁证纪律与列名规范（只读文档）
           │
           ▼
[切片 2：C-04 切片 2] ────► 实施 dividend 旁证接线（cn_akshare_provider 封装 + 单元测试覆盖，0 业务侵入）
           │
           ▼
[切片 3：C-04 切片 3] ────► 接入 Tushare 未复权日线（Raw Daily），与 dividend 联合支撑 price_basis="pit_raw"
```

---

## 六、工程红线审计与只读探针规范声明

### 6.1 六大纪律合规性终审确认

1. **未改动业务与测试代码**：
   未改动 `tradingagents/`、`api/`、`frontend/`、`tests/` 下的任何代码，`git diff` 严格限定在本文档。
2. **未改动服务与权重**：
   未改动任何 `role_bindings`、`providers` 权重分配；未改动 `.env`，未修改现网运行配置。
3. **未启停或重启服务**：
   未重启后台 uvicorn、nginx 或任何工作流进程，生产环境保持静默。
4. **未实现复权引擎**：
   严格贯彻只读约束，不编写任何数学复权计算或因子连乘引擎。
5. **未接线业务代码**：
   未在 `cn_akshare_provider.py` 中接入 `dividend`、`adj_factor` 或 raw `daily`。
6. **未混入 C-09-3 或 C-05**：
   完全聚焦于 `dividend` 与 `raw daily` 的 PIT 评估，不越界修改 C-05 公告流或 C-09 规模归一逻辑。
7. **未打印真实凭证**：
   全篇无任何真实 Token 字符串，无任何敏感 URL 凭据泄露。

### 6.2 只读探针设计规范（未来若实施探针的硬性约束）

若后续切片需要实施真实网关只读探针，输出必须遵循以下脱敏与最小化原则：
- **严禁输出**：Token、Authorization 头、完整请求体、未经脱敏的敏感内网 URL；
- **只记录五类标准元数据**：
  1. `http_status`：HTTP 状态码（如 200, 403, 404, 500）；
  2. `business_code`：Tushare 响应体的 `code` 字段（0 表示成功，非 0 为具体错误码）；
  3. `row_count`：返回记录行数（整数）；
  4. `field_names`：返回字段名列表（如 `["ts_code", "ann_date", "ex_date", ...]`），用于 schema 漂移核验；
  5. `failure_category`：标准故障归类（`provider_failure` / `collateral_empty` / `schema_drift` / `token_missing`）。
- **默认准则**：本卡完全以主干静态 grep + 现有 `work/2026-09-05-tushare-private-gateway-matrix.md` 矩阵为准，严格不向真实网关发送网络请求。
