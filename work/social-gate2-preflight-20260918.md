# Social Gate 2 前置核验

日期：2026-09-18（AWST）

## 当前实测状态

- 远端主线：`c2a52a9eec798df257fe157012c872806d3132f2`
- 8000 运行服务：`6ee148699339efefc2f7f7548eb286be485524e1`
- `/v1/social-data/status`：HTTP 200，`mode=disabled`，整体 `status=disabled`。
- 平台状态：`xhs=inactive`、`dy=inactive`。
- 服务 `.env` 未设置 `TA_SOCIAL_MODE` 或 `TA_SOCIAL_ARCHIVE_DB`，因此没有生产社交归档输入。
- 独立沙箱 archive：2 次成功运行（xhs/dy），357 条快照，93 条实体提及；生产库未写入。

## Gate 2 真正还差什么

1. **发布当前主线**：把 `c2a52a9` 部署到服务 worktree，并做 `/healthz`、数据库守恒和只读接口核验。
2. **准备生产级 append-only archive**：不能直接把沙箱路径当作生产配置；需明确归档位置、权限、备份和导入边界。
3. **继续积累真实覆盖**：至少 10 个代表性标的、30 份真实分析报告，覆盖不同板块和关注度。
4. **单独开启 shadow**：设置 `TA_SOCIAL_MODE=shadow` 后重启服务；shadow 只记录 bundle 和 trace，不得改变报告正文和最终方向。
5. **人工审计**：逐份核对 `social_data_context`、`bundle_id`、`direction_allowed=false`、缺口语义和新闻/社交隔离；完成 30 份后才能进入 Gate 3 讨论。

## 明确未执行

- 没有部署 `c2a52a9`。
- 没有设置 `TA_SOCIAL_MODE=shadow` 或 `active`。
- 没有把沙箱 archive 接入生产服务。
- 没有触发会写生产报告的真实分析。
- 没有开始 Gate 3 active canary。

因此当前结论是：**Gate 0 沙箱已通过，Gate 2 尚未开始；生产仍保持 disabled。**
