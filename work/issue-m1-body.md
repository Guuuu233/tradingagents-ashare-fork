项目规划：work/2026-08-04-development-plan.md（David 已授权 Hermes 全权指挥 multica 专业小队）。

## 背景
Hermes 在宿主机完成 10 项修复（交易日历fallback/近窗豁免/中文前置/3000字提示词恢复/SSRF allowlist/新浪资金流备用源/数据库路径/start.sh/socksio/max_tokens），全部未提交（工作区39文件）。分支 codex/dav-4-p4-allowlist-clean，HEAD 7860cf9。

## 任务
1. 只读核验：审查员独立审查这 10 项改动（git diff），出具审查报告
2. 按 AGENTS.md 铁律分 commit 提交（一次一关注点，改原路径）
3. 全量回归跑绿（env -u PYTHONPATH .venv310/bin/python -m pytest tests/）
4. 修复时间敏感测试 test_get_zt_pool_rolls_back_and_labels_actual_date（mock 时钟）

## 验收
- 10 项修复全部干净提交（不混入其他 29 个脏文件）
- 全量测试通过
- 审查报告附在 issue

完成小段工作后自动 @项目调度助手 触发下一轮。
