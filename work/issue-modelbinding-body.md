## 目标
修复角色模型绑定不生效：设置里给每个角色配置的模型（role_bindings）未在分析流程中消费，所有角色静默回退到默认模型（常规/推理）。

## 根因（Hermes 已定位并验证修复方案）
- `tradingagents/graph/trading_graph.py:100-113`：角色模型解析依赖 `self.config.get("db")` 或 `self.config.get("user_id")`，二者存在时调用 `resolve_all_roles(db, user_id, runtime_config)` 生成 `role_llms`。
- 但 `api/main.py:_build_runtime_config`（:1333）返回的 config **不含 `user_id` 键**（实测确认 `'user_id' not in config`）→ graph 里 `db=None, user_id=None` → resolved_roles 为空 → 所有角色用 `quick_think_llm`/`deep_think_llm` 默认模型（用户看到的"只有常规和推理两类模型"）。
- 实测：`resolve_all_roles(db, '429163f7-...', config)` 能正确解析全部 15 角色绑定（market=gemini-3.6-flash-high、bull=opencode/qwen3.8-max、trader=opencode/qwen3.7-max、risk_manager=opencode/minimax-m3、research_manager=opencode/glm-5.2 等）——只需传入 user_id。

## 修复建议（已验证的最小改动）
- `api/main.py:_build_runtime_config` 返回前：`if user_id: config["user_id"] = user_id`
- graph 已有 `elif user_id:` 分支（`get_db_ctx()` 新 session 调 resolve_all_roles），无需改 graph
- 覆盖所有调用方（_run_job_inner 主 graph + dual-horizon 第二 graph + warmup 等）
- 注意：不要传 db session 进 config（跨线程/await 不安全），只传 user_id，让 graph 自开 session

## 测试要求（TDD）
- 新增/扩展测试：构建 runtime config 后断言 `config["user_id"] == user_id`（或 mock graph 构造验证 resolve_all_roles 被以正确 user_id 调用）
- 若已有 graph 模型解析测试（role_llms），补一条"config 带 user_id 时 role_llms 使用绑定模型"的用例
- 全量回归 0 新增失败

## 验收标准
- `_build_runtime_config(..., user_id=X)` 返回 config 含 `user_id=X`
- 分析时日志出现各角色绑定模型初始化（如 market 角色 → gemini-3.6-flash-high），不再全部是默认模型
- 全量回归 0 新增失败；完成后评论汇报，等 Hermes 验收
