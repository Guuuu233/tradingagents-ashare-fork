# 新窗口入口（2026-08-26 ~00:43 UTC+8）

`PROJECT_STATE.md` 里旧 P2 段已过期。**以本文件 + 实时 Git / Multica / `/healthz` / DB 为准。**

## 一句话

**P2 已收口并部署。** tip/healthz=`11309037de9334820603eec6dd801f291172f6ed`。H1b 门槛用户已批准（推荐值 + 分层隔离）。当前唯一在施：`DAV-433`（资深开发1 实施信用加权，**默认关**）。

## 实时核验（写交接时）

| 项 | 值 |
|---|---|
| 分支 | `codex/dav-4-p2a-trunk` |
| HEAD / `target/codex/dav-4-p2a-trunk` | `11309037de9334820603eec6dd801f291172f6ed` |
| `/healthz.commit_sha` | 同上；`executor_threads=4` |
| uvicorn | PID `9356`，`.venv310`，`127.0.0.1:8000` |
| DB | `data/tradingagents.db` |
| 持久轮次 | **3/1**（未改） |
| v2 completed（含 `v2_structured_disagreement`） | **10** |
| 在途分析 | 0 |
| 样本补齐 / 5m loop | 已停 |

## Multica 看板

| Issue | 状态 | 含义 |
|---|---|---|
| DAV-421 | **done** | P2-M 五战场监控 PASS（n=10） |
| DAV-429/430/431 | **done** | data_gaps 分类 → FF → 部署（即当前 tip） |
| DAV-432 | **done** | H1b 门槛草案；用户批准推荐值 + 分层隔离 |
| **DAV-433** | **in_progress** | H1b 实施（分层隔离 + `verify_h1b_gates.py`，flag 默认 false） |

施工远端常需：`git -c http.proxy=http://127.0.0.1:7897`。

## 正在跑

1. **DAV-433 资深开发1** `6050b57e-f551-4756-8ad9-3af522d7d4e3`  
   - 写交接时：run `01a039cb-5440-…` **running**；另有 queued  
   - 较新 Multica worktree 目录（在 `…/a9f7c79e-936b-441c-9895-d70c6ff76b54/` 下）：`6fe94c6c3420`、`0a8c18795a70` 等（以 issue runs 的 `work_dir` 为准）  
   - 规格正文：`work/issue-v2-p3-h1b-impl.md`  
   - 批准门槛：`work/p3-h1b-activation-gates-draft.md`（Approved）  
   - 批准备忘：`work/2026-08-26-p3-h1b-approved.md`  
   - 决定：`DECISIONS.md` **D-006**

2. **无**样本补齐脚本、**无** 5m 巡检 loop（刻意停掉）

## 角色 UUID（稳定）

- 独立代码审核员 `aa01a41a-c3da-4021-9e45-a592ac77166c`
- 资深开发1 `6050b57e-f551-4756-8ad9-3af522d7d4e3`
- 资深开发2 `5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc`
- 代码运维测试员 `f179edb8-9a81-4dbd-8787-afbfd307eda4`
- 项目评估师 `2c03cc8f-6628-4464-954a-84c47079fdf3`
- 项目规划与写作助手 `aaf3d3f5-184a-4c5d-8953-d4e0b9b8a0e1`
- 项目调度助手 `826abb3f-…`：**只编排，禁止施工**；误 @ 要 cancel

## 铁律（本阶段）

- 不改用户模型绑定 / Key / 持久 **3/1**
- 不自行 FF；审核 PASS ≠ 已部署；**FF 卡禁止重启**，部署另卡
- Multica 从**仓库根**跑，勿进 Multica workdir（残留 daemon context 会炸 CLI）
- Host 工作树很脏：禁止 reset / clean
- 证据用 `.venv310`，系统 Python 全量 pytest 不算
- H1b：`credit_weighting_enabled` **默认 false**；系统级门槛不过不得加权；单模型偏置只 clamp 该模型
- 不开「已加权上线」直到门槛脚本 + 审核 + 你批准开 flag
- 可选后续：H2 周评脚手架（零加权）— 未开卡

## 新窗口建议顺序

1. 读 `AGENTS.md` → **本文件** → `DECISIONS.md`（含 D-006）；再查 healthz / tip / Multica / DB  
2. 盯 **DAV-433**：`issue get` / `runs` / `comments`；有候选 SHA 后独立 `.venv310` pytest（`test_shadow_credit` + 新增 H1b 测）  
3. 审核员只读复审 → 另开线性 FF 卡 → 另开部署卡  
4. Multica agent 有 diff 时：在聊天贴 `git show --stat` / 关键 hunk（本 IDE 不会自动出 Multica worktree diff）  
5. 调度助手若被误触发：立即 `cancel-task`

## 关键路径

- 本交接：`work/2026-08-26-handoff-new-window.md`
- 门槛草案：`work/p3-h1b-activation-gates-draft.md`
- 实施卡正文：`work/issue-v2-p3-h1b-impl.md`
- P2 收口备忘：`work/2026-08-26-p2-complete-p3-entry.md`
- 规格：`/Users/davidliu/Downloads/TradingAgents-AShare-v2-完整详细施工实施规格-2026-08-24.md` §11
- H1a 已有影子信用相关代码与 `tests/test_shadow_credit.py`（以仓库内实际路径为准）
