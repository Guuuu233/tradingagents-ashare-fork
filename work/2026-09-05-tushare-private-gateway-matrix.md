# Tushare 私有网关能力矩阵与现有调用覆盖报告 (DAV-618)

> **制定日期**：2026-09-05  
> **责任角色**：资深开发1 (`6050b57e-f551-4756-8ad9-3af522d7d4e3`)  
> **基线分支**：`origin/codex/dav-4-p2a-trunk`  
> **基线 SHA**：`b2f7b77bca19a9b50f0556989f06553c5b15404f`  
> **关注点**：将私有兼容网关实测结论、权限边界、字段/PIT（Point-in-Time）穿越风险及仓库内静态代码调用覆盖冻结为标准文档。  
> **执行约束**：只读审计卡，严禁修改业务代码、严禁在文档/日志中记录真实 Token 或敏感 URL 查询参数、严禁改动默认官方回落地址、严禁修改 `role_bindings`/`providers`、严禁启停当前 uvicorn 进程、严禁在当前卡混入 C-04/C-05/C-09 施工。

---

## 一、运输层与网关配置事实核验

### 1.1 环境变量支持与解析机制

经过对当前基线代码的逐行静态核对，项目已在所有 Tushare 调用入口处原生支持私有网关环境变量，其优先级与回落链路如下：

1. **资金流与财务报表模块**（`tradingagents/dataflows/providers/cn_akshare_provider.py:1946-1950`）：
   ```python
   url = (
       os.getenv("TUSHARE_API_URL", "").strip()
       or os.getenv("TUSHARE_BASE_URL", "").strip()
       or _TUSHARE_FUND_FLOW_URL  # "https://api.tushare.pro"
   )
   ```
2. **产业链宏观与期货模块**（`tradingagents/dataflows/providers/industry_linkage_provider.py:63-69`）：
   ```python
   def _get_tushare_url() -> str:
       return (
           os.getenv("TUSHARE_API_URL", "").strip()
           or os.getenv("TUSHARE_BASE_URL", "").strip()
           or _TUSHARE_DEFAULT_URL    # "https://api.tushare.pro"
       )
   ```

### 1.2 配置失误风险说明（严格防范直连公网）

- **默认回落行为**：当运行环境缺少 `TUSHARE_API_URL` 与 `TUSHARE_BASE_URL` 配置时，代码默认回落到官方公网端点 `https://api.tushare.pro`。
- **潜在配置失误风险**：在私有网关部署或受限网络拓扑下，如果环境变量未正确注入（例如 `.env` 软链接失效或部署容器漏配），请求会直接穿透至官方公网，导致凭证认证失败（401/403）、限频（429）或公网网络超时。
- **架构纪律要求**：本卡严格遵循只读约束，**不修改该默认值**。运维与容器编排层必须确保宿主与 worktree 环境均正确注入 `TUSHARE_API_URL` 或 `TUSHARE_BASE_URL`。
- **安全与凭证保护**：严格禁止在文档、测试用例、代码、日志或 Issue 评论中复述、打印或硬编码真实 Token 或带密钥的 URL 查询参数。

---

## 二、私有网关能力实测矩阵（用户冻结结论）

下表为私有兼容网关针对各核心 API 接口的真实测试结论与后续演进建议落点：

| API 接口 | 网关实测结论 | 状态与后续建议落点（仅规划，本卡不施工） |
|---|---|---|
| `trade_cal` | 可用 | **未接入**。用于交易日历核验、交易日对齐与跨源旁证。 |
| `moneyflow_dc` | 可用 | **已接入**。生产资金流核心源（东方财富口径主力净额与分单）。 |
| `moneyflow_ths` | 可用 | **已接入**。生产资金流核心源（同花顺口径主力净额与大单参考）。 |
| `adj_factor` | 可用 | **未接入**。后续归入 **C-04**。**关键纪律**：仅允许作为当日核验或自上线起按日归档，**严禁使用当前截面最新复权因子回填历史破坏 PIT**。 |
| `daily_basic` | 可用 | **未接入**。后续归入 **C-09**。用于提取成交额、自由流通股本、流通市值进行主力资金规模归一化。 |
| `dividend` | 可用 | **未接入**。后续归入 **C-04**。与原始日线（Raw Daily）联合评估分红除权时点与 PIT/RAW 时序对齐。 |
| `repurchase` | 可用 | **未接入**。后续归入 **C-05**。仅作为股票回购事件的结构化旁证，不作主数据源。 |
| `forecast` | 可用 | **未接入**。后续归入 **C-05**。仅作为业绩预告事件的结构化旁证，不作主数据源。 |
| `disclosure_date` | 可用 | **未接入**。后续归入 **C-05**。仅作为财报预约披露日期的结构化旁证。 |
| `anns_d` | **403，需卖家单独授权** | **严禁接入主链路**。全量公告主源继续坚持巨潮资讯 AKShare 链路（并行卡 C-05a），坚决不走 `anns_d`。 |
| `stk_surv` | 空结构，暂不认定可用 | **不接入**。返回字段为空或不可解析，不进入候选集。 |
| `MCP` | 暂不进生产链 | **仅记录**。评估为外部探针协议，暂不纳入生产自动化数据链路。 |

---

## 三、现有代码调用覆盖矩阵（基于代码库静态 Grep 审计）

通过对当前基线全量 Python 源码（`tradingagents/` 与 `tests/`）进行静态检索与逐行比对，代码中对 Tushare API 的调用与覆盖情况详见下表：

| api_name | 是否已调用 | 文件与对应实现函数 | 失败分类是否区分 auth/403/empty/rate-limit | 是否走 TUSHARE_API_URL |
|---|---|---|---|---|
| `moneyflow_dc` | **已调用** | `tradingagents/dataflows/providers/cn_akshare_provider.py`:<br>• `_fetch_tushare_fund_flow:2605`<br>• `_fetch_tushare_api_records:2357`<br>• `_tushare_post:2031`<br>• `_tushare_post_once:1996` | **是**。<br>• 显式区分 auth/权限（`permission_denied`，包含 2001, 2002, 40101~40103 及关键词 "权限"/"permission"/"unauthor"）；<br>• 显式区分限频（`rate_limited`，包含 2003, 40203~40206 及关键词 "频率"/"rate"/"limit" 或 HTTP 429）；<br>• 显式区分空行（`no_rows`）；<br>• 独立区分 `token_missing`, `transport_timeout`, `transport_error`, `json_shape`, `http_error` 等。 | **是**。<br>由 `_tushare_transport_post:1946` 统一解析 `TUSHARE_API_URL` / `TUSHARE_BASE_URL`。 |
| `moneyflow_ths` | **已调用** | `tradingagents/dataflows/providers/cn_akshare_provider.py`:<br>• `_fetch_tushare_fund_flow:2605`<br>• `_fetch_tushare_api_records:2357`<br>• `_tushare_post:2031`<br>• `_tushare_post_once:1996` | **是**。<br>机制与 `moneyflow_dc` 完全一致；额外严格校验 `net_amount` 与可选的 `net_d5_amount` 字段格式。 | **是**。<br>由 `_tushare_transport_post:1946` 统一解析。 |
| `balancesheet` | **已调用** | `tradingagents/dataflows/providers/cn_akshare_provider.py`:<br>• `_fetch_tushare_financial_tables:1074`<br>• `get_financial_statements_with_announce_dates:1173` | **是**。<br>调用 `_tushare_api_failure_category:1886` 分类：`permission_denied`、`rate_limited`、`api_code`；并在解析层校验 `data_missing`、`fields_items_missing`、`no_rows`（空数据）与必需的公告日字段。 | **是**。<br>通过 `_tushare_transport_post:1105` 发起请求，遵循网关环境变量配置。 |
| `income` | **已调用** | `tradingagents/dataflows/providers/cn_akshare_provider.py`:<br>• `_fetch_tushare_financial_tables:1074`<br>• `get_financial_statements_with_announce_dates:1173` | **是**。<br>分类逻辑与 `balancesheet` 一致，具备完整的结构解析与空行兜底。 | **是**。<br>通过 `_tushare_transport_post:1105` 发起请求。 |
| `cashflow` | **已调用** | `tradingagents/dataflows/providers/cn_akshare_provider.py`:<br>• `_fetch_tushare_financial_tables:1074`<br>• `get_financial_statements_with_announce_dates:1173` | **是**。<br>分类逻辑与 `balancesheet` 一致。 | **是**。<br>通过 `_tushare_transport_post:1105` 发起请求。 |
| `fut_daily` | **已调用** | `tradingagents/dataflows/providers/industry_linkage_provider.py`:<br>• `_query_tushare_api:131`<br>• `_fetch_tushare_indicator:428`<br>• `get_lme_copper_price:578` (沪铜 `CU.SHF` 备源) | **是**。<br>在 `_query_tushare_api:184-235` 中精细分类：<br>• `"token"` (Token 未配置)；<br>• `"403"` (HTTP 403 或 业务码 2001/2002/40101~40103 或 关键词 "权限"/"permission"/"403"/"unauthor")；<br>• `"rate_limited"` (HTTP 429 或 业务码 2003/40203~40206 或 关键词 "频率"/"rate"/"limit")；<br>• `"empty_rows"` (data 为空或 items 为空列表)；<br>• 独立区分 `"timeout"`, `"network_error"`, `"http_error"`, `"parse_error"`。 | **是**。<br>由 `_get_tushare_url:63` 统一解析 `TUSHARE_API_URL` / `TUSHARE_BASE_URL`。 |
| `index_global` | **已调用** | `tradingagents/dataflows/providers/industry_linkage_provider.py`:<br>• `_query_tushare_api:131`<br>• `_fetch_tushare_indicator:428` | **是**。<br>走统一的 `_query_tushare_api` 错误判定通道，区分 auth/403/empty_rows/rate_limited。 | **是**。<br>由 `_get_tushare_url:63` 统一解析。 |
| `shibor` | **已调用** | `tradingagents/dataflows/providers/industry_linkage_provider.py`:<br>• `_query_tushare_api:131`<br>• `_fetch_tushare_indicator:428` | **是**。<br>走统一的 `_query_tushare_api` 错误判定通道。 | **是**。<br>由 `_get_tushare_url:63` 统一解析。 |
| `shibor_lpr` | **已调用** | `tradingagents/dataflows/providers/industry_linkage_provider.py`:<br>• `_query_tushare_api:131`<br>• `_fetch_tushare_indicator:428` | **是**。<br>走统一的 `_query_tushare_api` 错误判定通道。 | **是**。<br>由 `_get_tushare_url:63` 统一解析。 |
| `trade_cal` | **未调用** | 无（后续建议 C-04 日历旁证） | 暂无（尚未封装） | 尚未接入 |
| `adj_factor` | **未调用** | 无（后续建议 C-04 因子核验） | 暂无（尚未封装） | 尚未接入 |
| `daily_basic` | **未调用** | 无（后续建议 C-09 规模归一） | 暂无（尚未封装） | 尚未接入 |
| `dividend` | **未调用** | 无（后续建议 C-04 事件对齐） | 暂无（尚未封装） | 尚未接入 |
| `repurchase` | **未调用** | 无（后续建议 C-05 结构化旁证） | 暂无（尚未封装） | 尚未接入 |
| `forecast` | **未调用** | 无（后续建议 C-05 结构化旁证） | 暂无（尚未封装） | 尚未接入 |
| `disclosure_date` | **未调用** | 无（后续建议 C-05 结构化旁证） | 暂无（尚未封装） | 尚未接入 |
| `anns_d` | **未调用** | 无（**严禁接入**，网关实测 403，主源坚持巨潮） | 暂无（坚决不接入） | 不走 |
| `stk_surv` | **未调用** | 无（**不接入**，网关实测空结构） | 暂无（不接入） | 不走 |

---

## 四、关联自动化测试用例覆盖清单

仓库中已沉淀成熟的自动化单测套件，全面覆盖了 Tushare 传输层抽象、环境变量、凭证缺省隔离、HTTP/业务错误码分类及数据解析契约：

1. **资金流与备源降级测试**（`tests/test_cn_akshare_backup_sources.py`）：
   - `test_tushare_dc_ths_success_keeps_transport_and_field_semantics`: 验证 `moneyflow_dc` 与 `moneyflow_ths` 响应解析、量纲转换（万元转亿元）及溯源元数据记录。
   - `test_tushare_token_missing_is_typed_and_does_not_call_network`: 验证 Token 缺失时直接返回类型化错误，禁止发起网络连接。
   - `test_tushare_typed_api_and_validation_gaps`: 验证业务错误码分类（2002 对应 `permission_denied`，40203 对应 `rate_limited`，其他非 0 对应 `api_code`）。
   - `test_tushare_http_and_json_failures_are_typed`: 验证 HTTP 500、网络异常及 JSON 解析失败的类型化判定。
   - `test_individual_fund_flow_uses_tushare_before_legacy_when_configured`: 验证配置 Tushare Token 时的链式优先级调度。
   - `test_tushare_ths_daily_row_does_not_require_optional_d5`: 验证同花顺资金流行结构对非必要 5 日均额字段的容忍性。
2. **财务报表公告日截断测试**（`tests/test_financial_announce_cutoff.py`）：
   - `test_tushare_financial_tables_error_handling`: 验证三张报表调用在 Token 缺失或传输异常时的错误归类。
   - `test_tushare_financial_tables_valid_envelope_parsing`: 验证对 Tushare 数据 Envelope（fields/items）的解析并映射为规范中文表结构。
3. **资金流证据链与仲裁测试**（`tests/test_fund_flow_evidence.py` & `tests/test_fund_flow_lg_credibility.py`）：
   - 验证多源资金流在 Tushare、东财公开源与同花顺即时源发生冲突时的可信度排序、大单阈值过滤与投票共识机制。
4. **产业链宏观与标的采集测试**（`tests/test_industry_linkage_provider.py`）：
   - `test_tushare_fut_daily_lc_gfe_success`: 验证商品期货（如碳酸锂 LC.GFE）日线调用与趋势计算。
   - `test_tushare_index_global_spx_success`: 验证标普 500（SPX）全球指数行情拉取。
   - `test_tushare_shibor_3m_success`: 验证 Shibor 3M 利率行情采集。
   - `test_tushare_shibor_lpr_1y_success`: 验证 LPR 1Y 利率指标采集。
   - `test_tushare_token_missing_categorization`: 验证 Token 缺失时的 `"token"` 分类。
   - `test_tushare_permission_denied_403_categorization`: 验证 403 权限拒绝分类。
   - `test_tushare_rate_limit_categorization`: 验证 429 与频率限制分类。
   - `test_tushare_empty_rows_categorization`: 验证空行（`"empty_rows"`）捕获。
   - `test_tushare_token_safety_and_provenance_integrity`: 验证全链路严禁泄露 Token，确保 Provenance 证据链安全。

---

## 五、PIT 核心风险审计：`adj_factor` 截面污染防范

在量化金融体系与事件驱动决策中，**时间截面真实性（Point-in-Time, PIT）**是防范未来信息穿越（Lookahead Bias）的基石。

### 5.1 风险机理与技术陷阱

1. **`adj_factor` 的本质**：Tushare 接口返回的复权因子是基于全历史分红送转事件计算的除数/乘数累计序列。其当前交易日返回的历史因子已包含了历史上所有发生过的权益除权变动。
2. **前复权未来泄露（Lookahead Leakage）**：
   - 如果使用当前最新的前复权因子去折算历史价格，那么在除权日之前，历史价格已经被未来发生的分红送转稀释折算；
   - 决策模型在回溯 T 日时，若读取到了“已被 T+30 日分红事件修正过”的前复权价格，就等于提前获知了未来的分红送股动作；
   - 这会导致均线死叉被抹平、波动率失真、技术形态与新闻事件完全错位。
3. **后复权与跨源失真**：即使采用后复权，不同源（如东财 vs 恒生 vs Tushare）因选取的基准日或配股交割计价方式不同，其绝对价格不可比。

### 5.2 严谨架构约束准则

针对 `adj_factor`，确立以下不可违背的工程原则：

1. **禁止直接回填**：**绝对禁止在回测、离线评估或历史事件回溯中，直接拿当前最新截面的 `adj_factor` 去覆写或回填历史日线。**
2. **仅作当期核验**：当前仅允许用于实时/当日截面的除权核对。
3. **建立日度快照归档机制**：若要用于历史回放，必须自系统部署上线之日起，按交易日日度归档并打上时间戳存储（Daily Snapshot），仅允许读取 $T \le t$ 时刻已知的复权因子。
4. **RAW 原始价格对齐**：在 C-04 中，坚持以未复权的真实行情（Raw Daily）作为底层价格标准，结合 `dividend` 显式事件驱动处理除权跳空，杜绝黑盒隐式复权。

---

## 六、建议后续演进路线（单卡单关注点推进）

本卡仅进行能力冻结与静态审计，绝不进行任何业务代码施工。建议后续按以下次序单卡推进：

```
[本卡 DAV-618 冻结网关矩阵]
           │
           ▼
[第一刀：C-04 专项卡] ───► dividend + raw daily 评估与 PIT/RAW 对齐，严格隔离复权穿越
           │
           ▼
[第二刀：C-09 专项卡] ───► daily_basic 规模指标接入 (成交额/流通盘/流通市值)，用于资金流规模归一
           │
           ▼
[第三刀：C-05 专项卡] ───► 接入 forecast / repurchase / disclosure_date 结构化旁证 (在巨潮契约稳定后)
```

1. **第一刀（C-04 评估卡）**：
   - 目标：评估 `dividend`（分红送转）与未复权日线（Raw Daily）的对齐机制。
   - 范围：建立明确的除权日判定与无未来信息的 PIT 数据集，确定因子归档规则。
2. **第二刀（C-09 规模归一卡）**：
   - 目标：接入 `daily_basic` 接口。
   - 范围：提取 `turnover_rate`（换手率）、`volume_ratio`（量比）、`free_share`（自由流通股本）、`circ_mv`（流通市值），对 `moneyflow_dc` 与 `moneyflow_ths` 主力净流入进行市值/自由流通盘归一化，解决大盘权重股与小微盘股资金流不可比的问题。
3. **第三刀（C-05 结构化旁证卡）**：
   - 目标：接入 `forecast`（业绩预告）、`repurchase`（股票回购）、`disclosure_date`（预约披露日）。
   - 前提条件：必须等待巨潮全量公告与 IR 契约（C-05a 并行卡）测试稳定且定型。
   - 定位：仅作为结构化元数据旁证（Side-Evidence），严禁替代巨潮成为公告主源。

---

## 七、合规性与系统状态声明

资深开发1 在此严正声明，本卡交付物完全符合只读要求：

1. **未修改任何业务代码**：`tradingagents/`、`api/`、`frontend/` 等业务代码目录保持零改动，`git status` 仅包含新增文档。
2. **未修改任何配置文件与凭证**：`.env` 与 `.env.example` 保持零改动，文档中绝无泄漏任何真实 Token。
3. **未修改服务运行状态**：未启停、未重启当前 uvicorn 实例或后端后台进程。
4. **未开启策略加权**：未在模型评分、决策权重中开启或修改任何权重参数。
5. **未执行部署操作**：未对任何生产或预发服务执行构建或发布上线操作。
