# H1b 修复重放到当前主线

## 目标

把已复审通过的 DAV-1056 修复重放到当前远端主线，生成新的候选 SHA。旧候选 `c11c423e115946c459492ce6ddffa3874223947e` 的直接父是 `6ee148699339efefc2f7f7548eb286be485524e1`，当前主线已前移，不能直接合入或沿用旧复审。

## 精确输入

- 当前远端主线：`061f007ebf58692024c78772cb21e1429695a569`
- 旧候选：`c11c423e115946c459492ce6ddffa3874223947e`
- 旧候选已通过代码审核；在当前主线临时应用预检已 CLEAN_APPLY。
- 允许的最终改动仍只有：
  - `tradingagents/agents/managers/research_manager.py`
  - `tests/test_expectation_revision_contract.py`

## 执行要求

1. 从当前远端主线 `061f007` 建立干净工作树/分支。
2. 将旧候选修复重放到当前主线，不能改功能语义，不能顺手修改其他文件。
3. 保持 `excluded_evidence` 中字符串/None/历史非 Mapping 元素：保留、不参与 claim_id 去重、不当作 claim；Mapping 仍按 claim_id 去重；幂等重入不重复追加。
4. 生成并推送新的远端 branch/SHA；报告完整 SHA、直接父、diff stat。
5. 使用 `.venv310`、`env -u PYTHONPATH` 跑：
   - `tests/test_expectation_revision_contract.py`
   - 4 个 research_manager 定向测试文件
   - `compileall`
   - `git diff --check`
6. 不合入 `codex/dav-4-p2a-trunk`，不部署，不写生产库，不改用户配置。
7. 新 SHA 交付后停止修改，等待对新 SHA 的代码审核；最后一行精确 mention 项目调度助手。
