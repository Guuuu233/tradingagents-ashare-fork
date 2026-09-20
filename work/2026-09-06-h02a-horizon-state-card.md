# H-02a 跑次元数据进入 AgentState

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `872d94e9477809a236b09139afe145b3343e3856`（或其线性后代）。脏宿主 `/Users/davidliu/Documents/TradingAgents-AShare` 禁止当工作树、禁止 reset/clean。从该 SHA 的干净 worktree 开工。  
**一个关注点：** 把 H-01 已解析的期限配置写进图初始 state，供后续节点读取。不改报告 payload、不改缓存键、不改 `api/main.py` / `trading_graph.py` / `report_service.py`。  
**禁止：** frontend；collector；分析师；校准/回测/`DEFAULT_HOLD_DAYS`/shadow_credit；写 `evaluation_eligible=true` 或计算收益/评价日；H-02b/H-02c/H-03+；C-04/C-09-3/Track B/H1b/PDF；改 `role_bindings`/`providers`/token；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划：Codex `2026-09-06_整合施工计划-v1.1.md` 的 **H-02a**。H-02b 才落库回显。本卡只保证 `Propagator.create_initial_state` 产出可序列化的跑次元数据。

## 允许改

- `tradingagents/agents/utils/agent_states.py`（TypedDict / 字段；禁止并行 `_v2` state）
- `tradingagents/graph/propagation.py`（`create_initial_state` 写入上述字段）
- `tests/test_horizon_run_metadata.py`（新建；本卡主验收）
- 若现有断言被新字段打断的最小修补：`tests/test_trading_graph_multi_horizon.py`、`tests/test_report_social_context.py`、`tests/test_agent_states.py`（不得改 `_build_horizon_result` / 报告 payload 语义）

## 现网缺口（`872d94e`）

`create_initial_state` 只有切片字段 `horizon: str`（默认 `"short"`），以及 `user_context.investment_horizon`（持有意图）。没有：

- 请求是否显式提供过 `horizons`
- `resolved` 列表（可双档）与 `resolution_source`
- `horizon_profile_v1` 及计划评价步长 T+10/T+40（常量，不是已算评价日）
- cutoff：可复用已有 `workflow_context.data_as_of` / `market_context.data_as_of`，须在跑次元数据里**显式引用**，不要另造第二套 cutoff 算法

`horizon=` 参数仍是**本图这一档切片**（双档会跑两次图）。不得用 `horizon="medium"` 推断整次请求是 explicit medium。

## 契约

1. 新增一处一等字段（建议名 `horizon_run_metadata`，若改名须全卡一致），JSON 可序列化 dict，至少含：
   - `requested`：未提供为 `null`；显式则为用户列表（去重前或后须在测试里写死一种并保持）
   - `resolved`：`["short"]` / `["medium"]` / `["short","medium"]`（保序）
   - `resolution_source`：`default` | `explicit`（历史无版本可 `legacy`，仅当调用方显式传入；默认路径不要自称 explicit）
   - `profile_id`：`"horizon_profile_v1"`
   - `primary_eval_offsets`：按 **resolved 各档** 取自 `HORIZON_PROFILE_V1` 的 `primary_eval_offset`（short→10，medium→40）。这是计划步长，不是交易日日历、不是已解析评价日。
   - `cutoff`：与 `workflow_context.data_as_of` 相同来源；缺则 `null`，禁止填「今天」
   - `investment_horizon`：从 `user_context` 拷贝持有意图；缺则 `null`。**禁止**与 `resolved` 合并或互相覆盖
   - 不得出现 `evaluation_eligible: true`；若写该键必须为缺省/false/省略
2. `create_initial_state` 增加可选参数（例如 `horizon_resolution: Optional[HorizonResolution | Mapping]`）。未传入时调用 `resolve_analysis_horizons` 的**未提供**路径（default short），即使 `horizon="medium"`。
3. 传入 `HorizonResolution` 或与 H-01 `to_dict()` 兼容的 mapping 时原样落入 state（可补 profile/offsets/cutoff/持有意图）。二次组装不得把 default 翻成 explicit。
4. 保留现有 `horizon` 字符串语义与社交 `social_data_context` 默认行为。
5. 复用 `tradingagents/graph/horizon_profile.py`，禁止再写一套解析。

## 测试

TDD：先写 `tests/test_horizon_run_metadata.py` 确认 `872d94e` 上 RED，再改 state/propagator。

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_horizon_run_metadata.py tests/test_trading_graph_multi_horizon.py tests/test_report_social_context.py tests/test_agent_states.py -q
```

至少覆盖：缺省 default+short+T+10；显式 medium；显式双档列表在 state 而本图 `horizon` 仍为 short；`horizon="medium"` 且未传 resolution 仍为 default/short 元数据；持有意图与研究档分离；cutoff 与 workflow 一致且不填今天；无 `evaluation_eligible true`；社交默认 context 仍在。

不要跑全仓当本卡验收。一个 commit，push 功能分支，评论完整 40 位 SHA、父提交、`git diff --stat`、测试命令与真实 passed/failed/skipped。
