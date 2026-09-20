# 最终方案：数据/裁决修补 × 社交舆情接入

日期：2026-08-27  
状态：**统一方案稿，产品代码均未实施**  
基线：以当前 Git HEAD 为准（2026-08-27 晚核验 `de88de4…`）；文内旧 SHA `aa41f44…` 仅作历史引用

> **2026-08-27 晚更新（D-009）**  
> 决策语义 / PIT / 回测污染的权威设计改为  
> `work/2026-08-27-audit-decision-semantics-plan.md`，  
> 派工入口为 `work/2026-08-27-decision-semantics-workflow.md`。  
> **Track A 施工顺序以 D-009 的 P0→P1 为准**；本文件其余禁令（不开加权、不改 3/1、不混社交 commit）继续有效。

本文件是双轨合入后的施工总纲。细节仍以源文档为准；**与 D-009 / 审计稿冲突时，以 D-009 为准**。

| 源文档 | 角色 |
|---|---|
| `work/2026-08-27-audit-decision-semantics-plan.md` | **决策语义权威审计（D-009）** |
| `work/2026-08-27-decision-semantics-workflow.md` | **当前派工入口** |
| `work/2026-08-27-data-verdict-repair-plan.md` | Track A 证据细节（服从 D-009 顺序） |
| `docs/social_data/implementation_plan.md` | Track B 唯一社交实施方案（时间语义第二轮） |
| `DECISIONS.md` D-006/D-007/D-008/**D-009** | 持续有效决策 |
| `PROJECT_STATE.md` | 进度快照（双窗口共用，禁止写「本窗口」） |

---

## 0. 一句话目标

在**不开加权、不改 3/1 轮次、不取消历史快照拒绝**的前提下：

1. **Track A**：修掉财报假缺口与缺 K 线 fail-open；压生成侧偏多；把 v2 winner 接到 T+5 校准。  
2. **Track B**：按时间语义第二轮，把 MediaCrawler 接成 append-only social archive → provider → collector → prompt，经 Gate 0–4 再删 `legacy_proxy`。

两轨路径大部分不重叠，但会抢同一批文件；**必须按 §5 顺序，禁止混 commit。**

---

## 1. 全局禁令（违反即失败）

1. 不开 `credit_weighting_enabled`，直到 `verify_h1b_gates` → `ELIGIBLE_FOR_ACTIVATION`（D-006/D-007）。  
2. 不改辩论轮次 **3/1**。  
3. 不取消 `snapshot_historical_refusal`（板块资金流 / 雪球热搜 / 质押 / 股东截面）。  
4. 不把「资金流对打 → 一律改空/中性」当全局规则（16 场反事实会把符号命中从 50% 打到 37.5%）。  
5. **禁止**实现旧时间映射：`content_observed_at=add_ts`、`metric_observed_at=last_modify_ts` 当作平台正文/互动时间（D-008）。  
6. 分析链上不启动爬虫；不新增不会被 bind 的 social LangChain tool；不对 `social_record_snapshots` 做 UPDATE/DELETE。  
7. 禁止 `git add .`；已跟踪脏文件 `AGENTS.md` / `frontend/src/services/api.ts` / `work/h1b_gates_report.json` 不得进社交提交。  
8. 未确认本方案前：不派 Multica、不改 `tradingagents/` 产品路径。  
9. 社交 Issue 标题必须写「修订后 Task N」，禁止沿用更早未修订的 Task 描述。

---

## 2. 时间语义第二轮对照表（Track B 绑定）

钉住 MediaCrawler SHA `d6f7c5bb906b6dac40ddf343ef9e26438a3de092`。

### 2.1 旧映射 → 新映射

| 项 | 第一轮（已废弃） | 第二轮（唯一有效） |
|---|---|---|
| `add_ts` | 当作 `content_observed_at`（正文观察时间） | 只映射 `first_seen_at`（爬虫**首次写入**工作库） |
| `last_modify_ts` | 当作 `metric_observed_at`（互动发生时间） | 只映射 `snapshot_at`（爬虫**该版工作行最后写入**） |
| 平台发布时间 | 未与库务时间严格分列 | XHS 帖 `time`；XHS 评 / DY 帖评 `create_time` → `published_at` |
| XHS `last_update_time` | 易被当成正文编辑时间 | → `source_updated_at`；**单独验证通过前不参与资格** |
| 指标资格 | 易与库务/导入时间混淆 | **一律** `snapshot_at <= cutoff` |
| `ingest_at` | 曾有回填风险 | 只做 archive 审计；**永不**参与资格，也不得回填缺失源时间 |
| 历史存储 | 易继承工作库 update-in-place | TradingAgents archive **append-only**；分析只读 archive |

### 2.2 三类时间（不得混用）

| 层 | 字段 | 来源 | 用途 |
|---|---|---|---|
| 平台源时间 | `published_at` | 见上 | 内容何时发布 |
| 平台源时间 | `source_updated_at` | XHS `last_update_time`；其余常 null | 仅存储；trusted 前不参与资格 |
| 爬虫库务 | `first_seen_at` / `snapshot_at` | `add_ts` / `last_modify_ts` | 发现时间 / 该版快照时间（指标） |
| 归档审计 | `ingest_at` | 导入器时钟 | 何时写入 TA archive |

正文资格（摘要）：`window_start ≤ published_at ≤ cutoff` 且 `first_seen_at ≤ cutoff`。  
指标资格：候选快照已满足 `snapshot_at ≤ cutoff`。  
后补抓取：`published_at` 早但 `first_seen_at` 晚于 cutoff → 排除。

### 2.3 源表映射速查

| 平台 | 表 | `published_at` | `source_updated_at` | `first_seen_at` | `snapshot_at` |
|---|---|---|---|---|---|
| 小红书帖 | `xhs_note` | `time` | `last_update_time` | `add_ts` | `last_modify_ts` |
| 小红书评 | `xhs_note_comment` | `create_time` | null | `add_ts` | `last_modify_ts` |
| 抖音帖 | `douyin_aweme` | `create_time` | null | `add_ts` | `last_modify_ts` |
| 抖音评 | `douyin_aweme_comment` | `create_time` | null | `add_ts` | `last_modify_ts` |

---

## 3. Track A — 数据缺口 / 裁决偏多 / T+5

审计结论（细节见源方案）：假缺口是账本 as_of；真缺口多为设计拒绝；偏多在生成侧；n=16 T+5 符号命中 50%，机械改多头不能抬准确度。

| ID | 关注点 | 主要文件 | 验收 | 明确不做 |
|---|---|---|---|---|
| **A1** | 财报假缺口：有数字无 ISO 日期 → 勿记「获取失败」 | `data_collector.py` provenance | 有数字无日期不得进失败 gaps | 不取消 snapshot refusal |
| **A2** | 缺日线 OHLCV → fail-closed | 市场分析师路径 + manager / `extract_and_validate_manager_verdict` | 688981 类夹具禁止 bull/bear | — |
| **A3** | few-shot / Hold 去偏 | `prompts/zh.py`（及仍用的 en） | 新样本 dispute 多方占比下降 | 不承诺 n=16 T+5 立刻上升 |
| **A4** | 资金流对打默认 tie；禁「流出=吸筹」 | `evidence_verifier` / manager 后处理（改原路径） | 对打夹具 dispute=tie | 不把全部多头改中性/空 |
| **A5** | v2 winner → T+5 校准管线 | calibration / shadow metrics | `/v1/calibration` sample_size>0 | 等 08-24/08-25 到期再报准确度 |
| **A6** | 门槛脚本只计 v2 | `scripts/verify_h1b_gates.py` | 分侧/行业数字可信 | 不过关不开加权 |
| **A0** | 前端 chat 强制 v2 | `frontend/src/services/api.ts`（已脏，未提交） | override 单独 commit | 不进社交 commit |

每个 ID **一个 commit**。先展示 diff，确认后再合。

---

## 4. Track B — 社交舆情（修订后 Task 1–15）

主路径（唯一）：

```text
MediaCrawler 工作库 (update-in-place, sqlite)
  → Importer (只读源表 → INSERT snapshot)
  → social archive (append-only)
  → SocialDataProvider → DataCollector（_fetch_all 之后、独立短超时）
  → social_data_context → analyst / manager / verifier / report
```

### 4.1 任务摘要

| Task | 内容 | Commit 主题 |
|---|---|---|
| **B1** | collector 非法 as-of fail-closed（禁回退 now） | `fix(data): fail closed on invalid collector as-of` |
| **B2** | raw/bundle contract + archive schema | `feat(social): define raw and bundle contracts` |
| **B3** | MediaCrawler 导入器 append-only | `feat(social): import MediaCrawler snapshots into archive` |
| **B4** | 实体解析 | `feat(social): add deterministic equity entity resolver` |
| **B5** | 只读 provider + 资格护栏 | `feat(social): add read-only archive provider registry` |
| **B6** | 分类 / 采样 / bundle | `feat(social): build deterministic sentiment bundle` |
| **B7** | SocialDataCollector + 配置 | `feat(social): configure social collector modes` |
| **B8** | `_fetch_all` 之后读社交；拆 market_attention | `feat(data): collect social context after market fetch` |
| **B9** | State / Propagator / Graph / **api/main 三处** | `feat(graph): propagate social evidence context` |
| **B10** | social ToolNode 去掉 get_news；不新增 social tool | `fix(graph): stop social analyst tool fallback to news` |
| **B11** | social/news 输入分离 + prompt 四段 | `feat(analyst): separate social sentiment from news and attention` |
| **B12** | 报告 / ledger 映射 / 下游方向守卫 / status API | `feat(report): persist social context and enforce directional guard` |
| **B13** | 导入脚本 + runbook + last_update_time 验证文档 | `feat(ops): add bounded MediaCrawler ingestion workflow` |
| **B14** | shadow / canary 守卫 | `feat(social): add shadow and canary rollout gates` |
| **B15** | 端到端 + **独立提交删除 legacy_proxy** | `refactor(social): remove legacy social proxy after activation` |

Gate 0 合规环境 → Gate 1 离线契约（含 `xhs_last_update_time_verification.md`）→ Gate 2 shadow → Gate 3 active canary → Gate 4 删 legacy。未完成 Gate 4 不得宣布接入完成。

`TA_SOCIAL_MODE` 默认 `disabled`；`active` 需验收。`direction_allowed=false` 时不得把社交分数当多空证据。

完整文件地图、测试矩阵、验收命令见 `docs/social_data/implementation_plan.md` §九 / §十二 / §十四。

---

## 5. 冲突文件与合入规则

### 5.1 谁改什么

| 文件 / 区域 | Track A | Track B | 规则 |
|---|---|---|---|
| `data_collector.py` | A1 provenance | B1 日期解析；B8 社交挂载 | **串行**：B1 → A1 → … → B8 |
| `prompts/zh.py` (+en) | A3 few-shot / Hold | B11 social 四段 + 切新闻措辞 | **串行**：A3 先于 B11 |
| `evidence_verifier.py` | A4 资金流 tie | B12 社交不可用源 | **串行**：A4 先于 B12 |
| `research_manager.py` | A2/A4 相关 | B12 direction_allowed 注入 | **串行**：A 侧裁决修补先于 B12 |
| `frontend/src/services/api.ts` | A0 | **禁止** | 仅 A 单独 commit |
| `AGENTS.md` / `DECISIONS.md` / `PROJECT_STATE.md` | 双方可能改 | 双方可能改 | 合入前**手工合并**，禁止互相覆盖 |
| `work/h1b_gates_report.json` | A 运维产物 | **禁止**入社交 commit | 勿当产品改动 |
| `tradingagents/dataflows/social/**` | **禁止** | B2–B7 | 社交独占 |
| `api/main.py` 三处 init | 原则上不碰 | B9 / B12 | 社交独占接线 |
| snapshot refusal / H1b flag / 3/1 | **禁止改** | **禁止改** | — |
| D-008 时间语义 | **禁止改** | 只按第二轮实施 | — |

### 5.2 推荐统一施工顺序

```text
Phase 0  确认本最终方案；停手对齐脏工作树；不派 Multica
Phase 1  A0 前端 v2 override（单独 commit，先合或不合由 David 定）
Phase 2  B1  collector 非法日期 fail-closed
Phase 3  A1  财报假缺口账本
Phase 4  A2  缺 K 线 fail-closed
Phase 5  A3  提示词去偏
Phase 6  A4  资金流对打 tie
Phase 7  A5 / A6  校准接线 + 门槛脚本口径（可与下相并行于不同 worktree）
Phase 8  B2→B7  社交新包（与 Phase 7 可并行，若隔离 worktree）
Phase 9  B8→B10  collector 挂载 + graph 接线 + 去新闻 tool
Phase 10 B11→B14 prompt/下游/shadow（A3/A4 已合入后）
Phase 11 B13 运维脚本可与 B4–B12 并行（B3 完成后）
Phase 12 Gate 2→3 人工验收 → B15 Gate 4 删 legacy（独立 commit + 独立 review）
```

同树同时只允许一个写者。隔离 worktree 时仍禁止两边同时改 §5.1 冲突文件。

### 5.3 Multica

- Track A：按 A1–A6 拆卡；标题写清「数据/裁决 Ax」。  
- Track B：标题必须「修订后 Task N」。  
- 确认本方案前 **不创建** issue。

---

## 6. 已确认事实（施工前提）

**Track A**

- 用户 v2 completed ≈ 51–55；winner 约 30 bull / 14 bear / 7 tie。  
- 假缺口：50/51 场财报被标「无验证日期」，正文仍有数字。  
- 真拒绝（勿修）：历史日 `fund_flow_board` / `insider_transactions` / `hot_stocks` / `share_pledge`；北向制度性停更。  
- 真 fail-open：688981 @ 2026-07-22 缺 OHLCV 仍给方向。  
- T+5：n=16，符号命中 50%；`/v1/calibration` 仍 `sample_size=0`。

**Track B**

- 独立架构审查完成；`legacy_proxy` 仅 Gate 0–3 一次性例外，Gate 4 必须删。  
- 方案文件已落地：`docs/social_data/implementation_plan.md`。  
- 全量 pytest 基线未跑完（2126 collected）；旧 80/1 不是基线。验收只跑 `tests/`，并去掉代理环境变量。

---

## 7. 验收总表（摘要）

| 轨 | 最小验收 |
|---|---|
| A1 | 有数字无日期 → 非「获取失败」gap |
| A2 | 缺 OHLCV → 禁止方向 winner |
| A3 | 新跑样本 dispute 多方占比下降；资金流对打更多 tie |
| A4 | 对打资金流夹具 → dispute tie |
| A5 | calibration 对 v2 sample_size>0 |
| A6 | 门槛脚本只计 v2_structured_disagreement |
| B | 测试矩阵 T-A1…T-H4（见社交方案 §十二）；Gate 4 后无 legacy 符号 |
| 合入 | HEAD/healthz 仍可核验；社交 diff 不含 A0/AGENTS/h1b_gates 脏文件 |

---

## 8. 回滚与边界

- Track A：逐 commit 回滚；不回填旧报告的假缺口文案。  
- Track B：改 `TA_SOCIAL_MODE` 并安全重启；不删 archive；无 schema 降级。  
- MediaCrawler 开源版仅个人学习/研究；商业用途必须停开源采集路径。  
- 社交历史从启用日起向前积累；此前报告只能 `social_no_historical_snapshot`。

---

## 9. 下一步（需 David 点头）

1. 确认本最终方案（尤其 §5 施工顺序）。  
2. 决定是否先合 A0（前端 v2），再开 Phase 2。  
3. 确认后再派 Multica / 改 `tradingagents/`。  
4. 合入时手工合并 `PROJECT_STATE.md` / `AGENTS.md` / `DECISIONS.md`。

未确认前：停手展示 diff；不实施；不派发。
