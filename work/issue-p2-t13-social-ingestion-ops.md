# P2-T13：外置采集控制与文档（修订后 Task 13）

## 目标

补齐 **bounded MediaCrawler 工作流**：导入脚本 + 受控启动脚本 + `docs/social_data/` 下的 runbook / 契约 / XHS `last_update_time` 验证说明。只接本地库、只追加 archive；**不**改分析图、**不**删 `legacy_proxy`、**不**部署、**不**默认开 active。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`（含 T12）
- **新建**隔离分支，例如 `agent/dev2/p2-t13-social-ingestion-ops`
- 禁止 FF / 部署 / `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 13
- D-008（时间分层 / append-only）
- D-009 / D-010
- 已有库：`tradingagents/dataflows/social/mediacrawler_importer.py` + `tests/test_mediacrawler_importer.py`（复用，勿重写导入核心）

## 文件白名单

1. `scripts/import_mediacrawler_social.py`（新建）
2. `scripts/run_social_ingestion.py`（新建）
3. `docs/social_data/runbook.md`（新建；**不得**放 `work/`）
4. `docs/social_data/data_contract_v1.md`（新建）
5. `docs/social_data/xhs_last_update_time_verification.md`（新建）
6. 相关测试（新建或扩展），建议：
   - `tests/test_import_mediacrawler_social_cli.py`
   - `tests/test_run_social_ingestion_guards.py`
   （可用 tmp sqlite / mock subprocess；禁止真连外网、禁止读真实 Cookie 文件进断言）

禁止改：`tradingagents/graph/*`、分析师、`report_quality_gate`、辩论轮次、加权 flag、删 `legacy_proxy`。

## 行为契约

### A. `import_mediacrawler_social.py`

必需 CLI 参数（缺一非零退出）：

- `--source-db`
- `--archive-db`
- `--platform`
- `--query`
- `--crawler-commit`

调用既有 `MediaCrawlerImporter`（或等价公开 API）。打印/返回 run 摘要即可；不得把帖子正文打到日志默认级别。

### B. `run_social_ingestion.py`

1. 启动爬虫必须 `save_option=sqlite`；启动后校验工作库确有目标表；若为 JSONL/其他 → **非零退出**。
2. 只允许连 `127.0.0.1`（含 DB / 控制面）；拒绝其它 host。
3. 已有 running 任务时 **不**并发第二任务。
4. Cookie 登录路径：文档与脚本说明清楚；**不得**把 cookie/token 写入仓库、测试 fixture、日志或 Multica 评论。
5. 默认：`enable_comments=true`、`enable_sub_comments=false`（可测常量/flag）。
6. MediaCrawler 钉住文档中的 commit（plan 记载 `d6f7c5bb…`）；`--crawler-commit` 必须传入并写入 ingest 审计字段。

### C. 文档（均在 `docs/social_data/`）

| 文件 | 必须写明 |
|---|---|
| `runbook.md` | 工作库可原地更新；archive **只追加**；本机 loopback；如何跑 import / run；失败退出码；Gate 4 前默认 disabled |
| `data_contract_v1.md` | 字段/时间语义对齐 D-008（`first_seen_at`/`snapshot_at`/`ingest_at`/平台源时间） |
| `xhs_last_update_time_verification.md` | `last_update_time` 可靠性验证步骤与「验证通过前不参与历史资格」 |

文档禁止：真实 Cookie、账号密码、生产路径密钥、把 `work/` 当权威文档。

### D. 测试（TDD）

至少覆盖：

1. import CLI 缺任一必需参数 → 非零 + 明确错误
2. import 正常路径（tmp source/archive）→ 调用导入且成功码 0（可 mock importer）
3. run：非 sqlite / 缺目标表 → 非零
4. run：非 `127.0.0.1` host → 拒绝
5. run：已 running → 拒绝第二任务
6. 默认 comments true / sub_comments false 可断言

## 交付

- 单关注点 commit，建议 message：`feat(ops): add bounded MediaCrawler ingestion workflow`
- 完整 **40 位 SHA** + 父提交（须为 `c0bfac5…`）+ diffstat + pytest 精确数字 → `in_review`
- 不自行 FF；不 @调度助手合入；等独立审核员 + Cursor「准予合入」

## 明确不做

- MED M1–M7 清理（另卡）
- Task 14 shadow/canary、Task 15 Gate 4 删 legacy
- 真实外网爬取验收（可放后续 smoke；本卡以脚本守卫 + 本地 fixture 为准）
