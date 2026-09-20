# DAV-874 前候选 f27 全量回归证据（2026-09-13）

候选：`f27fed0e8ee2565d7f7269f1fe2365b3eac82c16`

直接父：`2fbfb6729c7b340335b1ec9661fdba7e6b1d8545`

解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`

命令：`python -m pytest -q`，在 DAV-871 隔离副本运行；未修改代码、线上服务或生产库。

结果：`20 failed, 4326 passed, 1 skipped, 3 deselected, 176 warnings in 1148.72s`。

父版本已有证据：`19 failed, 4291 passed, 1 skipped, 3 deselected`，失败集合为既有 19 项。

候选相对父版本新增失败 1 项：

`tests/test_evidence_citation_density.py::test_adjudication_chain_cites_evidence_densely`

同文件复跑：父版本 `2 passed`；候选 `1 failed, 1 passed`。候选失败为经理引用密度 `0.17 < 0.67`，日志显示新 E-04 守卫把原有带证据引用的经理计划整体改成阻断文本。该回归与红队发现的 E-04 穿透问题一并进入 DAV-874，当前候选不得合入或部署。
