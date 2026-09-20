# P2-T15a：端到端验收（夹具 / 无删 legacy）

## 目标

在主干 tip `7db2882…` 上补齐 Task 15 的**可自动化验收**，用夹具锁住：

1. 定向社交测试 + 关键回归相对本轮基线 **0 新增失败**（报告精确数字与基线对比）
2. **历史 smoke（夹具）**：cutoff 前 snapshot 可用；cutoff 后才出现的 `snapshot_at` 不得把新 likes 带入资格/方向
3. **合成端到端**：xhs/dy 各至少 1 post + 一级评论的 archive 夹具 → import/provider/collector/adapter 路径可跑通；记录 native id 与四时间字段；**不**读真实 Cookie、**不**打外网

## 明确不做（本卡禁止）

- **不删除** `legacy_proxy` / 适配层 legacy 臂 / 旧 social prompt 新闻措辞（那是 **Gate 4 / T15b**，须另卡 + 独立 commit + 单独「准予合入」）
- 不改默认 `TA_SOCIAL_MODE`（保持 `disabled`）
- 不部署；不开生产 canary 放量
- 不把「真实外网 smoke」当作合入前置（plan 写人工 Gate 不阻塞代码合入；本卡用夹具代替）

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `7db2882d43276c22dd87e259b259dd1f500c6bd0`
- **新建**隔离分支，例如 `agent/dev2/p2-t15a-social-e2e-acceptance`
- 禁止 FF / 部署 / `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 15（验收项）+ Gate 2/3 测试期望；**Gate 4 删除项本卡跳过**
- D-008 / D-009 / D-010

## 文件白名单

优先只加测试与必要夹具：

1. `tests/test_social_e2e_acceptance.py`（新建）
2. 可选夹具目录：`tests/fixtures/social/`（sqlite 或建库 helper；无密钥）
3. 若必须最小改产品代码才能测通：仅限白名单内且须在评论说明理由；默认 **零产品改动**

禁止改：`analyst_adapter.py` legacy 删除、`prompts/*` 去新闻措辞、`social_media_analyst.py` 删 legacy 路径。

## 验收用例（必须）

### A. 回归基线

在隔离 worktree 跑（无代理）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q \
  tests/test_social*.py \
  tests/test_data_collector_social_integration.py \
  tests/test_report_social_context.py \
  tests/test_import_mediacrawler_social_cli.py \
  tests/test_run_social_ingestion_guards.py \
  tests/test_social_e2e_acceptance.py
```

交付评论写清：**passed 精确数字**（及 warnings）。相对 tip `7db2882` 不得引入失败。

### B. 历史 PIT smoke（夹具）

构造同一 native post 两条 snapshot：

- `snapshot_at <= cutoff`：旧 likes
- `snapshot_at > cutoff`：更高 likes

断言：as_of=cutoff 的资格/聚合 **不得**采用后一条的互动数。

### C. 双平台最小图

夹具含 xhs + dy 的 post + 一级评论；经 provider（或 collector）在 shadow/active（测内设 mode）下能产出 context；断言时间字段齐全；日志/返回无 cookie。

### D. disabled 仍安全

默认 disabled：不触 archive；adapter 仍 `legacy_proxy`（本卡未删 legacy，行为不变）。

## 交付

- 单关注点 commit，建议：`test(social): add e2e acceptance fixtures without removing legacy`
- **先 push**，`git ls-remote` 可见完整 40 位 tip；父提交 = `7db2882…`
- pytest 精确数字 → `in_review`
- 不自行 FF；不 @调度助手合入

## 后续（不要做在本卡）

**T15b / Gate 4**：独立卡 + 独立 commit `refactor(social): remove legacy social proxy after activation`；`disabled` 变为社交不可用、不再新闻替代。须 Cursor 显式批准开卡。
