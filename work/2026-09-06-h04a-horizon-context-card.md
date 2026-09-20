# H-04a 研究档与专业观察窗在 context 中分别可见

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `8325943e23e67b2b8f81438d03aa297d9f7eccfe`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。从该 SHA 的干净 worktree 开工。  
**一个关注点：** `build_horizon_context` 把「本次研究档」和「本节点专业观察窗」写成两行可区分文案；分析师现有调用不改也能读到研究档。  
**禁止：** 改任何 `tradingagents/agents/analysts/*.py` 或 researchers；改 `data_collector.py` / `get_window`；改 `research_manager.py`；H-04b/H-04c/H-04d/H-05；把 14 天/90 天换成 T+10/T+40；改 `parse_intent` 的 `horizons` 强制值；改 `role_bindings`/`providers`；C-04/C-09-3/Track B/H1b/PDF；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-04a**。现网（`8325943e`）：

- 分析师把专业窗写死后传入 `build_horizon_context`（市场/新闻/资金/量价/情绪=`short`，基本面/宏观=`medium`）。
- `build_horizon_context` 只用这一参数填「当前分析维度」，标签是「短线（1-2周，技术面主导）」/「中线（1-3月，基本面主导）」。中期跑次里市场节点会被写成短线任务。
- `Propagator.create_initial_state` 已有本切片 `horizon` 与 `horizon_run_metadata.resolved` / `profile_id` / `primary_eval_offsets`。评价步长已在元数据里，本卡只展示，不算评价日、不改回看窗。

## 允许改

- `tradingagents/graph/intent_parser.py`（`build_horizon_context` + 研究档绑定；禁止并行 `_v2`）
- `tradingagents/prompts/zh.py`、`en.py` 的 **`horizon_context_block` 一条模板**（其它 prompt 不动）
- 必要 state 传递：仅 `tradingagents/graph/propagation.py` 的 `create_initial_state`（或同文件内极小 helper）绑定当前切片研究档，供现有 `build_horizon_context(...)` 读取。可用 `contextvars`。不要改分析师文件去传新参数。
- 测试：新建 `tests/test_horizon_analyst_context.py`；若旧断言被模板字段打断，最小改 `tests/test_intent_parser.py` 里三个 `build_horizon_context_*`。

## 契约

1. 第一参数 `horizon` **仍是专业观察窗**（与现网分析师传入值同义）。不得把它改名为研究档后让旧调用静默变成「任务=专业窗」。
2. 研究档来源优先级：显式可选 kwarg（若加）> 本线程绑定（create_initial_state 写入的当前切片 `horizon`，须与 `horizon_run_metadata.resolved` 含该档一致）> 未绑定。未绑定不得把专业窗冒充研究档。
3. 模板必须同时出现可机读区分的两行（中英都要），例如「本次研究档」与「本节点专业观察窗」（英模板对应清晰英译）。禁止再只留一个「当前分析维度」。
4. 中期切片 + 专业窗 `short`：文案含中线研究档 **且** 短线观察窗；不得把整段说成短线任务/短线研究。短期切片 + 专业窗 `medium`：对称。
5. `weight_hint` 保持空；不要为基本面节点编造「次要」。不要改 collector 窗口、不要改 `HORIZON_PROFILE_V1` 评价步长语义。
6. 绑定必须在图跑完/测试结束可清理，避免用例串档。双档是两次图；每次 `create_initial_state` 绑的是**这一切片**的 `horizon`，不是 `resolved` 整表。
7. 不改分析师、bull/bear 文件。bull/bear 现在把 `state["horizon"]` 当第一参数：本卡之后该参数仍按专业窗槽位填，研究档走绑定；二者同为 medium 时两行可以相同，不算失败。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_horizon_analyst_context.py tests/test_intent_parser.py
```

`test_horizon_analyst_context.py` 至少覆盖：

- 绑定 medium 后 `build_horizon_context("short", ...)` 同时可见中线研究档与短线观察窗，且不以「当前分析维度：短线」冒充任务档。
- 绑定 short 后 `build_horizon_context("medium", ...)` 对称。
- 未绑定：专业窗仍可见；研究档为明确未绑定/缺失，不得填成与专业窗相同的假研究档。
- `create_initial_state(..., horizon="medium", horizon_resolution=...)` 之后，不改分析师调用签名即可让 `build_horizon_context("short", [])` 读到 medium 研究档（用显式 `HorizonResolution` / 与 H-02a 测试相同的传入方式）。

一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。
