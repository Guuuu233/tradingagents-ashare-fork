# H-02b 跑次元数据回显落库

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `664a76b1b8ad9bbb8aba45a812e0c286928b1f21`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 把 H-02a 的 `horizon_run_metadata` 从 state 接到图结果与 ReportDB `result_data` 回显。不改派生缓存键（H-02c）、不改分析师/collector、不改 `propagation.py` 装配逻辑（空列表 mapping 兜底随入口只传 `HorizonResolution` 收口，不要再改 propagator）。  
**禁止：** frontend；缓存键；H-02c/H-03+；校准/`DEFAULT_HOLD_DAYS`/收益标签/`evaluation_eligible=true`；C-04/C-09-3/Track B/H1b/PDF；`role_bindings`/`providers`；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-02b**。旧报告缺字段标 legacy/unknown，禁止回填新 T+40。首选 JSON 元数据，不新增 DB 列。

## 允许改

- `tradingagents/graph/trading_graph.py`（`create_initial_state` 传入 `horizon_resolution`；`_build_horizon_result` 拷贝 metadata）
- `api/main.py`（三处 `create_initial_state`：约 2998、3570、3764；`_build_result_payload`；`_run_job_inner` 勿把已解析来源丢掉）
- `api/services/report_service.py`（`result_data` 根及 dual 嵌套 `short_term`/`medium_term`/`horizons` 留痕；读回显；对标现有 cohort 嵌套拷贝风格）
- `tests/test_horizon_run_metadata.py`（扩展 payload/落库，或同文件新 class）
- `tests/test_report_dual_horizon.py`、`tests/test_report_social_context.py`（单双档/部分失败/读取回显；社交 context 仍在）

## 现网缺口（`664a76b`）

- `AnalyzeRequest` 已有 `horizons_resolution_source` / `horizons_notice` / `horizons_explicit`，但 `create_initial_state` **未传** `horizon_resolution`，图内仍走 default short 元数据。
- `_run_job_inner` 约 2874 用 `_normalize_analysis_horizons` 只收回 `resolved` 列表，二次归一化不得把 default 翻成 explicit。
- `_build_horizon_result` / `_build_result_payload` 不拷贝 `horizon_run_metadata`。
- 双档聚合后的 `result_data` 无请求级 resolved 列表与 profile。

## 契约

1. 组装 `HorizonResolution`（或 `to_dict()` 兼容 mapping，且 `resolved` 不得用空列表静默当 short）传入所有 `create_initial_state`。切片 `horizon=` 仍是本图档；metadata.resolved 是**整次请求**已解析列表。
2. 最终/部分失败/读取回显：`result_data`（及 dual 嵌套档）含 `horizon_run_metadata`：requested、resolved、resolution_source、profile_id、primary_eval_offsets、cutoff、investment_horizon；无 `evaluation_eligible: true`。
3. 旧报告无该字段：读取时标 `legacy`/`unknown`，不回填 T+40。
4. 不改缓存键、不裁原始池、不新增表/列。
5. `trading_graph.propagate` / `propagate_async` 无 HTTP 显式档时保持 H-02a 缺省（default short），不要用 `user_intent.horizons` 冒充 explicit。

## 测试

先写失败测再接线。命令：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_horizon_run_metadata.py tests/test_report_dual_horizon.py tests/test_report_social_context.py tests/test_trading_graph_multi_horizon.py -q
```

覆盖：显式 medium 落库 source=explicit；未提供 default short；双档根级 resolved 为双档且各切片 horizon 仍单档；部分失败仍留痕；读取旧 payload 无字段不填 T+40；社交 context 仍在。不要全仓当验收。一个 commit，push 功能分支，评论完整 40 位 SHA、父提交、`git diff --stat`、真实 pytest 计数。
