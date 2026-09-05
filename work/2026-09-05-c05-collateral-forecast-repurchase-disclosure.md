# C-05 旁证切片 1（只读）：forecast / repurchase / disclosure_date 结构化旁证评估与挂载契约 (DAV-632)

> **制定日期**：2026-09-05  
> **责任角色**：资深开发2 (`5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc`)  
> **关联 Issue**：DAV-632 (`01a06fe3-e888-7e2a-bc99-ce95bccba2df`)  
> **基线分支**：`agent/2/a3830afc78c6`  
> **基线核验**：主干已合入 C-05d `b42bb50893ec135c9f365f98f12255a18728f696` 及 C-04 `d4d145fae714a21bd919fad3ad66dba7fa1ae852`。  
> **依据文件**：`work/2026-09-05-tushare-private-gateway-matrix.md` (DAV-618) 及已合入巨潮 Envelope 契约（`tradingagents/dataflows/cninfo_disclosure.py` 与 `tradingagents/dataflows/news_event_evidence.py`）。  
> **唯一核心关注点**：冻结 `forecast`（业绩预告）、`repurchase`（股票回购）、`disclosure_date`（财报预约披露日期）三张私有兼容网关表如何作为**结构化旁证（Structured Collateral Evidence）**挂载到巨潮主源，而不是替代 CNINFO AKShare 全量公告主源。  
> **六大禁止红线**：  
> 1. **严禁修改 `tradingagents/` 业务代码**（本卡为只读评估与契约冻结卡，全量业务代码零改动）；  
> 2. **严禁接线 `anns_d`**（网关实测 `anns_d` 返回 403 需单独授权，主源坚持巨潮，坚决不接入）；  
> 3. **严禁修改聚类算法、PDF 与内容资格核验逻辑**；  
> 4. **严禁把旁证空表写成「确认无公告」或「全市场无新闻」**；  
> 5. **严禁在代码、配置、文档、日志或评论中记录或打印任何真实 Token 与敏感 URL**；  
> 6. **严禁触发 Fast-Forward (FF) 合并或执行部署操作**。

---

## 一、主源与旁证的定位分工与架构边界

在 A 股上市公司事件驱动与多智能体（Multi-Agent）决策架构中，必须清晰界定**法定披露主源**与**专题结构化旁证**的职责边界，杜绝“主次颠倒”或“以偏概全”引发的系统性认知风险。

### 1.1 巨潮主源（Primary Source）的法定核心地位

依据中国证监会规定，巨潮资讯网（CNINFO）是法定的上市公司信息披露指定官方载体：

1. **法定完整性与权威性**：
   - 巨潮资讯 AKShare 链路（`cninfo_announcement` 与 `cninfo_ir_survey`）覆盖 A 股全市场法定披露，包含上市公司董事会决议、定期报告、临时公告、交易所问询函等全量事件；
   - 具备巨潮官方唯一原生 `announcementId`，派生权威的 `canonical_event_id = "cninfo:{announcementId}"`；
   - 具备官方 PDF 原文下载地址（`adjunctUrl`）与基于正文内容的 SHA256 哈希核验机制（C-05b，`content_status: 'hashed'`）。
2. **事件存在性判断标准**：
   - 巨潮主源是判断“某时段内上市公司是否存在法定披露事件”的唯一主数据源；
   - 只有巨潮主源在指定标的与时间窗口内显式返回空表且网络状态为 `ok` 时，系统才记录 `cninfo_status: "confirmed_empty"`（注：即便如此，仍严格限定仅代表巨潮官方披露在该区间为空，不可外推为全市场无媒体新闻）。

### 1.2 私有网关三张表的定位：纯结构化旁证（Structured Collateral / Side-Evidence）

私有网关中 `forecast`、`repurchase`、`disclosure_date` 三张表的工程定位如下：

| 表名称 | 业务含义 | 网关实测状态 | 架构定位 | 产出核心价值 |
|---|---|---|---|---|
| `forecast` | 业绩预告 | 可用 | **结构化旁证** | 净利润变动幅度上下限（`p_change_min/max`）、净利润区间（`net_profit_min/max`）、预告类型（`type`） |
| `repurchase` | 股票回购 | 可用 | **结构化旁证** | 回购进度（`proc`）、回购金额/股数（`amount`/`vol`）、回购价格上下限（`high_limit`/`low_limit`） |
| `disclosure_date` | 财报披露计划 | 可用 | **结构化旁证** | 预约披露日期（`pre_date`）、实际披露日期（`actual_date`）、日期修正记录（`modify_date`） |

这三张表被定义为“结构化旁证”，其核心内涵包含三层约束：

1. **非主数据源（Not Primary Source）**：
   - 它们仅是第三方数据商从部分公告中二次提炼出来的结构化专题指标切片；
   - 它们绝对不是全量公告库，其收录范围、提取规则、清洗时滞完全取决于供应商，不能代表上市公司的全量披露状态。
2. **非全文替代品（Not Fulltext Replacement）**：
   - 它们只提供特定的数值型或枚举型字段，缺乏公告全文上下文、保荐机构意见、审计师意见及风险提示；
   - 决策智能体绝不能仅凭旁证表的几列数字就替代对巨潮公告原文的阅读与资格审核。
3. **主源的增强挂载物（Augmentative Attachment）**：
   - 当巨潮主源检索到一条业绩预告公告时，巨潮公告证明了“事件发生”并提供权威全文，而 `forecast` 旁证表则提供免去 LLM 复杂正则解析的“结构化净利润指标”，两者以“主从挂载”形态协作。

### 1.3 严禁接线 `anns_d`，坚决不以 Tushare 替代巨潮主源

在 `work/2026-09-05-tushare-private-gateway-matrix.md` 中已完成实测核验并确立架构纪律：

1. **`anns_d` 实测 403 阻断**：
   - Tushare `anns_d` 接口在私有网关实测返回 403（需要卖家单独付费授权），且第三方维护的公告全量流存在不可控的爬取遗漏与清洗黑盒；
   - 严禁将 `anns_d` 接入主链路，全量公告主源坚决且唯一走巨潮资讯 AKShare 链路。
2. **防范数据源依赖锁定与单点瘫痪**：
   - 若将主源替换为第三方商业网关，一旦出现积分变动、接口下线或网关网络波动，整个多智能体系统的事件感知将全面停摆；
   - 巨潮 AKShare 链路已在 C-05a~d 完成了严格的 `CninfoDisclosureEnvelope` 封装、`query_manifest` 与 `recall_gap` 诚实化，具有极高的稳定性与透明度。

---

## 二、三张网关表字段剖析、时态语义与严格 PIT 截断机制

在量化投研与事件驱动分析中，**Point-in-Time (PIT)** 是防范未来信息穿越（Lookahead Bias）的核心纪律。三张私有网关表均包含多个日期字段，必须逐一拆解其物理含义与时序截断规则。

### 2.1 `forecast`（业绩预告）时态语义与 PIT 规范

#### 2.1.1 字段清单与物理含义

依据 Tushare 官方文档 ([tushare.pro/document/2?doc_id=45](https://tushare.pro/document/2?doc_id=45)) 与网关实测，`forecast` 接口字段结构如下：

| 字段名称 | 类型 | 物理含义 | 时态属性 | PIT 截断资格 |
|---|---|---|---|---|
| `ts_code` | str | 股票代码（如 `000001.SZ`） | 标识符 | - |
| `ann_date` | str | **公告日期**（`YYYYMMDD`） | **真实物理发布时点** | **唯一合法截断基准** |
| `end_date` | str | **报告期**（如 `20241231`） | **业务会计周期** | **严禁作为截断基准** |
| `type` | str | 预告类型（预增/预减/扭亏/首亏/略增等） | 状态指标 | - |
| `p_change_min` | float | 净利润变动幅度下限（%） | 预测指标 | - |
| `p_change_max` | float | 净利润变动幅度上限（%） | 预测指标 | - |
| `net_profit_min` | float | 净利润下限（万元） | 预测指标 | - |
| `net_profit_max` | float | 净利润上限（万元） | 预测指标 | - |
| `last_parent_net` | float | 上年同期归母净利润（万元） | 历史基准 | - |
| `first_ann_date` | str | **首次公告日**（`YYYYMMDD`） | 业务溯源时点 | 仅供审计，不可作截断 |
| `summary` | str | 业绩预告摘要 | 文本摘要 | - |
| `change_reason` | str | 业绩变动原因 | 文本说明 | - |

#### 2.1.2 PIT 核心陷阱与穿越机理

1. **报告期 `end_date` 混淆陷阱**：
   - 示例：某上市公司 `end_date = "20241231"`（2024 年年报），其实际发布业绩预告的日期是 `ann_date = "20250125"`；
   - 若回测或离线决策在历史时间点 $T = \text{2024-12-31}$ 执行，若使用 `end_date <= "20241231"` 进行检索，就会在 2024 年底提前获知 25 天后才披露的业绩预告，构成严重的**未来信息泄露**；
   - **铁律**：`end_date` 仅仅代表财务核算周期，绝对不能用于决定记录在时点 $T$ 是否可见。
2. **预告多次修正与 `first_ann_date` 陷阱**：
   - 上市公司发布业绩预告后，常因年审进展发生“业绩预告修正”（如由“预增 50%”修正为“首亏”）；
   - 在修正记录中，`first_ann_date` 为首次披露日，而最新修正内容的 `ann_date` 晚于首次披露日；
   - 若系统站在历史时点 $T$（处于首次披露与修正之间），必须仅能读取截至 $T$ 已披露的原始预告，严禁将未来修正后的 `net_profit_min` 倒填给历史。

#### 2.1.3 严格 PIT 过滤算法

在历史时点 $T_{as\_of}$ 读取 `forecast` 旁证数据，必须执行如下算法：

$$\mathcal{D}_{\text{visible}}(T_{as\_of}) = \left\{ r \in \text{forecast} \;\middle|\; r.\text{ts\_code} = \text{symbol} \;\land\; \text{parse\_date}(r.\text{ann\_date}) \le T_{as\_of} \right\}$$

对同一报告期 `end_date` 存在多次公告的，按 $\text{ann\_date}$ 取最新的已知状态：

$$r^*(T_{as\_of}, \text{end\_date}) = \arg\max_{r \in \mathcal{D}_{\text{visible}}, r.\text{end\_date}=\text{end\_date}} \text{parse\_date}(r.\text{ann\_date})$$

---

### 2.2 `repurchase`（股票回购）时态语义与 PIT 规范

#### 2.2.1 字段清单与物理含义

依据 Tushare 官方文档 ([tushare.pro/document/2?doc_id=124](https://tushare.pro/document/2?doc_id=124)) 与网关实测，`repurchase` 接口字段结构如下：

| 字段名称 | 类型 | 物理含义 | 时态属性 | PIT 截断资格 |
|---|---|---|---|---|
| `ts_code` | str | 股票代码（如 `600519.SH`） | 标识符 | - |
| `ann_date` | str | **公告日期**（`YYYYMMDD`） | **真实物理发布时点** | **唯一合法截断基准** |
| `end_date` | str | 截止日期 / 实施截止日 | 业务执行跨度 | **严禁作为截断基准** |
| `proc` | str | 回购进度（预案/股东大会通过/实施中/完成等） | 阶段状态 | - |
| `exp_date` | str | 过期日期 / 方案有效期届满日 | 未来预期时点 | **严禁作为截断基准** |
| `vol` | float | 回购数量（股或万股） | 规模指标 | - |
| `amount` | float | 回购金额（元或万元） | 规模指标 | - |
| `high_limit` | float | 回购最高限价（元） | 价格约束 | - |
| `low_limit` | float | 回购最低限价（元） | 价格约束 | - |

#### 2.2.2 PIT 核心陷阱与状态演进机理

1. **长周期状态演进特征**：
   - 股票回购不是瞬间事件，而是一个跨度长达 3~12 个月的持续业务流：
     $$\text{董事会预案} \longrightarrow \text{股东大会审议通过} \longrightarrow \text{首次实施回购} \longrightarrow \text{每月进展披露} \longrightarrow \text{实施完成 / 期限届满}$$
   - 每一阶段进展均伴随一份独立的上市公司临时公告，其 `ann_date` 逐步递增。
2. **未来完成态与累计金额穿越陷阱**：
   - 在 `proc = "完成"` 的最终记录中，`amount` 记录的是数月内累计回购的总金额（例如 10 亿元）；
   - 若系统在董事会预案发布日（仅提议回购 5~10 亿元）进行回溯，如果读取到了未来发布的回购完成记录，就会产生“提前获知回购必定顶格完成”的前瞻偏差；
   - **铁律**：回购记录必须严格按 `ann_date <= T_{as\_of}` 进行截断切片，在预案期仅能感知预案阶段的拟回购上下限，绝不能读取未来实施进度中的实际已回购金额。

---

### 2.3 `disclosure_date`（财报披露计划）时态语义与 PIT 规范

#### 2.3.1 字段清单与物理含义

依据 Tushare 官方文档 ([tushare.pro/document/2?doc_id=162](https://tushare.pro/document/2?doc_id=162)) 与网关实测，`disclosure_date` 接口字段结构如下：

| 字段名称 | 类型 | 物理含义 | 时态属性 | PIT 截断资格 |
|---|---|---|---|---|
| `ts_code` | str | 股票代码（如 `000001.SZ`） | 标识符 | - |
| `ann_date` | str | **最新披露公告日 / 变更公告日** | **真实物理发布时点** | **唯一合法截断基准** |
| `end_date` | str | 报告期（如 `20241231`） | 会计周期 | **严禁作为截断基准** |
| `pre_date` | str | **预计披露日期（预约披露日）** | **面向未来的公开计划** | **不可作为截断基准** |
| `actual_date` | str | **实际披露日期** | **后验事实时点** | **【严重未来信息陷阱】** |
| `modify_date` | str | 披露日期修正记录 / 变更日期 | 历史变更轨迹 | 辅助审计 |

#### 2.3.2 致命陷阱：`actual_date` 绝对禁止用作历史已知特征

1. **`actual_date` 的后验本质**：
   - `pre_date` 是交易所与上市公司在年初或季度初公布的“预约披露日”；
   - `actual_date` 是财报最终真实在交易所网站刊登出来的日期；
   - 在时点 $T < \text{actual\_date}$ 时，整个金融市场没有任何人能够提前确知实际披露日（上市公司可能临时由于审计障碍申请延期披露，也可能预约 4 月 28 日但提前到 4 月 20 日）；
   - **灾难性后果**：如果在回溯中允许系统读取 `actual_date`，模型就会利用这个特征构建“延期披露必暴雷”的作弊因子，彻底摧毁量化回测的真实性。
2. **变更时序切片规则**：
   - 上市公司变更预约披露日时，均需发布“关于变更定期报告披露日期的公告”；
   - 只有当变更公告的 `ann_date <= T_{as\_of}` 时，更新后的 `pre_date` 才能被时点 $T_{as\_of}$ 识别。

---

## 三、与 `canonical_event_id` 的关联契约：只能作为旁证，严禁发明巨潮 ID

### 3.1 巨潮 `canonical_event_id` 的权威性与纯洁性准则

在 C-05c (`d6f75dd`) 与 C-05d (`b42bb50`) 中，系统确立了严格的 `canonical_event_id` 契约：

```python
# tradingagents/dataflows/news_event_evidence.py
def cninfo_record_to_evidence(record: Any, default_entity: str = "") -> NewsEvidence | None:
    """Convert a CninfoDisclosureRecord into NewsEvidence, copying canonical_event_id verbatim.
    
    Enforces contract (C-05c / DAV-625):
    - Only copy CninfoDisclosureRecord.canonical_event_id verbatim.
    - Strictly forbids inventing canonical_event_id from title or hash.
    """
```

巨潮官方原生 `announcementId` 是巨潮信息披露系统的不可篡改主键，`canonical_event_id` 规范格式为：
$$\text{canonical\_event\_id} = \texttt{cninfo:\{announcementId\}}$$

**核心红线**：
1. 只有巨潮官方返回明确的 `announcementId` 时，才允许派生 `cninfo:...`；
2. **严禁通过标题文本哈希（如 MD5/SHA256）、URL 或其他拼凑规则“无中生有”发明 `canonical_event_id`**；
3. 没有官方 ID 的记录，其 `canonical_event_id` 必须严格保持为 `None`。

### 3.2 为什么私有网关表绝对不能拥有 `cninfo:` 前缀的 ID

私有网关的 `forecast`、`repurchase`、`disclosure_date` 接口来源于 Tushare 的结构化衍生库，其物理特征决定了它们绝不能使用 `cninfo:` ID：

1. **接口返回无巨潮底层 `announcementId`**：
   - Tushare 接口仅返回 `ts_code`、`ann_date`、`summary` 等字段，并不透传巨潮底层的原生 `announcementId`；
2. **ID 命名空间隔离原则**：
   - 若为了让旁证数据“看起来像公告”而人为构造一个 `cninfo:fake_123` 或 `cninfo:hash(title)`，会造成与真实巨潮公告的主键冲突、哈希碰撞，并导致下游 PDF 校验（C-05b）发起错误的官方下载请求，引发 404 崩溃；
3. **法律与审计可信度责任分离**：
   - `cninfo:` 前缀在系统中代表着法定的证据链资格（可下载官方附件、可验证 SHA256 哈希）；
   - 私有网关表是第三方清洗数据，绝不能冒充官方直连数据。

### 3.3 结构化旁证安全关联契约设计（软对齐挂载机制）

为使结构化旁证能够赋能决策，同时严格遵守 ID 隔离与只读契约，确立如下**挂载（Attachment）契约**：

```
巨潮主源 Envelope (Primary Evidence)
┌──────────────────────────────────────────────────────────────┐
│ canonical_event_id: "cninfo:1221849382"                      │
│ title: "关于2024年年度业绩预告的公告"                        │
│ announced_at: "2025-01-20 18:30:00"                          │
│ source_type: "cninfo_announcement"                           │
│ content_status: "hashed"                                     │
│ adjunct_url: "http://static.cninfo.com.cn/...pdf"            │
│                                                              │
│ ┌── 挂载的结构化旁证 (Collateral Evidences) ────────────────┐ │
│ │ collateral_id: "tushare:forecast:000001.SZ:20250120"      │ │
│ │ canonical_event_id: None (严禁发明巨潮ID)                 │ │
│ │ source_type: "tushare_forecast"                           │ │
│ │ payload: {                                                │ │
│ │   "type": "预增",                                         │ │
│ │   "p_change_min": 45.0,                                   │ │
│ │   "p_change_max": 55.0,                                   │ │
│ │   "net_profit_min": 15000.0                               │ │
│ │ }                                                         │ │
│ └───────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

#### 3.3.1 旁证对象独立数据结构规范

未来实现时，旁证数据应封装为专用的只读结构体（如 `CollateralRecord`），明确与 `CninfoDisclosureRecord` 区分：

```python
@dataclass(frozen=True)
class CollateralRecord:
    """Read-only structured collateral evidence from private gateway.
    
    Enforces DAV-632 contract:
    - canonical_event_id is ALWAYS None (strictly forbids inventing cninfo ID).
    - collateral_id carries a dedicated vendor namespace.
    """
    symbol: str
    ann_date: str                     # YYYY-MM-DD
    source_type: str                  # 'tushare_forecast' | 'tushare_repurchase' | 'tushare_disclosure_date'
    collateral_id: str                # e.g. "tushare:forecast:000001.SZ:20250120:20241231"
    payload: dict[str, Any]           # Structured numeric/enum metrics
    canonical_event_id: None = None   # MUST be None
```

#### 3.3.2 软对齐挂载规则（Soft Alignment Rules）

当事件流处理器将巨潮主事件与旁证集合汇聚时，依据以下三要素执行弱对齐：

1. **标的对齐**：`primary.symbol == collateral.symbol`；
2. **日期容差窗口**：
   $$|\text{primary.announced\_at.date} - \text{collateral.ann\_date}| \le \text{tolerance (默认 1 日)}$$
   （注：考虑到巨潮晚间公告可能在次日交易日被第三方供应商记录，允许 $\pm 1$ 日时窗对齐）；
3. **主题特征对齐**：
   - 巨潮公告标题命中“预告/业绩/中报/年报” $\longleftrightarrow$ `tushare_forecast`；
   - 巨潮公告标题命中“回购/股份变动” $\longleftrightarrow$ `tushare_repurchase`；
   - 巨潮公告标题命中“定期报告披露日期/变更披露日期” $\longleftrightarrow$ `tushare_disclosure_date`。

#### 3.3.3 主源未命中时的独立留存规则

如果特定时间窗口内巨潮主源拉取失败或未检索到对应公告，但私有网关存在旁证记录：
- 旁证记录**可以作为独立的 `CollateralRecord` 留存**供基本面分析员参考；
- 但其 `canonical_event_id` **仍然必须为 `None`**；
- 严禁将独立的旁证记录伪装为“官方全量公告”，其展示标签必须明确标注为 `[结构化旁证]`。

---

## 四、失败隔离与 Gap 记录体系：403 / 空表 / 缺列的诚实化处理

延续 C-05d (`b42bb50`) 与 DAV-627 确立的“诚实化 Gap 语义”体系，严谨规定私有网关表在出现各类异常与空态时的分类判定与 `recall_gap` 记录准则。

```
私有网关接口调用结果
       │
       ├─► 1. 认证/权限失败 (403, 2001, 2002, 40101~40103, Token未配)
       │      └──► 判定为 provider_failure / permission_denied
       │            └──► 记入 recall_gap（明确不可验证，绝非无数据，禁止打印Token）
       │
       ├─► 2. 接口返回成功但 0 行 (code=0, data/items 为空)
       │      └──► 判定为 collateral_empty
       │            └──► 【绝对红线】严禁输出「确认无公告」或「全市场无新闻」！
       │            └──► 仅记为该垂直表无记录，巨潮主源独立评估
       │
       └─► 3. 字段缺失/格式漂移 (缺少必要列、类型无法解析)
              └──► 判定为 schema_drift / unverifiable
                    └──► 单行剔除防污染，记录缺失列 gap，严禁静默吞掉或脑补默认值
```

### 4.1 `403` / 权限拒绝 / Token 缺失：记入 `provider_failure`

在多环境运行与网关权限受限拓扑下，`forecast`、`repurchase`、`disclosure_date` 可能遇到权限拒绝：

1. **判定条件**：
   - HTTP 状态码为 403；
   - Tushare 业务返回码 `code` 处于 2001, 2002, 40101~40103 范围；
   - 响应 `msg` 包含 "权限"、"permission"、"unauthor"、"403"、"token"；
   - 环境中缺少 `TUSHARE_TOKEN`。
2. **记录契约与禁止红线**：
   - **严禁掩盖**：绝不能将 403 异常捕获后简单返回空列表 `[]` 并当作“公司没有预告/回购”；
   - **严禁泄露 Token**：日志、异常栈、gap 消息中严禁打印 Token 内容或带有 Token 的 URL；
   - **记入 Gap 规范**：
     ```json
     {
       "source": "tushare_forecast",
       "theme": "财报",
       "item": "tushare_forecast",
       "status": "provider_failure",
       "reason": "403_forbidden",
       "message": "tushare_forecast：私有网关调用权限拒绝（403/permission_denied），不可验证（异常非空表，不得推断无相关记录）"
     }
     ```

### 4.2 空表（0 行）：判定为 `collateral_empty`，绝对禁止写成「确认无公告」

这是整个 C-05 旁证契约的**第一禁止红线**。

#### 4.2.1 为什么旁证空表绝对不能等同于“确认无公告”？

1. **披露规则差异（业务真实性）**：
   - 在 A 股市场，上市公司并非每期都必须发布业绩预告。只有当净利润为负、扭亏为盈、实现盈利且净利润同比增减 50% 以上等触及法定披露红线时才强制预告。经营平稳的公司可能直接披露正式定期报告；
   - 绝大多数上市公司在绝大多数时间里并没有进行股票回购；
   - 因此，`forecast` 或 `repurchase` 表返回 0 行，属于完全正常的商业事实，**绝不代表该公司在这个时间段内没有发布其他重大公告**（如中标公告、重大重组、高管变更等）。
2. **数据源覆盖率与清洗时滞（信源局限性）**：
   - Tushare 结构化表由第三方数据团队二次清洗录入，存在数据更新时滞（T+0 晚间或 T+1 凌晨录入）；
   - 在公告刚发布的数小时内，Tushare 表内很可能尚未提取入库，返回空行；
   - 若把旁证空表误判为“确认无公告”，多智能体系统就会对刚刚发生的重磅事件视而不见，产生致命盲区。
3. **主源与旁证的单向依赖关系**：
   - **只有法定的巨潮主源检索结果为空时**，在满足特定前置审计条件后，才允许谨慎提示 `cninfo_status: "confirmed_empty"`（且依旧保留不可外推至全市场新闻的声明）；
   - **旁证表的空表永远只能代表旁证表本身为空**，绝不能跨域篡改整个事件流的覆盖率结论。

#### 4.2.2 记入 Gap 与状态表达规范

当旁证接口返回 0 行时：
- 旁证记录列表为 `[]`，状态标记为 `status: "ok", count: 0`；
- 在事件覆盖率评估（`event_coverage`）中，**严禁将 `is_confirmed_empty` 设置为 `True`**；
- 若上层调用方显式在 `query_manifest` 中指定了对应旁证主题（例如指定查询 `tushare_repurchase`），在 `suspected_gaps` 中记录：
  ```json
  {
    "source": "tushare_repurchase",
    "theme": "公司治理",
    "item": "tushare_repurchase",
    "status": "not_retrieved",
    "reason": "未检索到/不可验证",
    "message": "tushare_repurchase：未检索到结构化旁证记录（注：仅代表私有网关特定专题表无记录，不可据此推断上市公司无相关公告）"
  }
  ```

### 4.3 缺列 / 格式漂移 / 字段损坏：记入 `schema_drift`

当私有网关升级或供应商数据返回格式变动时，必须具备强壮的防御性解析：

1. **核心必选列校验**：
   - `forecast`：必须包含 `ts_code` 与 `ann_date`；
   - `repurchase`：必须包含 `ts_code` 与 `ann_date`；
   - `disclosure_date`：必须包含 `ts_code`、`ann_date` 与 `end_date`。
2. **防御处理**：
   - 若关键列缺失，或返回数据形状不符合契约（例如非 `fields/items` 结构），解析器必须拒绝该响应；
   - 绝不允许对缺失的关键日期用当前日期（`today`）或系统抓取时间强行填充；
   - 将损坏记录记入 `recall_gap`：
     ```json
     {
       "source": "tushare_disclosure_date",
       "status": "schema_drift",
       "reason": "missing_required_columns: ann_date",
       "message": "tushare_disclosure_date：返回数据缺少必要日期字段（ann_date），该源判定为不可验证并丢弃"
     }
     ```

---

## 五、架构数据流与后续演进全景

```
                    ┌────────────────────────────────────────────────────────┐
                    │               多智能体数据采集请求                     │
                    │               (Data Collector Pipeline)                │
                    └───────────────────┬────────────────────────────────────┘
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
    【主数据流：巨潮资讯官方主源】                 【旁证数据流：Tushare 私有网关】
    • AKShare cninfo_announcement                  • 仅读取 TUSHARE_API_URL
    • AKShare cninfo_ir_survey                     • forecast / repurchase / disclosure_date
    • 提取原生 announcementId                      • 严格按 ann_date <= as_of 截断
    • 生成 canonical_event_id: cninfo:...          • 提取 p_change, amount, pre_date 结构化值
    • 官方 PDF 下载 & 内容 SHA256 核验              • 严禁生成 cninfo ID (保持 canonical_id=None)
                 │                                             │
                 │                                             │
                 └──────────────────────┬──────────────────────┘
                                        │
                                        ▼
                      ┌───────────────────────────────────┐
                      │ 结构化弱对齐挂载与证据集成         │
                      │ (Soft-Alignment & Attachment)     │
                      └─────────────────┬─────────────────┘
                                        │
                                        ▼
                      ┌───────────────────────────────────┐
                      │ 事件覆盖率与诚实 Gap 审计         │
                      │ (event_coverage & recall_gap)     │
                      │                                   │
                      │ • 403 ──► provider_failure gap    │
                      │ • 缺列 ──► schema_drift gap       │
                      │ • 空表 ──► 严禁判定为确认无公告   │
                      │ • 来源 ──► 仅回显真实实际查询源   │
                      └───────────────────────────────────┘
```

### 5.1 模块化分阶段演进路线

按照单卡单关注点的工程原则，本次任务（DAV-632）严格聚焦于“切片 1（只读）：架构评估与挂载契约冻结”，业务代码施工将在后续独立任务中推进：

- **切片 1（本卡 DAV-632，只读）**：
  - 冻结 `forecast`、`repurchase`、`disclosure_date` 字段与严格 PIT 截断标准；
  - 冻结其作为结构化旁证挂载到巨潮主源的契约与 ID 隔离红线；
  - 冻结 403 / 空表 / 缺列的分类准则与严禁推导“确认无公告”的纪律；
  - **业务代码零改动**。
- **切片 2（后续独立开发卡）**：
  - 在 `tradingagents/dataflows/providers/cn_akshare_provider.py` 中新增 `_fetch_tushare_forecast`、`_fetch_tushare_repurchase`、`_fetch_tushare_disclosure_date` 旁证读取实现；
  - 接入 `_tushare_transport_post`，走 `TUSHARE_API_URL` 环境变量；
  - 编写对应单测，验证 PIT 截断与 403/空表/缺列的异常归类。
- **切片 3（后续独立集成卡）**：
  - 在 `tradingagents/dataflows/news_event_evidence.py` 中实现 `CollateralRecord` 挂载至 `NewsEvidence` 的弱对齐逻辑；
  - 与 `data_collector.py` 协同，全面完成多智能体结构化旁证增强。

---

## 六、合规性与系统状态自审声明

资深开发2 在此严正声明，本卡交付物完全遵循只读审计纪律与全部约束红线：

1. **未修改任何业务代码**：`tradingagents/`、`api/`、`frontend/` 等业务代码目录保持零改动，`git status` 仅包含当前新增评估文档。
2. **未接线 `anns_d`**：未引入任何对 `anns_d` 的接口调用、配置或设计，主源全量公告坚持巨潮资讯 AKShare 链路。
3. **未改动聚类与 PDF 校验**：`EventCluster` 聚类算法、`content_status: 'hashed'` 校验与 PDF 内容资格判定逻辑保持完全独立未碰。
4. **绝对杜绝虚假无公告推断**：文档中以最高优先级确立“旁证空表严禁推断为确认无公告或全市场无新闻”的架构铁律。
5. **未泄露任何凭证信息**：文档与测试中绝无出现任何真实 Token、敏感环境变量或私有 URL 参数。
6. **未启停运行中的服务**：未启动、停止或重启当前环境下的 uvicorn 进程或其他系统后台服务。
7. **未执行部署与 Fast-Forward**：未对任何生产/预发集群发起部署，保持标准独立分支提交。
