# DAV-175 当前主干基线红测：测试契约修复（仅测试）

## 固定基线

- target trunk：`codex/dav-4-p2a-trunk@abf10fc48ca12d760211633e46a5728cc2bace2e`
- DAV-174 已确认：15 个失败均为 target trunk 既有，原因是 fail-closed `fund_flow_consensus_guard` 已进入生产节点，而旧测试 fixture 未提供该字段；当前 collector 窄修与 15 项失败关联为 0/15。
- 失败族：arbitration 2、custom_prompt_injection 6、evidence_citation_density 1、prompt_semantics 6。

## 目标

只修复测试契约/fixture，使全量回归恢复可解释、可重复；不得放宽生产 fail-closed guard，也不得让测试绕过真实业务行为。

## 允许修改

- 仅允许修改上述 4 个失败测试文件及必要的测试 fixture/helper 文件；
- 禁止修改生产代码、数据源、配置、数据库、用户模型/provider/API Key、主干和服务；
- 不得把 blocked guard 改成默认放行来“修绿”测试；测试若验证正常 prompt/LLM 路径，应显式提供非阻断且结构完整的 guard；测试若验证缺证据路径，应更新断言为 blocked。

## 验收

1. 在独立 checkout 的 `.venv310` / Python 3.10.x 上先逐项确认原失败，再修改测试；
2. 修改后只跑 15 个原失败 nodeid，报告逐项结果；
3. 跑四个完整测试文件，报告 passed/failed/skipped；
4. `compileall`（tests）和 `git diff --check` 通过；
5. 提交到独立远端分支并提供精确 branch/SHA、改动文件和测试结果；
6. 不宣称 DAV-124/DAV-125 解锁，不合入、不重启、不上线；完成评论不要 mention 项目调度助手，避免自触发循环。

这是一个独立测试契约修复泳道，不修改当前已交付的 collector 代码。
