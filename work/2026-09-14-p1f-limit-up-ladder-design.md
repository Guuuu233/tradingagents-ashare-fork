# P1-F：连板天梯接入设计冻结

日期：2026-09-14（Australia/Perth）  
设计状态：已冻结，尚未改功能代码、尚未派工、尚未部署

## 1. 发现的缺口

当前 `cn_fuyao` 只有 `get_zt_pool`：它按单个日期取得涨停池，并在结果中做一段
`continue_day_cnt` 的连板分布整理。代码、路由、工具包装、`DataCollector` 和提示词
均没有独立的 `/api/a-share/special-data/limit-up-ladder` 能力。因此“连板分布”不能写成
“连板天梯已接入”。

本设计只补齐独立能力的来源、日期、缺失和审计语义，不把它接入交易信号或博弈论确定性
计算。

## 2. 冻结的上游契约

以官方接口文档为准：

- [Fuyao 连板天梯工具文档](https://fuyao.aicubes.cn/docs/mcp/tools/get_a_share_special_data_limit_up_ladder/)
- [Financial-API special-data endpoint 文档](https://github.com/HiThink-Tech/Financial-API/blob/main/docs/api/endpoints-special-data.md)

接口名称为 `get_a_share_special_data_limit_up_ladder`，REST 路径为
`GET /api/a-share/special-data/limit-up-ladder`，不接受自定义日期或窗口参数；返回固定的
近 30 个交易日矩阵。外层包含 `timestamp`、`window`、`item`；`window` 包含
`length`、`date_list`、`board_caps`；矩阵板块键为 `two_board`、`three_board`、
`four_board`、`five_board`、`six_board`、`seven_over`。空板块数组是正常业务状态，不是
服务故障。

上游没有“给定历史日期重放”能力，所以本项目不能把当前滚动窗口伪装成历史快照。

## 3. 实施契约

### 3.1 能力名称与路由

- 对内能力名固定为 `get_limit_up_ladder`，放入现有 A 股市场数据工具类别，并提供现有
  provider 路由/工具包装；不新增第二套并行 provider 体系。
- `cn_fuyao` 是唯一允许的来源。该方法不得沿用全局自动补齐的 provider fallback；Fuyao
  失败时返回明确的 typed unavailable/failure 结果，不能改走 AkShare，也不能由
  `get_zt_pool` 拼出“天梯”。
- `get_zt_pool` 的请求、返回格式、日期回退和连板分布行为保持不变。

### 3.2 as-of 与历史边界

- 内部调用必须带 `curr_date`/请求基准日期；这个日期只用于本地 PIT 门禁，不传给上游，
  因为上游端点无日期参数。
- 对严格早于当前中国日期的分析日期，在发出网络请求前直接返回已有的历史快照拒绝语义，
  分析继续运行但该字段标为不可用；不得用今天的天梯填充历史分析。
- 对当前日期也必须校验返回的 `window.date_list`：若出现晚于请求基准日期的日期，整段
  天梯拒绝并保留原因；不得裁剪、向前找日期或静默用 `iloc`/单日涨停池回退。
- 返回日期缺失、格式非法、窗口长度与日期列表矛盾或外层信封不完整时，返回明确的
  typed failure/unavailable，不把空结果解释为“没有连板股”。

### 3.3 结构、来源与缺失语义

- 六个板块键必须逐一校验；合法空数组保留为空，不伪造股票、不补齐板数。
- `thscode`、`ticker`、`name`、`board_num`、`sign_level`、`seal_nextday` 等来源字段
  原样保留；尤其 `seal_nextday=null` 必须保持未知，不能推断次日封板。
- 输出必须保留 `source=cn_fuyao`、请求基准日期、上游窗口日期范围和状态/原因码，便于
  provider trace、报告来源追溯和后续 readback 对账。所有错误码仍按现有 Fuyao provider
  的错误映射约定处理，不新造“无数据即正常”的字符串。
- `market_attention` 增加独立的 `limit_up_ladder` 字段；它与 `zt_pool` 并列，不能把
  天梯内容塞进 `zt_pool` 的 `raw` 字段，也不能把“连板数”转换成方向票或交易信号。
- 提示词若展示该字段，必须同时显示时效、来源和不可用原因，并明确“市场关注度背景，
  非方向证据”；不得覆盖或改写现有 `zt_pool` 语义。

## 4. 允许修改的范围

实施卡只允许修改以下文件：

- `tradingagents/dataflows/providers/cn_fuyao_provider.py`
- `tradingagents/dataflows/interface.py`
- `tradingagents/default_config.py`
- `tradingagents/agents/utils/game_theory_tools.py`
- `tradingagents/agents/utils/agent_utils.py`
- `tradingagents/graph/data_collector.py`
- `tradingagents/dataflows/social/prompt_formatter.py`
- `tests/test_limit_up_ladder.py`

如实现确实需要其他文件，必须先停在 `in_review` 之前说明原因并更新卡面；不得自行扩大
白名单。

## 5. 明确不做

- 不改 `game_theory_node.py` 的特征、信号、权重、投票或确定性计算。
- 不把天梯接入买卖方向、收益评估、H1b 或信用加权。
- 不改变 `get_zt_pool`、龙虎榜、交易日历或其他 provider 的行为。
- 不新增数据库表/列、报告回填、生产数据写入、真实社交采集、Cookie、前端 UI 或部署。
- 不从 `get_zt_pool`、当前日期或模型输出合成缺失的天梯。

## 6. 红队与验收门禁

至少逐条覆盖并给出固定 Python 3.10 的精确结果：

1. 合法完整的 30 日响应，包含六板块和来源元数据。
2. 六板块合法为空时仍为 available，空数组不被改写成错误或零值事实。
3. 外层信封缺失、`window`/`date_list` 类型错误、日期非法、长度矛盾、板块缺键时显式
   失败；不得返回部分伪造天梯。
4. HTTP 4xx/5xx、Fuyao 4001、无效 key、无数据分别保留 typed failure/unavailable
   语义；不自动切换 AkShare。
5. 历史分析日期在网络调用前拒绝；测试必须断言请求次数为零。
6. 返回窗口包含晚于请求基准日期的日期时拒绝；不得裁剪、日期回退或 `iloc` 切片。
7. `seal_nextday=null` 原样保留；不从价格或涨停池推断它。
8. `get_zt_pool` 的父版本行为和字段保持不变；不能因新增能力扩大旧路由的 fallback。
9. `market_attention.limit_up_ladder` 与 `zt_pool` 分栏，提示词显示来源/时效/状态，且不
   生成方向信号。
10. trace/返回结构包含请求基准、上游窗口和来源；失败路径同样可追溯，不用自由文本掩盖
    缺失原因。

候选交付必须包含完整 SHA、直接父、实际分支、clean 状态、严格白名单和
`git diff --check`。随后由**代码审核员**对同一完整 SHA 只读审查，再与当前发布版本做
同口径 RT-FULL；代码审核、RT-FULL、合入和部署仍是分开的门。实施卡本身不得部署或触发
真实分析。

## 7. 当前结论

这是一个可施工的独立 P1 缺口，但不是当前线上故障，也不是收益实验解锁条件。完成代码和
回归后，仍只能声称“天梯能力已按固定当前窗口接入”；历史分析若需要该字段，必须明确写
不可用，不能声称已有历史天梯证据。
