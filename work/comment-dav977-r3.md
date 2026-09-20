## 本卡改为复审 DAV-941 返修候选 `ae54e52d`

此前对 `f317e824` 的审查结论（❌ 打回）已生效并完成使命，**不计入本次门禁**。请对下面这个新的完整 SHA 重新执行只读复审。

### 版本边界（运维已 `git ls-remote` 预核实）

- 候选完整 SHA：`ae54e52d016cbeb2da5fda0ec146776703319838`
- 精确远端 ref：`origin/agent/1/01a0a2b8-dav-941-r3`
- 直接父：`8854853cfc167fd9bb015528138ae36a1df6bf8f`（= 交付当时也是当前的主线 tip，已核对一致）
- 目标主线：`origin/codex/dav-4-p2a-trunk` = `8854853cfc167fd9bb015528138ae36a1df6bf8f`

### 严格白名单（仅 2 个文件）

1. `tradingagents/knowledge/historical_cases.py`
2. `tests/test_historical_cases.py`

### 复审重点

- 上一版被打回的问题是否**真正修掉**，而不是靠调整测试断言让红灯变绿：请对比 `f317e824` 与 `ae54e52d` 的差异，确认修复发生在产品代码而非仅在测试侧。
- 历史案例 T+1 行情拒绝与重复日冲突的处理是否有**前视偏差**：任何用到 `curr_date` 之后数据的路径都必须拒绝，且拒绝须为可识别的类型化结果，不得是普通字符串或静默回退。
- 边界：重复日期、冲突值、缺失日期、非法日期字符串、空数据集。

### 红队场景（逐条实跑，贴实际输出）

- RT-1 正常单日查询 → 预期正常返回
- RT-2 同一日期出现冲突重复记录 → 预期按契约拒绝或去重，说明依据
- RT-3 请求日期晚于可用数据（T+1 场景）→ **必须拒绝**，不得回退到最近可用日
- RT-4 非法 / 空日期 → 明确错误，不得静默返回全量
- RT-5 空数据集 → 不得抛未捕获异常
- RT-6 与调用方交互：确认拒绝结果不会被上游当成有效数据继续使用

### 环境与回归

必须用 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python` 并 `env -u PYTHONPATH`，报告贴 `-V`（须 `Python 3.10.20`）。

⚠️ **RT-FULL 现状已变**：原 `--deselect tests/test_fund_flow_scale_consumption.py::...` 方案**已被运维证伪，无效**。主干实测存在三类挂死：
1. 38% 处 `tests/test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation`（`%CPU=0` 锁阻塞，仅在全量上下文出现，单独跑 0.18s 通过）
2. 96% 处高 CPU 空转
3. `tests/test_knowledge_rag.py` 分文件执行时也会挂

上述均为**主干既有**，不计为本候选新增失败。请勿反复重跑全量。**推荐做法**：采用运维已验证的分文件对照——对 `tests/test_*.py` 逐个独立进程执行（120s 看门狗），并在候选与其直接父 `8854853` 两侧使用完全相同切分，再逐项对照失败集合。基线参考（`8854853` 的前身 `b95a9b88` 实测）：`214 OK / 8 个失败文件 / 1 个挂死文件`，失败文件为 `test_cninfo_disclosure_metadata`、`test_dav27_report_semantics`、`test_debate_state_persistence`、`test_game_theory_integration`、`test_provider_date_guards`、`test_signal_processing`、`test_social_data_api`、`test_two_stage_analyst_topology`。

有任何**新增**失败即不得 PASS；若只做定向测试，须说明限制且结论不得写成无条件 PASS。

### 禁止项

不修改代码、不合入、不部署、不重启、不写生产库、不改个人配置。你**不是**本候选实现者，可正常复审（D-011 §3）。

[@代码审核员](mention://agent/c732eba5-bbdd-40ac-b2c6-e0be14c0d3be)
