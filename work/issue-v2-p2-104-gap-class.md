# P2-10.4：data_gaps 结构性分类（常驻 ≤5 可计量）

## 为什么现在开

规格 10.4：`data_gaps` 常驻目标 ≤5，超过必须写明结构性原因；五战场清单与 provenance 一致。P2-G1/G3 已上线。DAV-425 盘点：历史分析下快照拒绝 + 北向停更会把字符串缺口堆到 11–18，其中多数是设计拦截，不是故障。DAV-421 的 n<10 战场统计与本卡无关，禁止刷分析单。

基线：主干/服务 `50679db31a7fa1908f5ba91d54aa7c39711605a0`。隔离 worktree / 独立分支。禁止自行 FF、重启、改 3/1、改模型绑定。

## 要做

1. TDD：先写失败用例。至少覆盖：
   - 北向制度性停更 → `gap_class=structural`（或同等字段名，全卡统一）
   - 历史日 `snapshot_historical_refusal`（如 `share_pledge` / `fund_flow_board`）→ structural
   - 传输/源失败（token、timeout、空表）→ `operational`
   - 分类后「常驻故障」计数只含 operational；structural 仍写入 ledger/prompt，不得删文案、不得改成成功
2. 在现有 `data_failure_ledger` / `source_provenance` 上加分类，不要新建表、不要改 `users`/`role_bindings`。
3. 五战场数据清单与 provenance 的 source 名对齐：同一 source 不得一边叫可用一边进 gap。
4. 解析失败不得填今天。禁止伪称北向。禁止历史日打即时快照。

## 允许文件

- `tradingagents/graph/data_collector.py`
- `tradingagents/dataflows/providers/cn_akshare_provider.py`（仅当必须把 refusal 的 class 传上来；能只改 collector 就不要动 provider）
- `api/services/report_service.py`（仅当 `merge_data_gaps` 必须区分两类；否则不动）
- `tests/test_data_collector.py`
- 如需新测试：只加 `tests/test_data_gap_classification.py` 或扩展 `tests/test_report_data_gaps.py`

禁止改辩论图核心、前端、3/1、模型绑定。不新增 pip 依赖。

验收：`.venv310` 跑上述测试。系统 Python 全量 pytest 不算证据。候选 SHA 推独立分支后 mention 独立代码审核员。不要 mention 项目调度助手。
