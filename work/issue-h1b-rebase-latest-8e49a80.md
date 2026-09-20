# H1b 修复再次重放到最新主线 8e49a80

## 背景

H1b `double_count_guard` 类型修复已先后在旧基线生成并复审过 `c11c423`、`43bf20b8`、`aa020a0`，但目标主线在复审期间持续前移。当前远端主线已是：

`8e49a809333868da73f98bd44bb070f0831bf23e`

`8e49a80` 相对 `c2a52a9` 只新增 `work/social-gate2-preflight-20260918.md`，但旧候选仍非其后代，旧复审不可转移。

## 精确任务

- 最新远端主线：`8e49a809333868da73f98bd44bb070f0831bf23e`
- 上一候选：`aa020a01bf444207befe82e71b36f02c3b7b2b3b`
- 目标：从 `8e49a80` 干净工作树再次重放完全相同的 H1b 修复，生成新远端 branch/SHA。
- 白名单：
  - `tradingagents/agents/managers/research_manager.py`
  - `tests/test_expectation_revision_contract.py`

## 要求

1. 从最新主线开始，等价重放上一候选 H1b 修复；功能语义必须与已复审版本逐字节等价。
2. 历史字符串/None/非 Mapping `excluded_evidence` 保留、不参与 claim_id 去重、不当作 claim；Mapping 仍按 claim_id 去重；重入幂等。
3. 推送新远端分支，报告完整 SHA、直接父、白名单 diff stat。
4. `.venv310` + `env -u PYTHONPATH` 跑 contract + 4 个 research_manager 定向测试、compileall、diff-check。
5. 不合入、不部署、不写生产库、不改用户配置。
6. 新 SHA 交付后停止修改，等待“代码审核员”新 SHA 同 SHA 复审；最后一行精确 mention 项目调度助手。
