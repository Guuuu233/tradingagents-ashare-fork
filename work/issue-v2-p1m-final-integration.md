## 固定输入与顺序

- 目标 trunk 基线：`50e115347b49bcb9e767c593296045a356099006`
- P1-M 原候选：`33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`（直接子于 trunk）
- 生产挂载 sibling：`25e52c02971d7459ec8adb2b38b47e8d6cfa70f8`（父=33c6e6b，DAV-375 PASS）
- 指标语义 sibling：`8ccd6391404c05d96ff524de8ba5bd02bb371561`（父=33c6e6b，DAV-373 PASS）
- sibling文件零重叠。

必须在 fresh 独立 checkout 操作，禁止进入/修改宿主项目树。唯一允许的组合顺序：

```text
50e1153 -> 33c6e6b -> 25e52c0 -> cherry-pick 8ccd639
```

不得重放 33c6e6b两次。不得手工复制文件、改代码或改测试。若 cherry-pick冲突，立即停止并BLOCK。

## 组合验收

### 1. Git结构
- 最终应为线性3提交增量（33c6e6b、25e52c0、8ccd639重放提交）；
- 变更总范围必须恰好9文件：原6文件 + propagation.py + trading_graph.py；protocol test是原6内已有测试文件，最终唯一；总文件按 `git diff --name-only 50e1153..HEAD`程序计数去重；
- 禁止 api/main、researcher、conditional_logic、manager、prompt、DB schema、前端、provider、配置、用户设置。
- diff-check、compileall。

### 2. 生产可达性确定性 smoke（不启动服务）
用宿主 `.venv310`：
- `Propagator.create_initial_state` 必须返回 v1 metadata/flags，两个state深拷贝隔离；
- `_build_horizon_result` 对生产形状输出顶层及debate state protocol/flags/data_utilization_metrics；
- 真实 Golden 000333/600900/600276：recycling denominator >0、无代码/日期/INV污染；
- 当前真实报告 fixture：probability note合法空值计分；
- legacy fixture nested state逐字保持，顶层默认metadata可读；
- 不调用LLM/网络/DB；flag关闭。

### 3. 定向与全量
宿主 `.venv310`：
- 所有 P1-M tests；
- Phase 0辩论/证据/graph/report关键矩阵；
- replay verifier；
- 全量 `env -u PYTHONPATH .../.venv310/bin/pytest tests/`，保存原始输出文件并给绝对路径、exit code、最终计数；只运行一次，不重复。

### 4. 交付
- 提交仅用于 cherry-pick产生的重放提交，无额外第四个代码提交；如必须格式修复，停止并报告，不自行加提交。
- 推送远端新branch/SHA；报告完整 ancestry、9文件范围、定向/全量原始结果、限制。
- 未合入主干、未重启、未上线；P1-B仍锁定。

禁止修改主干或服务。不要 mention 项目调度助手。