# DAV-856 / DAV-859 合入与部署证据

## 结果

- 候选完整 SHA：`a227cdc3bb466edf2e910419cb6013cfc021d309`
- 候选父提交：`b92acd15bc20d6a1bcc699f3ea7723fcb8868b37`
- 父链上一层（本轮施工主干）：`54077b6ad4a287bbd8c43d386e91427662a5e786`
- 远端主干：`codex/dav-4-p2a-trunk`
- 合入方式：非强制快进；远端回读与候选 SHA 逐位一致
- 候选相对 `54077b6` 的改动文件恰为白名单 6 项：
  - `api/main.py`
  - `api/services/custom_prompt_service.py`
  - `tests/test_custom_prompt_e02_guard.py`
  - `tests/test_custom_prompt_injection.py`
  - `tradingagents/agents/managers/research_manager.py`
  - `tradingagents/agents/utils/prompt_injection.py`
- `git diff --check`：通过

## 质量门禁

- DAV-857：红队覆盖复核，补充 RT-13 至 RT-16；已完成。
- DAV-858：首次审查曾错误放行弱化的既有断言，已保留为 blocked 历史，不采纳其 PASS。
- DAV-859：恢复既有快照路径的严格 `source.count(call) == 3`，并新增阻断分支快照测试；已完成。
- DAV-860：由 `代码审核员` 对完整 SHA `a227cdc3bb466edf2e910419cb6013cfc021d309` 只读复审，PASS；已完成。
- 修复候选全量：`19 failed, 4268 passed, 1 skipped, 3 deselected`；失败集合与线上基线的 19 项逐项一致，无新增失败。
- 修复后的聚焦测试：T14/T14b `2 passed`；红队 `28 passed`；相关回归 `82 passed`。

## 部署前后

- 部署前活跃报告：`pending/running = 0`。
- 部署前 SQLite 备份：`work/tradingagents.db.bak-20260913-deploy-a227cdc`。
- 备份校验：`quick_check=ok`；备份内报告计数 `completed=793, failed=616`。
- 服务工作目录：`/private/tmp/ta-serve-dav854.dsusBF`；数据库仍通过 `data` 链接使用主项目生产库，未改写报告数据。
- 新服务 PID：`35280`，PPID=`1`，监听 `*:8000`。
- 启动恢复日志：`failed=0`；历史案例回填 `scanned=0, backfilled=0`；应用启动完成。
- `/healthz`：HTTP 200，`commit_sha` 与 `build_identity` 均为 `a227cdc3bb466edf2e910419cb6013cfc021d309`。
- 部署后报告计数仍为 `completed=793, failed=616`；生产库 `quick_check=ok`。
- `DAV-808`、`DAV-856`、`DAV-859` 已收口为 `done`；DAV-858 的首次错误审查记录保留为 `blocked`。

## 未做事项

- 未启用信用加权。
- 未进行真实社交采集、Cookie 读取或生产数据回写。
- 未修改当前全局自定义提示词及其 `5489166b29c2` 基线指纹。
