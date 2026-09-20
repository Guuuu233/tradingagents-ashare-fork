# H1b 修复再次重放到最新主线 c2a52a9

## 背景

DAV-1056 修复已在 `43bf20b825f4a6336308069d65c80a3b2ddc04fa` 交付并通过 DAV-1060 同 SHA 复审；组合全量回归（父线 `061f007` + 候选）为 `4974 passed / 0 failed / 1 skipped / 6 deselected`。

复审期间远端主线又前移到：

`c2a52a9eec798df257fe157012c872806d3132f2`

该主线新增的是社交 Gate0 文档/脚本/测试变更，实测未包含 H1b 修复：
- `research_manager.py` 的两处 `excluded_evidence` 比较仍是 `e.get("claim_id")`；
- `tests/test_expectation_revision_contract.py` 存在，但没有候选新增回归内容。

因此旧候选不能直接合入，旧 PASS 也不转移到新父线。

## 精确任务

- 最新远端主线：`c2a52a9eec798df257fe157012c872806d3132f2`
- 旧已审候选：`43bf20b825f4a6336308069d65c80a3b2ddc04fa`
- 目标：从最新主线重放同一 H1b 修复，生成新远端 branch/SHA。
- 白名单仍严格为：
  - `tradingagents/agents/managers/research_manager.py`
  - `tests/test_expectation_revision_contract.py`

## 要求

1. 在干净工作树从 `c2a52a9` 开始，cherry-pick/等价重放 `43bf20b8` 的 H1b 修复。
2. 不改变修复语义：历史字符串/None/非 Mapping `excluded_evidence` 保留、不参与 claim_id 去重、不当作 claim；Mapping 仍按 claim_id 去重；重入幂等。
3. 新候选推送远端，报告完整 SHA、直接父、白名单 diff stat。
4. 使用 `.venv310`、`env -u PYTHONPATH` 跑：
   - `tests/test_expectation_revision_contract.py`
   - 4 个 research_manager 定向测试文件
   - compileall
   - git diff --check
5. 不合入主线、不部署、不写生产库、不改用户个人配置。
6. 新 SHA 交付后停止修改，等待“代码审核员”对新 SHA 同 SHA 复审；最后一行精确 mention 项目调度助手。
