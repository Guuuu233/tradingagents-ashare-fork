# 4b540b0 发布与 H1b 受控真实分析证据（2026-09-18）

## 发布

- 目标 SHA：`4b540b0c9b08d77a12ce7d08cfdf0095288cd350`
- 远端主线回读：`codex/dav-4-p2a-trunk = 4b540b0c9b08d77a12ce7d08cfdf0095288cd350`
- 候选直接父：`8e49a809333868da73f98bd44bb070f0831bf23e`
- 备份：`work/tradingagents.db.bak-20260918-130737-deploy-4b540b0`
- 备份 SHA-256：`28568ecbb856fb80cedef19e8b9e63b822b94d7e2f361545b37a69f52c5b5e64`
- 备份前守恒：`1418 reports / 794 completed / 624 failed / quick_check=ok`
- 服务 PID：`52913`
- 服务 worktree：`/private/tmp/ta-serve-4b540b0`
- `/healthz`：精确 `commit_sha=4b540b0c9b08d77a12ce7d08cfdf0095288cd350`
- `GET /`：200
- `GET /v1/reports`：200
- 伪造 API 路径：404

## 受控真实分析

- 任务：`5fd6de0b3a80403a9841f227efde8651`
- 账户：`429163f7-50b6-4982-8bdf-96ae99506843`
- 标的/日期：`600036.SH / 2026-09-11`
- v2：显式 `v2_debate_enabled=true`
- 结果状态：`completed`
- 协议：`v2_structured_disagreement`
- 运行 SHA：`4b540b0c9b08d77a12ce7d08cfdf0095288cd350`
- 研究经理 winner：`bear`
- 最终 `analysis_status`：`ABSTAIN`
- 原因：E-04 `priced_in` 无可回溯证据，触发一致性硬闸；不是运行失败
- T+5 案例：`600036.SH / 2026-09-11` 实际回填 `+1.16%`
- 原 DAV-998 pending：`600519.SH / eval_date=2026-09-18` 未被提前填充

## 计数变化

受控分析前：`794 completed / 624 failed`。

受控分析后：`795 completed / 624 failed`。

这 1 条是完整真实管线产出的 `completed + ABSTAIN` 报告，**不计入 H1b 合格样本**。正确门槛复核：

- `795 completed -> 133 v2 候选 -> 9 D-009 合格`
- `legacy_unversioned` cohort：仍为 `7/60`
- 系统门槛：`FAIL / KEEP_FALSE`
- `credit_weighting_enabled=False`

## Social 运行态

- `TA_SOCIAL_MODE=shadow`
- 平台：`xhs,dy`
- 最近 xhs ingest：203 读 / 203 写 / 0 拒绝
- 最近 dy ingest：154 读 / 154 写 / 0 拒绝
- `social_record_snapshots=357`
- `social_entity_mentions=93`
- active 未开启
- Cookie 未导出、未保存、未人工读取；采集使用已登录浏览器会话的登录态
