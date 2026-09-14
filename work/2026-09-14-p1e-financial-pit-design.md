# P1-E：Fuyao 财务披露日 PIT 窄修设计

日期：2026-09-14  
状态：设计冻结；实施卡在 P1-D 收口后建立

## 1. 结论先行

这不是重做已经存在的 `cn_akshare` 公告日截断逻辑。当前生产基本面链的首选 provider 是 `cn_fuyao`，因此真正的剩余风险在 Fuyao 这一条路径：

1. 三大报表请求虽然带 `end=curr_date`，返回行也有 `report_date_ms`，但 `_financial_report_markdown()` 目前只对 `report_date_status == "future"` 的行遮蔽金额；`report_date_status == "missing"` 的行仍可带着金额进入提示词。
2. `_q2_derivation_block()` 的 H1/Q1 资格判断没有要求披露日已核实，缺披露日的行仍可能参与 H1−Q1 派生。
3. 若 `period_end` 本身晚于分析日但上游日期字段异常地较早，当前展示层也没有统一的 period-end fail-closed 保护。
4. Fuyao `get_fundamentals()` 只向指标接口发送启发式的 `report=yyyy-N`；返回信封没有逐票公告披露日，不能把该结果称为逐票披露日 PIT。

所以当前不能写成“Fuyao 财务数据已经完成公告日 PIT”。`cn_akshare` 的逐期有效公告日逻辑已存在，但只有回退到该 provider 时才覆盖上述 Fuyao 首选路径。

## 2. 现有证据

- 路由配置的 `fundamental_data` 顺序为 `cn_fuyao,cn_akshare,...`，所以 Fuyao 的成功字符串会阻止后续 provider 接手。
- `cn_fuyao_provider.py::_annotate_financial_rows()` 将 `report_date_ms` 转为 `report_date`，状态分成 `verified/future/missing`。
- `_sanitize_future_rows()` 仅处理 `future`，没有处理 `missing`，也没有把 `period_end > curr_date` 作为独立可见性门槛。
- `_derivation_frame()` 只排除 `report_date_status == "future"`，没有要求 `verified`；这使得缺失日期行可能进入 H1−Q1。
- `get_fundamentals()` 的指标响应只有 `data.report` 与 `abilities`，当前代码没有可核验的逐票 `ann_date` / `report_date`。
- `cn_akshare_provider.py` 已通过 `financial_announce.py` 的 `build_effective_announce_map()` 和 `filter_financial_df_by_effective_announce()` 做公告生效日截断；本项不复制或改写那套实现。

### 2.1 只读复现

在目标主干 `51b8b15` 检查树中，用固定 Python 3.10、mock Fuyao 信封（两行都有金额，但 `report_date_ms=0`）调用 `get_cashflow(..., curr_date="2026-09-10")`：

- `missing_date_value_visible=True`：`report_date=unknown` / `report_date_status=missing` 的行仍显示 `1134640000`；
- H1−Q1 派生块仍生成 `经营活动产生的现金流量净额=2290200000` 和 `购建固定资产...=612030000`，即缺失披露日的两行仍被当作可计算输入。

这证明它是当前代码可复现的安全缺口，不是只由旧文档推断出来的风险。

## 3. 最小实现方向

### 3.1 三大报表

- 只有同时满足 `period_end <= curr_date` 且 `report_date_status == "verified"` 的行，才允许保留财务数值。
- `future`、`missing`、非法日期以及 `period_end > curr_date` 的行可以保留日期/状态元数据用于审计，但不得把数值、H1−Q1 派生结果或普通数值摘要暴露给模型。
- `_q2_derivation_block()` 与 `_derivation_frame()` 必须使用同一条 `verified` 可见性条件；缺失披露日时只能明确输出不可派生原因。
- 若一张报表没有任何可核验的可见行，应返回带原因的 `VendorRefuse(allow_peers=("cn_akshare",))`，只允许既有公告日感知 provider 接手；不得落到日期盲的弱源，也不得把“没有可核验披露日”包装成“确认无数据”。
- 已核验的合法零值、负数和百分比保持原值；只遮蔽不可证明时点的财务数值。

### 3.2 财务指标 `get_fundamentals`

- 当接口响应不含逐票可核验披露日时，不得把 `report=yyyy-N` 的法定截止日启发式当成实际公告日。
- 对需要 PIT 证明的分析请求，返回明确的 `VendorRefuse`，并将 peer allowlist 限定到能提供公告日截断的现有路径（当前为 `cn_akshare`）；不伪造 `report_date`，不从报告正文、标题或 PDF 推测公告日。
- 若后续确认 Fuyao 指标接口有稳定、逐票的公告日字段，可另开小卡接入；本卡不得先假定字段名称或语义。

### 3.3 明确不在本卡

- 不改 `financial_announce.py` 的 A4 算法或法定截止日常数。
- 不做 CNINFO PDF/财务正文抽取，不把标题、URL、hash 或 LLM 结果当成财务数值。
- 不接 `/limit-up-ladder`，不改 C-05 的公告/旁证模型，不改数据库 schema 或报告持久化。
- 不改 Fuyao API key、频控、交易日历、社交、H1b、V-03 或前端。
- 不写生产库、不触发真实分析、不部署；部署另走发布门。

## 4. 建议白名单

首选最小白名单：

- `tradingagents/dataflows/providers/cn_fuyao_provider.py`
- `tests/test_cn_fuyao_provider.py`

只有在测试需要补充独立 PIT 夹具时，才允许新增一个专用 fixture 文件；不得顺手扩大到 `interface.py`、`data_collector.py` 或 `financial_announce.py`。

## 5. 最低红队与验收

1. 合法披露日且报告期不晚于分析日：金额仍可见，合法 `0`、负数保持不变。
2. 披露日缺失但有金额：金额不进入可供模型使用的表格，状态明确为 `missing/unavailable`；不得被当成“确认无数据”。
3. 披露日晚于分析日：金额不进入提示词，且 H1−Q1 不得使用该行。
4. 报告期晚于分析日但披露日字段异常早：仍不得显示金额。
5. H1 已核验但 Q1 缺披露日，或反过来：不得派生 Q2；原因必须可读且不是静默零值。
6. 指标响应只有 `report=yyyy-N`、没有逐票披露日：不得标成 PIT 成功；既有 provider 链可回退时必须保留 `VendorRefuse` 原因和受限 peer allowlist。
7. 既有 Fuyao 期间语义、缺失 `curr_date` 拒绝、未来行遮蔽、3001/3002/4001 错误语义不回归。

## 6. 发布门

实现卡交付后必须报告完整 40 位候选 SHA、直接父、clean 状态、严格白名单和实际测试数字；先由**代码审核员**对同一完整 SHA 只读复核，再由总工复跑关联回归和全量相对基线对照。通过审查和“零新增失败”只进入合入门，不自动代表部署或生产 PIT 证据已经完成。
