# Track A10：backfill 脚本兑现 `--db-path`（A9 同款诚实性）

## 背景（Cursor 实测 2026-09-02）

主干 tip：`4493177eeefa4a7aabfc05904c156ccb0106d06e`（A9 已合入）。

A9 修了 `scripts/verify_h1b_gates.py` 的 `--db-path`。同模式缺陷仍在：

| 脚本 | CLI 有 `--db-path` | `load_raw_reports` 是否使用 |
|---|---|---|
| `scripts/backfill_report_industry.py` | 有 | **否** → 只走 `get_db_ctx()` / golden |
| `scripts/backfill_tplus5_shadow.py` | 有 | **否** → 同上 |

无 `DATABASE_URL` 时传 `--db-path` 会静默落到 golden，回填统计不可信；写路径还可能写错库。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `4493177eeefa4a7aabfc05904c156ccb0106d06e`
- 分支建议：`agent/dev2/a10-backfill-db-path`
- origin：`https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- **单关注点 commit**（两脚本同一缺陷类，可同 commit）。不要 FF / 部署。

## 只做这件事

1. 两脚本的 `load_raw_reports(db_path=…)`：传入非空 `db_path` 时优先打开**该** SQLite。
   - 路径不存在 / 无法打开 → 明确失败（抛异常；CLI 非零退出），**禁止**静默 golden。
   - 未传 `db_path`：保留现有 `input_file` → `input_dir` → `get_db_ctx` → golden。
2. 写回语义：
   - `--dry-run`：可只读打开目标库统计，不写。
   - 非 dry-run：对**该** `db_path` 的 `ReportDB` 行更新既有槽位（industry / T+5 shadow），不得改其它字段、不得改 schema。
3. 可参考 A9 `verify_h1b_gates.load_reports_from_db` 的路径解析与失败阻断；回填需要可写 session 时不要照搬 `mode=ro`。
4. 定向测试：夹具 sqlite 证明 `--db-path` 被使用；坏路径不落 golden；dry-run 不写库。

## 明确不做

- 改门槛阈值 / `shadow_credit` T+5 分母逻辑（另卡）
- 开加权 / 部署 / schema 列 / A0
- 社交 / 脏文件 trio：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 对生产库做真实回填（本卡只交代码+测试；真实回填另授权）

## 验收

- 两脚本相关 pytest 绿（新建或扩 `tests/test_*backfill*` / 现有 industry / tplus5 测）
- push → 完整 40 位 tip → `in_review`；D-010

## 权威

A9（DAV-553）；A5 / A7 回填脚本合同；D-007 门槛诚实性。
