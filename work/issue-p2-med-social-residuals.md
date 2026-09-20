# P2-MED：社交残留清理（M2 / M3 / M4 / M7；可选 M5）

## 目标

消化回顾审计 `work/audit-retro-p2-t5-t11.md` 中仍开放的 Medium 项（**M6 已在 T14 修复，勿重复**）。提高正确性与可观测性；**不**删 `legacy_proxy`、**不**开 Gate 4、**不**部署、**不**改默认 `TA_SOCIAL_MODE`。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `c1ec33e7adf95b2889e015038b78dd4ac2233fd5`
- **新建**隔离分支，例如 `agent/dev2/p2-med-social-residuals`
- 禁止 FF / 部署 / `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 范围（按 ID；**每个 ID 单独 commit**）

| ID | 问题 | 期望修复 | 建议文件 |
|---|---|---|---|
| **M2** | 缺失 `ingest_run` 时硬编码 `crawler_commit` | 缺 run 元数据则显式失败/拒绝该批或记 typed reason，**禁止**捏造 commit 字符串 | `mediacrawler_importer.py` + 测 |
| **M3** | `ingest_at` 用字符串 tie-break | 解析为 datetime（UTC）再比较；非法时间拒绝该条并打日志，禁止填「现在」 | `provider.py` / importer 相关 + 测 |
| **M4** | `_log_state` 漏 `social_data_context` | 日志摘要含 mode/status/`direction_allowed`/reason 计数等安全字段；**禁止**打正文/cookie | 含 `_log_state` 的 graph/collector 路径 + 测 |
| **M7** | aggregator 去重后 0 行标 `partial` | 应为 `empty`（或契约等价）+ 对应 reason；不得当 partial 可用 | `aggregator.py` + `tests/test_social_aggregator.py` |
| **M5**（可选，时间允许） | 过宽 `except Exception` / 错误码误标 | 收窄捕获、区分超时/结构异常；补日志；测锁错误码 | 触及点最小集 |
| **M1**（本卡默认不做） | `fetch_records` 过长 | 纯拆分重构，另卡 | — |

## 禁止

- Gate 4 / T15b：删除 `legacy_proxy`、改 disabled=新闻替代语义
- 改辩论轮次 / 加权 flag / 部署
- 把多个 MED 揉进**同一个** commit（违反「一次一个关注点」）

## 测试

每个 commit 附带能红→绿的定向测。整卡交付前跑：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_mediacrawler_importer.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_social_e2e_acceptance.py
```

## 交付

- **先 push** 分支；`git ls-remote` 可见 tip
- 评论列出：**每个** commit 的完整 40 位 SHA + 对应 MED ID + 各测数字
- 最终 tip 父链须含基线 `c1ec33e…`
- → `in_review`；不自行 FF；不 @调度助手合入

## 后续

Gate 4 / T15b 仍须 Cursor/用户显式「开 Gate4」后另卡。
