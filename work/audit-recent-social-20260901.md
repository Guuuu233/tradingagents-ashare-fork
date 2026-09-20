# Cursor 近期社交代码总审（2026-09-01）

范围：主干 tip `0d7a67e…` 上已合入社交轨（约 T5…T15a + H1 + MED M2–M7）+ 候选 MED2 `883bded…`。  
方法：独立 worktree 静态契约核对 + 定向 pytest；辅以子代理并行深审。

## 总评

**无 HIGH。** 已修 H1 / M6 / M2–M4 / M7 主契约成立。  
**MED2 `883bded`：准予合入**（行为保持型拆分，未引入新语义缺陷）。

## 已确认健全

- D-008：`ingest_at` 不参与资格；非法 as_of 拒绝；不填「今天」
- H1：lookback 空窗 → `empty`，不进 structural ledger
- disabled 不打开 archive；shadow `source_mode=legacy_proxy` 且 `direction_allowed=False`
- active 不回退 news；canary 未命中不 silently active
- M7：去重后 0 行 → `empty` 非 `partial`
- status API / 日志：无帖子正文 / Cookie 内容

## 新发现（主干已存在，非 MED2 引入）

| ID | 级别 | 摘要 | 建议 |
|---|---|---|---|
| **R1** | MED | `metrics_json` 损坏 → 未捕获异常被外层标成 `social_archive_missing` | 行级拒绝或 `social_archive_corrupt` |
| **R2** | MED | 缺 `crawler_commit` 拒行后整体常落 `empty`/`social_empty`，掩盖元数据完整性问题 | 独立 reason；收紧 M2 断言 status/reason |
| **R3** | MED | `DEFAULT_CRAWLER_COMMIT` 在 importer / `run_social_ingestion` 仍可省略并捏造 provenance（空串已拒） | 与 import CLI 一样强制显式 commit |
| L1–L5 | LOW | 适配层虚构 cutoff 展示、宽 `except`、字符串 sort、verifier mode/status 混用、死 import | 另卡清理 |

## MED2

- SHA：`883bdedb64693d6f1a9923a9b515243a0677d89f`
- 独立审核 DAV-530：✅
- Cursor：核心 76 passed；扩展 rollout/analyst/report 续跑
- 结论：**准予合入**；R1–R3 另开修复卡，不挡 MED2

## 下一步

1. FF MED2  
2. 开 **P2-MED3**：R1 + R2 + R3（各单独 commit）  
3. Gate 4 / T15b 仍须显式「开 Gate4」
