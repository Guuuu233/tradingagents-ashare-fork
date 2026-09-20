# Track A9：`verify_h1b_gates --db-path` 必须真正读该库

## 背景（Cursor 实测 2026-09-02）

主干 tip：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`。

`scripts/verify_h1b_gates.py` CLI 接受 `--db-path`，但 `load_reports_from_db` **从不使用**该参数，只走 `get_db_ctx()`（看 `DATABASE_URL`）或 golden fallback。

复现：
- 不设 `DATABASE_URL`、传 `--db-path` 指向真实库 → 落到 golden，仅 **3** 条样本，门槛数字不可信。
- 设 `DATABASE_URL=sqlite:////…/data/tradingagents.db` → 731 completed → 69 合格 v2；行业数 **22**（A7 已生效）。总评仍 `KEEP_FALSE`（空侧 16<25、交易日 18<30、多头 74%）。

这破坏 A6「门槛数字诚实」契约。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `018fdef6f79c82fc8b24e2ac4630774f57cf6338`
- 分支建议：`agent/dev2/a9-h1b-gates-db-path`
- origin：`https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- **单关注点 commit。** 不要 FF / 部署。

## 只做这件事

1. 当传入 `--db-path`（或 `run_verify(db_path=…)`）时，**只读**打开该 SQLite，加载 `status=completed` 的 `ReportDB` 行；优先于默认 `get_db_ctx()` 与 golden。
2. 路径不存在 / 无法打开 → **明确失败**（非零退出或抛可测异常 + 日志），禁止静默掉进 golden。
3. 未传 `--db-path` 时保持现有行为（`get_db_ctx` → golden）。
4. 过滤口径不变：仍只计合格 v2（与 A6 `filter_v2_completed_reports` / `is_qualifying_v2_report` 同路径）。
5. 定向测试：夹具 sqlite 或 monkeypatch 证明 `--db-path` 被使用；坏路径不落到 golden。

## 明确不做

- 开 `credit_weighting_enabled` / 宣称 `ELIGIBLE_FOR_ACTIVATION`
- 部署 / schema / `ReportDB.industry` 列
- A0 / `frontend/src/services/api.ts`
- 改门槛阈值本身、改 3/1、社交
- 脏文件 trio：`AGENTS.md`、`api.ts`、`work/h1b_gates_report.json`

## 验收

- 测试证明显式 db-path 加载该库样本
- 坏路径不静默 golden
- `tests/test_h1b_gates.py`（及必要新增）绿
- push → 完整 40 位 tip → `in_review`；挂独立审核（D-010）

## 权威

D-006 / D-007；A6 门槛只计 v2；本卡只修 CLI/加载诚实性。
