# E-04：基本面 / 事件 / 预期修正分栏

## 施工定位

这是整合施工计划 v1.1 的 E-04 实施卡，起点必须是当前远端施工主干的精确版本：

```text
base SHA: 020d3e3b18147f5ea20e90d3878b20952d20fd97
remote ref: refs/heads/codex/dav-4-p2a-trunk
parent/runtime: 生产仍为 a227cdc3bb466edf2e910419cb6013cfc021d309；本卡不得部署或重启
```

先核对远端主干仍为上述 SHA；若已变化，停止并以新 SHA 重新报告第一父和白名单，不得在旧基线上继续写。

目标不是让模型“猜出预期差”，而是把当前已有、可核验的事件事实和财务记录分成可回读的结构化栏位。无法证明的部分必须保留为 typed gap 或定性状态。

## 产品契约

相关 analyst trace 必须携带一个 JSON-safe 的 `expectation_revision` 栏位，至少包含：

- `status`：`available` / `partial` / `gap` / `not_applicable`；
- `event_type`：`fundamental` / `event` / `none`；
- `publication`：发布时间、来源、`source_hash` 或等价可回溯引用、正文资质状态；不得用抓取时间或当前日期补发布时间；
- `actual`：只有现有结构化财务记录已经同时给出指标、数值、单位、报告期和截至日期时才填数值，否则明确缺口；
- `baseline`：`management_guidance` / `consensus_expectation` / `prior_self_forecast` / `implicit_model` / `none`，并记录来源、指标、单位、期间和 as-of；没有旧基线必须是 `none`，不得计算数值修正幅度；
- `revision`：`numeric` / `qualitative` / `gap`；只有 actual 与 baseline 在标的、指标、单位、报告期和 as-of 均可比时才允许 numeric；
- `priced_in`：`supported` / `not_supported` / `unknown`，没有可回溯证据时不得写成已定价或未定价的事实；
- `double_count_guard`：说明该影响是否已被已有预测/事件栏位计入；无法判断时保持 `unknown`，不得再次加票；
- `gaps`：列出缺失、不可验证、未来日期、正文未取得或不可比较的具体原因。

字段名可以在实现中保持上述固定契约；不得以自由文本替代结构化状态。所有数值必须带来源和口径，不能由 LLM 输出单独创造。

## 允许修改的文件

只能修改以下生产代码、提示词和测试文件：

```text
tradingagents/agents/analysts/news_analyst.py
tradingagents/agents/analysts/fundamentals_analyst.py
tradingagents/agents/managers/research_manager.py
tradingagents/prompts/zh.py
tradingagents/prompts/en.py
tests/test_expectation_revision_contract.py
```

不允许修改 `agent_states.py`、`propagation.py`、`trading_graph.py`、数据库 schema、API、provider、通用 PDF/HTML 解析器或现有证据关系/财务期间底层契约。应优先把结构化栏位放进现有 `analyst_traces`，并证明 LangGraph 的现有状态/结果回读仍保留它；若不改 `agent_states.py` 无法安全保留，停止并报告 blocked，不得擅自扩大白名单。

允许读取并调用已有的新闻事件证据、正文资质、财务期间合规解析；不得修改这些底层模块，也不得把标题、URL、PDF 字节 hash、公告存在本身当作已读到的财务数字。

## 实施要求

1. `news_analyst`：从已有 `event_coverage` / 合格新闻证据生成事件/发布时间/来源引用和内容资质；对“新发布”“新内容”只能在现有字段足以证明时标记，否则为 unknown/gap。
2. `fundamentals_analyst`：沿用现有财务输入及报告期/公告日合规结果；forecast、业绩预告、实际半年报必须分开，不得把预测当实际，不得把 H1 累计写成 Q2 单季。
3. 两个 analyst 都不得让模型自由生成 actual、baseline 或 revision 数值；缺少可比较旧基线时只能输出 qualitative/gap。
4. `research_manager`：把两类 trace 的结构化栏位作为可审计上下文传给经理，并在返回结果/现有回读路径中保留；经理提示词只能引用已提供字段，不能自行填数、重复计入或把 unknown 改成事实。
5. 中英文提示词只补充上述边界和输出纪律，不改变 E-02、E-03、PIT、资金流 guard、决策状态或现有交易口径。
6. 现有报告没有可靠正文抽取时，必须留下明确 typed gap；本卡不实现通用 PDF 解析器，不调用未授权的外部采集，不写生产库。

## 必须新增的红队测试（`tests/test_expectation_revision_contract.py`）

至少覆盖以下 12 类情形，测试必须在候选 SHA 上实际运行并列出精确计数：

1. 只有标题、URL 或 PDF 字节 hash，没有正文指标：不得生成毛利率/收入等 actual 数字。
2. 有发布时间和 source hash 但正文未取得：publication 可追溯，actual/baseline/revision 为 gap。
3. 实际财报期间与业绩预测期间不同：不得互相替代。
4. 没有旧基线：revision 不能出现百分比或数值幅度，只能 qualitative/gap。
5. 管理层指引、一致预期、自身旧预测、隐含模型四类 baseline 必须可区分，来源缺失不能降级冒充另一类。
6. actual 与 baseline 的单位、报告期、as-of 任一不一致：不得 numeric。
7. “超预期/不及预期”只有在可比基线真实存在时才能成立，否则 unknown/gap。
8. “已定价”没有可回溯证据时必须 unknown，不得由标题或模型措辞推断为事实。
9. 同一事件已经进入 forecast/事件栏位时，`double_count_guard` 阻止再次加票；无法判断不加票。
10. 未来日期、不可解析日期、provider failure 或正文 unavailable 必须进入 gap，不能作为方向证据。
11. research manager 只能消费结构化栏位，不能从自由文本补造数值；trace 与最终结果回读一致。
12. 现有新闻、基本面、财务期间合规、研究经理和 E-02/E-03 相关测试保持原语义，不得通过删测或放松断言“变绿”。

## 验收门禁

- 交付完整 40 位候选 SHA、直接父 SHA、远端分支、白名单逐文件 diff、`git diff --check` 和洁净状态。
- 候选不得包含 `work/` 证据、数据库、副本、配置、凭据或部署产物。
- 使用固定解释器：

  ```text
  env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
  ```

- 至少运行：新增 E-04 测试、news/fundamentals/research_manager 相关测试、财务期间合规/事件证据相关测试；交付报告列出实际命令和结果。
- 不能把定向测试通过当成全量绿色；交付后由项目评估员做红队覆盖复核，再创建同一 SHA 的只读代码审查卡。
- 代码审查只能派给 `代码审核员`，不得派给 `独立代码审核员`；审查、合入、部署分别留证。
- 实施者不得自行合入、部署、重启、修改生产库或开真实采集；候选完成后卡状态置为 `in_review`，不要直接置 `done`。

## 明确不做

- 不新增通用 PDF 解析器；
- 不把市场标题、URL、hash、模型推断当财务事实；
- 不改收益实验、数据库写入、社交采集、Cookie、信用加权或生产运行态；
- 不新增“预期差投票”或任何分析师人数/来源数量加权。
