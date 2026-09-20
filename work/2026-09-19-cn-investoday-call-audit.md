# 今日投资（cn_investoday）调用审计与降费风险评估报告

**关联卡片**：DAV-1096（卡 4：今日投资降费调用审计）  
**审计类型**：只读调用审计（不执行任何代码变更、配置变更、服务重启或生产库写入）  
**审计基准**：
- 目标主线 HEAD SHA：`7a988197ef982fae95b6c9669234348e0632216a`
- 锁定解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`
- 解释器实测版本：`Python 3.10.20`
- 生产数据库只读连接：`file:/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db?immutable=1`（1742 份 reports 记录）
- 全量审计日志集：全仓 28 份服务与测试日志（全量扫描，共 1581 条匹配行）

---

## 一、全仓 28 份日志资产全景与扫描清单

对全仓及历史任务生成的全部 28 份日志进行逐行解析，统计 `investoday` 关键字匹配分布如下：

| 序号 | 日志文件路径（相对仓库根目录） | 文件大小 (Bytes) | 总行数 | investoday 相关行数 | 说明 |
|:---|:---|---:|---:|---:|:---|
| 1 | `logs/uvicorn-20260908.log` | 102,242 | 1,137 | 53 | 服务运行日志 |
| 2 | `logs/uvicorn.log` | 108,475 | 1,196 | 67 | 服务运行日志 |
| 3 | `uvicorn.log` | 310,534 | 3,652 | 197 | 根目录服务主日志 |
| 4 | `work/analysis_runs/collect_probe.log` | 30,476 | 369 | 37 | 采集探测日志 |
| 5 | `work/analysis_runs/prompt_measure.log` | 29,657 | 448 | 36 | 提示词测量日志 |
| 6 | `work/analysis_runs/run.log` | 0 | 0 | 0 | 空文件 |
| 7 | `work/dav101-service-4cf5c55.log` | 115,691 | 1,240 | 70 | 历史服务测试日志 |
| 8 | `work/dav164-service-20260816.log` | 2,836 | 31 | 0 | 历史运维日志 |
| 9 | `work/dav165-uvicorn-restart.log` | 3,242 | 46 | 0 | 历史重启日志 |
| 10 | `work/dav181-service-20260817-222548.log` | 4,806 | 65 | 1 | 仅包含链路初始化配置声明 |
| 11 | `work/dav209-service.log` | 81,117 | 983 | 37 | 招商银行验证日志 |
| 12 | `work/dav213-service.log` | 80,158 | 1,007 | 30 | 招商银行验证日志 |
| 13 | `work/dav247-uvicorn.log` | 53,203 | 779 | 30 | 部署验证日志 |
| 14 | `work/df753841-rtfull-agg.log` | 19,422 | 304 | 0 | 全量回归聚合日志 |
| 15 | `work/h1b-sample-fill-batch2.log` | 10,724 | 134 | 0 | 样本填充日志 Batch 2 |
| 16 | `work/h1b-sample-fill-batch3.log` | 11,284 | 132 | 0 | 样本填充日志 Batch 3 |
| 17 | `work/h1b-sample-fill-batch4.log` | 17,406 | 216 | 0 | 样本填充日志 Batch 4 |
| 18 | `work/h1b-sample-fill-batch5.log` | 73,781 | 978 | 0 | 样本填充日志 Batch 5 |
| 19 | `work/h1b-sample-fill-batch6.log` | 9,510 | 106 | 0 | 样本填充日志 Batch 6 |
| 20 | `work/h1b-sample-fill-batch7.log` | 15,471 | 174 | 0 | 样本填充日志 Batch 7 |
| 21 | `work/h1b-sample-fill-retry502.log` | 6,373 | 92 | 0 | 502 重试日志 |
| 22 | `work/h1b-sample-fill.log` | 4,364 | 52 | 0 | 样本填充日志 Batch 1 |
| 23 | `work/lens-300433-2026-05-06-rerun.log` | 1,316 | 16 | 0 | 案例重跑日志 |
| 24 | `work/local-service-dav98-20260809.log` | 84,359 | 739 | 44 | 本地服务测试日志 |
| 25 | `work/pytest_b4_full_regression.log` | 2,874 | 35 | 0 | 测试回归日志 |
| 26 | `work/uvicorn_batch5.log` | 1,057,326 | 10,068 | 786 | 批量运行长日志 |
| 27 | `work/uvicorn_service.log` | 327,883 | 3,525 | 193 | 服务长日志 |
| 28 | `work/v2-sample-fill.log` | 22,409 | 276 | 0 | V2 样本填充日志 |
| **合计** | **28 份文件** | **2,531,345** | **26,966** | **1,581** | **13 份有匹配，15 份为 0 匹配** |

---

## 二、今日投资调用尝试与命中统计（全景实证）

在上述 1581 条匹配行中：
- 1497 条为请求发起时的链路拓扑初始化追踪（`configured='...' chain=[...]`），声明配置包含 `cn_investoday`；
- **84 条为 `cn_investoday` 作为目标 vendor 的实际调用尝试**；
- 实际调用尝试中：**成功命中 76 次（90.48%）**，**降级/失败 8 次（9.52%）**。

### 2.1 全量方法维度调用统计矩阵

| 接口方法名 (`method`) | 所属业务分类 | 实际调用尝试次数 | 成功命中次数 (`status=hit`) | 降级/失败次数 (`status=fallback`) | 成功率 | 核心说明 |
|:---|:---|---:|---:|---:|---:|:---|
| `get_global_news` | `news_data` | 45 | **42** | 3 | 93.3% | 历史宏观新闻回溯核心支撑 |
| `get_news` | `news_data` | 23 | **22** | 1 | 95.7% | 历史个股新闻核心支撑 |
| `get_stock_data` | `core_stock_apis` | 12 | **12** | 0 | 100.0% | **全部为 `AUUSDO`**（外汇/黄金期权等外盘标的） |
| `get_fundamentals` | `fundamental_data` | 1 | **0** | 1 | 0.0% | 详见第三节（缺少 API Key 降级） |
| `get_balance_sheet` | `fundamental_data` | 1 | **0** | 1 | 0.0% | 详见第三节（缺少 API Key 降级） |
| `get_cashflow` | `fundamental_data` | 1 | **0** | 1 | 0.0% | 详见第三节（缺少 API Key 降级） |
| `get_income_statement` | `fundamental_data` | 1 | **0** | 1 | 0.0% | 详见第三节（缺少 API Key 降级） |
| `get_realtime_quotes` | `realtime_data` | 0 | **0** | 0 | - | **尝试次数为 0**（被前置 AkShare 100% 拦截） |
| `get_indicators` | `technical_indicators` | 0 | **0** | 0 | - | 未见到达尝试 |
| `get_insider_transactions`| `news_data` | 0 | **0** | 0 | - | 未见到达尝试 |
| **全量总计** | - | **84** | **76** | **8** | **90.48%** | **76 次成功命中已完全坐实** |

> **证据定案**：
> 1. 早期「今日投资仅命中 11 次」系只扫了 3 份日志得出的作废结论；全量 28 份日志实证命中为 **76 次**。
> 2. 早期「日志零成功命中故可安全移除今日投资」已被彻底证伪：在新闻与 `AUUSDO` 路径上，今日投资是不可或缺的生产数据源。

---

## 三、目标审查链路深度审计：`realtime_data` 与 `fundamental_data`

### 3.1 `realtime_data` 链路审计（`get_realtime_quotes`）

1. **链路配置与拓扑**：
   - 依据 `tradingagents/default_config.py:47`：`"realtime_data": "cn_akshare,cn_investoday,cn_fuyao"`
   - 优先顺序：第 1 位 `cn_akshare`，第 2 位 `cn_investoday`，第 3 位 `cn_fuyao`。
2. **全仓日志统计结果**：
   - 全仓日志中共发起 `get_realtime_quotes` 链调用 **13 次**。
   - 第 1 位 `cn_akshare` 命中 **13 次**（成功率 100.0%）。
   - 到达第 2 位 `cn_investoday` 的尝试次数为 **0 次**。
3. **兜底实证判定**：
   - **无兜底实证**。今日投资在实时行情链上**从未被实际调用过**，从未承担过兜底职责。
   - 原因：前置源 AkShare 在所有采样周期内均正常响应，链路未发生向后回退。

### 3.2 `fundamental_data` 链路审计（三大报表与公司基本信息）

1. **链路配置与拓扑**：
   - 依据 `tradingagents/default_config.py:45`：`"fundamental_data": "cn_fuyao,cn_akshare,cn_baostock,cn_investoday,yfinance"`
   - 优先顺序：`cn_fuyao`（第 1）→ `cn_akshare`（第 2）→ `cn_baostock`（第 3）→ `cn_investoday`（第 4）→ `yfinance`（第 5）。
2. **全仓日志统计结果**：
   - 4 个方法（`get_fundamentals`、`get_balance_sheet`、`get_cashflow`、`get_income_statement`）各发起链级调用 **63 次**，合计 **252 次**方法调用。
   - **第 1 位 `cn_fuyao` 命中 228 次（57 轮 × 4 方法，占比 90.48%）**。
   - **第 2 位 `cn_akshare` 命中 19 次（占比 7.54%）**。
   - **第 3 位 `cn_baostock` 触发 fallback 4 次**（对 ETF 报 `NotImplementedError: does not provide ... yet`）。
   - **第 4 位 `cn_investoday` 到达调用尝试 4 次**（各方法 1 次，占比 1.59%）。
3. **今日投资 4 次尝试的场景与失败分类**：
   - **发生位置**：`work/dav101-service-4cf5c55.log:285, 295, 302, 313`
   - **调用目标**：证券代码 `159763.SZ`（恒生科技 ETF），基准日 `2026-08-11`。
   - **链路传导过程**：
     - `cn_fuyao` 失败：`ValueError: [cn_fuyao] 参数错误 code=1002: Unknown thscode: 159763.SZ`；
     - `cn_akshare` 失败：`NotImplementedError: cn_akshare is temporarily unavailable for fundamentals / 资产负债表 / ...`（代理网络抖动及 KeyError）；
     - `cn_baostock` 失败：`NotImplementedError: cn_baostock does not provide ... yet.`；
     - `cn_investoday` 被触发尝试调用。
   - **失败分类与异常原因**：
     - 全部抛出：`NotImplementedError: cn_investoday 需要 API Key。请在配置中设置 investoday_api_key 或环境变量 INVESTODAY_API_KEY。`
     - **归类**：**环境配置缺失（Credentials Missing / Configuration Gap）**。在 `dav101` 运行时刻，进程环境中未注入 `INVESTODAY_API_KEY`，导致触发接口初始化检查即失败退出。
   - **最终兜底结果**：
     - 链路继续回退至第 5 位 `yfinance`；
     - `yfinance` 在 `dav101-service-4cf5c55.log:314, 321, 322, 325` 全部命中（`status=hit`），最终完成了数据采集。
4. **生产数据库验证**：
   - 经 `sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db?immutable=1"` 全量检索 1742 份历史报告：
   - `fundamentals_report` 中 `investoday` 命中数为 **0**。
   - 仅在 1 份外汇报告（`AUUSDO`）的 `smart_money_report` 建议文本中提及 `investoday` 名字，在 1 份失败回退报告（`000858.SZ`）中记录了链配置名字，**无任何基本面财务报表数据来自今日投资**。
5. **兜底实证判定**：
   - **无成功兜底实证（兜底实效为 0）**。
   - 4 次尝试全部失败，最终兜底是由 `yfinance` 完成的；其余 248 次基本面调用均在前两序位（Fuyao/AkShare）即已终结。

---

## 四、等价能力深度核对：实时快照 vs 日频指标

关于将今日投资实时行情或 AkShare 替换为 Tushare 接口的提议，必须严格核对数据契约与时间语义：

| 维度 | `get_realtime_quotes`（今日投资 / AkShare） | Tushare `daily` / `daily_basic` | 契约等价性判定 |
|:---|:---|:---|:---|
| **数据本质** | **盘中实时快照 (Real-time Spot Snapshot)** | **盘后日频结算与指标 (EOD Bar & Daily Valuation)** | ❌ **绝对不等价** |
| **时间粒度** | 毫秒/秒级最新报价、买卖盘、盘中累计成交 | 交易日维度单行聚合（日高低开收、换手率、PE/PB） | 粒度跨越（秒级 vs 天级） |
| **发布与更新时点** | 交易时间内持续推送/动态刷新 | 交易日 **15:00~17:00 盘后清算后** 批量生成 | 盘中调用时 Tushare 当日数据尚未生成 |
| **盘中核心字段** | `currentPrice`, `dataTime`, 实时盘口 | `close`, `trade_date`, `pe_ttm`, `turnover_rate` | 缺失实时盘口与最新现价 |
| **回测与历史安全** | 强制受 `snapshot_historical_refusal` 闸门拦截，历史日期直接拒答，杜绝未来信息 | 用于历史截面与日频序列分析 | 语义完全对立：一个是纯实时，一个是历史日频 |

> **等价性审查结论**：
> 1. `get_realtime_quotes` 与今日投资 `/stock/realtime` **绝对不是** `daily`/`daily_basic` 的等价替换。
> 2. `daily`/`daily_basic` 是日终结算数据，无法在盘中提供现价快照；而在历史回溯场景下，实时快照接口本就受 `snapshot_historical_refusal` 拦截。
> 3. **严禁按等价接口执行静默替换**，否则会导致盘中决策拉取不到当日价格或拿到上一个交易日的陈旧收盘价，产生重大时序错配。

---

## 五、受保护边界审查（绝对不能动）

本审计严格重申并坐实以下两个绝对保护项：

1. **`news_data` 链路中的 `cn_investoday`（严禁移除/严禁降级）**：
   - 代码铁律：`tradingagents/dataflows/interface.py:194`
     ```python
     _HISTORICAL_NEAR_WINDOW_NEWS_PROVIDER_ALLOWLIST = {
         "get_news": frozenset({"cn_akshare", "cn_investoday"}),
         "get_global_news": frozenset({"cn_investoday"}),
     }
     ```
   - 业务实证：全仓日志中今日投资在 `get_global_news` 命中 42 次，在 `get_news` 命中 22 次（合计 64 次命中）。
   - 影响：`get_global_news` 历史路径已被代码硬限定为**仅允许 `cn_investoday`**。若从 `news_data` 链中移除或修改，所有历史宏观分析均将直接报 `historical-refuse` 阻断，引发大面积报告生成失败。
2. **`AUUSDO` 贵金属/外盘标的日 K 路径（严禁移除）**：
   - 业务实证：日志中 `get_stock_data` 的 12 次成功命中**全部来自于 `AUUSDO`**。
   - 影响：国内 A 股源（AkShare / Fuyao / BaoStock）均不原生支持 `AUUSDO` 历史日 K。移除今日投资将导致该类外盘/跨市场资产数据源彻底断供。

---

## 六、降级与移除建议及风险评估

基于上述客观调用证据与契约核对，提出以下梯级处置建议：

### 6.1 链路处置建议

1. **`fundamental_data` 链路（三大报表与公司信息）**：
   - **处置建议**：**建议在后续施工卡中将 `cn_investoday` 从 `fundamental_data` 链中移除，或降级至 `yfinance` 之后（作为最末兜底）**。
   - **充分依据**：
     - 在 252 次真实链调用中，今日投资命中数为 **0**；
     - 唯一触发的 4 次尝试全因配置缺失而失败，最终全部由 `yfinance` 成功兜底；
     - 生产数据库 1742 份报告中无一条基本面数据来自今日投资；
     - 普通 A 股基本面已被 `cn_fuyao`（90.5%）和 `cn_akshare`（7.5%）高可靠覆盖；
     - 移除后可彻底消除后续可能产生的昂贵第三方财务报表按次查询账单。
2. **`realtime_data` 链路（`get_realtime_quotes`）**：
   - **处置建议**：**建议保留配置现有序位或安全移除；若移除，风险为极低**。
   - **充分依据**：
     - 日志实测 AkShare 成功率为 100%（13/13），今日投资尝试次数为 0，当前并未产生实际账单；
     - 今日投资作为第 2 位兜底源，由于 AkShare 极其稳定，从未被触发；
     - 若移除，当 AkShare 盘中偶发反爬拦截时，将直接降级到 `cn_fuyao`；需确认 Fuyao 的实时快照权限是否完备。
3. **`news_data` 与 `core_stock_apis (AUUSDO)` 链路**：
   - **处置建议**：**保持现状，绝对维持**。禁止任何改动。

### 6.2 风险评估矩阵

| 风险项 | 影响范围 | 风险等级 | 规避与防范措施 |
|:---|:---|:---:|:---|
| **误删 `news_data` 导致宏观新闻中断** | 宏观分析师、全市场新闻回溯 | 🔴 **严重** | 严格执行代码审查，白名单配置与代码过滤硬限定禁止触碰 `news_data` 及 `_HISTORICAL_NEAR_WINDOW_NEWS_PROVIDER_ALLOWLIST`。 |
| **误删 `AUUSDO` 日 K 导致外盘资产缺口** | 跨市场资产、大宗商品分析 | 🔴 **严重** | 保持 `core_stock_apis` 中今日投资对非 A 股代码的路由通道。 |
| **将 `realtime` 误替换为 `daily`** | 盘中交易决策、实时行情快照 | 🔴 **严重** | 严守时序契约门禁，禁止用盘后 EOD 接口充当盘中实时接口。 |
| **从 `fundamental_data` 移除今日投资** | 个股基本面财务报表 | 🟢 **微弱/无影响** | 实测 Fuyao/AkShare 覆盖率达 98.4%，剩余极端 ETF/个案由 `yfinance` 兜底，移除今日投资无功能损失且可锁定降费。 |

---

## 七、交回报告与自查声明

1. **调用尝试统计口径与结果**：全量扫描 28 份日志（26,966 行），今日投资实际调用尝试 84 次，命中 76 次（90.5%），失败 8 次（9.5%）。
2. **`realtime_data` 审计结果**：链调用 13 次，AkShare 100% 命中，今日投资尝试 **0 次**，无兜底实证。
3. **`fundamental_data` 审计结果**：链调用 252 次，Fuyao/AkShare 命中 247 次，今日投资尝试 **4 次**（均为 ETF `159763.SZ`），因缺少 API Key 抛错全部失败（0 命中），由 `yfinance` 兜底。无成功兜底实证。生产库 1742 份报告中命中数为 0。
4. **等价能力核对**：`get_realtime_quotes`（盘中实时快照）与 Tushare `daily`/`daily_basic`（盘后日终结算）在时点、字段、粒度及契约上**绝对不等价**，不得替换。
5. **绝对红线核对**：`news_data`（64 次命中，代码硬限定）与 `AUUSDO`（12 次命中）已显式标注严禁修改。
6. **合规声明**：本任务为只读审计，未修改任何产品代码、未修改任何业务配置、未重启线上服务、未对生产库开启可写连接。

报告文件已持久化保存在：`work/2026-09-19-cn-investoday-call-audit.md`。
