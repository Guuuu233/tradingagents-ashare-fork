# P2 交接（2026-08-25 ~23:12 UTC+8）— 新窗口入口

`PROJECT_STATE.md` 仍是 08-24 快照。以本文件 + 实时 Git / Multica / `/healthz` / DB 为准。

## 一句话

P2-G3 已部署在 `50679db`。正在：**（1）真实跑分析把 v2 样本补到 10**；**（2）DAV-429 做 data_gaps 结构性分类**。不要空等、不要开 H1b、不要改 3/1 / 模型绑定。

## 实时核验（写交接时）

| 项 | 值 |
|---|---|
| 远端主干 `target/codex/dav-4-p2a-trunk` | `50679db31a7fa1908f5ba91d54aa7c39711605a0` |
| `/healthz.commit_sha` | 同上 |
| uvicorn PID | `38498`（`.venv310`，`127.0.0.1:8000`） |
| DB | `data/tradingagents.db`；用户 `429163f7-…` / `davidliu022305@gmail.com` |
| 持久 3/1 | `max_debate_rounds=3`，`max_risk_discuss_rounds=1` |
| v2 completed（`result_data` 含 `v2_structured_disagreement`） | **3** |
| 已有 v2 标的 | `000858.SZ` / `000063.SZ` / `000651.SZ`（trade_date 2026-08-24） |
| 在途分析 | `fa31da3d0a294effaff872095e0bff86` = **000001.SZ** / `2026-08-25` / `running` |

## Multica 看板

| Issue | 状态 | 含义 |
|---|---|---|
| DAV-421 | **blocked** | 五战场监控；n≥10 前不得验收。用户已下令用真实分析补样本 |
| DAV-426 | done | P2-G3 财报带公告日备用源实现 |
| DAV-427 | done | 线性 FF → `50679db` |
| DAV-428 | done | 部署；健康探针已对上 |
| DAV-429 | **in_progress** | P2-10.4 `data_gaps` structural/operational 分类；资深开发2 |

施工远端：`https://github.com/Guuuu233/1.git`（常需 `git -c http.proxy=http://127.0.0.1:7897`）。

## 正在跑的本机进程

1. **v2 样本补齐（优先盯）**
   - 脚本：`work/run_v2_sample_fill.py`（支持 resume：先 poll 在途 job，再串行 QUEUE）
   - 日志：`work/v2-sample-fill.log`
   - 覆盖：只 `config_overrides: {"v2_debate_enabled": true}`，**不要**改持久轮次
   - 队列（在 000001 之后）：`000333.SZ` `000725.SZ` `002415.SZ` `600036.SH` `600519.SH` `601318.SH`
   - 目标：库内 v2 completed ≥ **10**
   - 注意：首轮编排 shell 曾掉线；交接时应确认 `pgrep -fl run_v2_sample_fill`，没有就：
     ```bash
     cd /Users/davidliu/Documents/TradingAgents-AShare
     env -u PYTHONPATH .venv310/bin/python work/run_v2_sample_fill.py
     ```

2. **DAV-429 writer**
   - Agent：资深开发2 `5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc`
   - 分支/worktree：`agent/2/672b8f4758de`  
     `/Users/davidliu/multica_workspaces_desktop-api.multica.ai/a9f7c79e-936b-441c-9895-d70c6ff76b54/672b8f4758de/workdir/1`
   - 写交接时 HEAD 仍停在基线 `50679db`，未见提交；允许文件见 issue body / `work/issue-v2-p2-104-gap-class.md`
   - 候选 SHA 出来后：独立 `.venv310` pytest → 审核 → 单独 FF 卡 → 单独部署卡（FF 卡禁止重启）

3. **5 分钟巡检 loop**
   - PID 曾为 `56635`；prompt 文案仍写「不刷单」——**已过时**，以用户最新指令为准（必须跑分析补样本）
   - 新窗口应用 live 状态，不要第二份 `while true` 除非确认旧 PID 已死

## 角色 UUID（稳定）

- 独立代码审核员 `aa01a41a-c3da-4021-9e45-a592ac77166c`
- 资深开发1 `6050b57e-f551-4756-8ad9-3af522d7d4e3`
- 资深开发2 `5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc`
- 代码运维测试员 `f179edb8-9a81-4dbd-8787-afbfd307eda4`
- 项目主管 `4503de74-0fb3-457d-88a7-3db8db04ff8e`
- 项目评估师 `2c03cc8f-6628-4464-954a-84c47079fdf3`
- 项目调度助手 `826abb3f-…`：**只编排，禁止当施工代理**；重复 self-trigger 要 cancel

## 铁律（本阶段）

- 不改用户模型绑定 / Key / 持久 3/1
- 不自行 FF 主干；不把「审核 PASS」当已部署
- Multica 从仓库根跑，勿进 Multica workdir（残留 daemon_task_context 会炸 CLI）
- Host 工作树很脏：禁止 reset / clean
- 系统 Python 全量 pytest ≠ 证据；用 `.venv310`
- 不开 P3 / H1b（信用加权）

## 新窗口建议顺序

1. 读 `AGENTS.md` → 本文件 → `DECISIONS.md`；再查 healthz / trunk / Multica / DB
2. 确认 `run_v2_sample_fill` 在跑；否则 resume 重启
3. 盯 `work/v2-sample-fill.log` 与 v2 计数；满 10 后独立验收再唤醒 DAV-421（mention 评估师，取消调度助手）
4. 并行盯 DAV-429：有候选 SHA 再独立测 / 审核 / FF / 部署

## 规格位置

- `/Users/davidliu/Downloads/TradingAgents-AShare-v2-完整详细施工实施规格-2026-08-24.md`（§10.1 战场 / §10.4 gaps）
- Issue bodies：`work/issue-v2-p2-*.md`，`work/issue-v2-p2-104-gap-class.md`
