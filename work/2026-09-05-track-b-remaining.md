# Track B 剩余项（2026-09-05）

不重开舆情设计。权威仍是 `docs/social_data/implementation_plan.md`。本文件只补**完成态分类**和未闭环清单。

## 两种状态（不得混用）

| 状态 | 含义 | 当前 |
|---|---|---|
| **代码交付** | 契约、导入器、分析链接线、模式守卫、Gate4 删除 `legacy_proxy` 已合入主干 | 已交付（DAV-545 等） |
| **真实启用验收** | 受控采集、归档积累、shadow 30/10、canary 2–5、报告可追溯到合格时间切片 | **未闭环** |

`TA_SOCIAL_MODE=disabled` 只表示分析端不消费社交归档。外部 MediaCrawler 与导入可以独立跑。有没有持续采集，要看采集任务、源库和导入记录，不能只看该开关。

**不得**把「Gate4 删除项完成」写成「Gate0–4 真实启用验收通过」或「舆情完整接入完成」。

## Gate 对照（截至 2026-09-05，主干 `b9e7238`）

| Gate | 原计划 | 代码交付 | 真实启用验收 |
|---|---|---|---|
| 0 合规与环境 / 小样本导入 | 钉 SHA、sqlite、xhs/dy 各一轮导入 | 脚本与文档有 | **未证明**持续采集与导入记录 |
| 1 离线契约 | 四时间字段、append-only | 测试夹具已交付 | 不替代真实库 |
| 2 shadow | 夹具 + 人工 30 份/10 股 | 模式守卫已交付 | **30/10 未做** |
| 3 canary | 2–5 股 active | 模式守卫已交付 | **未授权、未做** |
| 4 全量 active + 删 legacy | 删 legacy 且全量 | **删除项已合入**；默认仍 disabled | **全量 active 未做** |

## 已合入代码上的缺陷（不能归因于「尚未启用」）

1. 社交 `not_applicable` / `direction_allowed=false` 时，提示词与 formatter 仍把结论推向「中性」，报告出现「完全真空」「未进入散户视野」等市场事实表述。
2. `_build_result_payload` 不带 `social_data_context`；近 50 条 completed 报告该字段为空。
3. `run_social_ingestion` CLI 不传 `crawler_cmd`；`record_social_run_summary` 无调用方；status 可凭 archive 文件存在报 `operational`。

## 施工顺序（已授权：拆卡/实现/测试/独立审查）

1. 缺口语义 + API 留痕 + 隆基/爱尔脱敏回归（三 commit）
2. 受控采集入口（对接真实 MediaCrawler 启动接口）+ 状态分项诚实化
3. 真实采集 / shadow / canary **验收方案**（承接原门槛，不降标准）。执行采集或切 mode 需另授权。

本次**不含**：部署、启用 active、扩大采集、改账号/Cookie、开加权、打断 C-05（DAV-646/647）。
