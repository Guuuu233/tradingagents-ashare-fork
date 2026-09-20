# P1-1：NewsEvidence / EventCluster / event_coverage（新闻事件覆盖）

## 目标

D-009 / 审计稿 §P1-1：新闻路径补上**结构化事件证据**与**覆盖率**，堵住「检索没命中 → 当成没有新闻」和「后补/未来新闻混进 cutoff 前证据」。

本卡产出确定性库 + 离线测 + **最小**接线。不做 P1-2 capitulation、不做 P1-3 回测、不做社交、不部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `3466e05a6483861cf071d4548b7bee990ac3c774`
- **新建**隔离分支，例如 `agent/dev2/p1-1-news-event-coverage`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要 FF 主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §7 P1-1、§4 新闻时间、§10.1 `test_news_event_coverage.py`
- `work/2026-08-27-decision-semantics-workflow.md` P1-1 验收钉子：R2「7/29 可见、8/11 future 不可见」
- D-008 时间分层语义：`published_at` = 内容时间；`first_seen_at` 只作 archive/审计；**不得**用 ingest/now 回填缺发布时间

## 行为契约（锁死）

改**原路径**；禁止 `_v2` / 平行旁路。

### 1. 结构化类型（新建模块，建议）

`tradingagents/dataflows/news_event_evidence.py`（风格对齐 `fund_flow_evidence.py`）：

`NewsEvidence` 至少含：
- `entity` / `theme`（可空字符串，不可 silently drop）
- `published_at`（必填；解析失败 → **拒绝该条**，记入 unverifiable，禁止填 today）
- `first_seen_at`（可选；不得替代 published_at 做资格）
- `source` / `source_hash`
- `title` / `summary`（摘要）
- `direct_impact` / `transmission_chain` / `expected_lag`（可 optional/null）
- `public_before_cutoff: bool`（由 cutoff 比较得出）

`EventCluster`：按实体/主题/时间窗口去重聚类；重复标题/同 source_hash 不得计成多个独立事件。

`event_coverage`（dict，可序列化进 state）：
- `requested_themes`：本轮检索/关注主题列表
- `hit_count` / `hit_cluster_ids`
- `unverifiable_count`（缺时间/解析失败）
- `suspected_gaps`：请求了但 0 命中的主题（**文案必须是「未检索到/不可验证」，禁止写成「确认无相关新闻」**）
- `cutoff` / `window`

### 2. 资格规则

对 cutoff=`trade_date`（日界按项目既有约定，显式写进测）：

| 条件 | 结果 |
|---|---|
| `published_at` 无法解析 | 拒绝；`unverifiable++`；不得进方向证据 |
| `published_at` > cutoff | 拒绝（future）；不得进方向证据 |
| `published_at` ≤ cutoff | 可进 cluster / coverage hit |
| 仅有 `first_seen_at`、无 `published_at` | **不得**当历史已知 |

### 3. R2 钉子（离线 fixture，禁止测时打网）

cutoff = `2026-07-30`：

- 一条 `published_at=2026-07-29…` 的跨市场/财报相关事件 → **可见**，进入 coverage hit
- 一条 `published_at=2026-08-11…`（或半年报 future）→ **不可见**
- 一条缺时间或乱时间 → unverifiable，不进 hit
- 两条同主题近重复 → **1 个** EventCluster（或明确 cluster_id 相同）

不得宣称工业富联/蓝思「案例已修」；只能宣称本卡规则与 fixture 测通过。

### 4. 接线（最小）

允许二选一或都做，但必须可测：

- A. `news_analyst`（或 `data_collector` 写 pool 后）调用 coverage，把 `event_coverage` 写入 node 返回 state；prompt 注入**紧凑** coverage 摘要（不是原始大表）。
- B. 从现有 `get_news` markdown（`### … [发布时间：…]`）做确定性 parser 喂入模块——**解析失败拒绝**，禁止填默认时间。

不要大改全部 vendor `get_news` 返回类型；本卡以库 + fixture + 轻接线为主。若必须动 `cn_akshare_provider.get_news`，只加结构化旁路字段或纯函数提取，禁止平行 `_v2` 实现。

### 5. Prompt 一句（可选）

`prompts/zh.py` / `en.py` news 相关：**「event_coverage 未命中 ≠ 确认无新闻；不可验证项不得当利多/利空」**。一句即可，禁止大改新闻人格/框架。

## 允许修改

- `tradingagents/dataflows/news_event_evidence.py`（**新建**）
- `tradingagents/agents/analysts/news_analyst.py`（注入 coverage / state）
- 必要时 `tradingagents/graph/data_collector.py` 或 `propagation.py` 增加 state 键（只加键，不大重构）
- `tradingagents/prompts/zh.py` / `en.py`（仅一句）
- `tests/test_news_event_coverage.py`（**新建**，审计稿点名）
- 可选极小 fixture 目录：`tests/fixtures/news_events/`（仅本卡离线 JSON）

## 禁止

- 社交 DAV-460、capitulation、回测/校准、confirmation 闸、资金流、cluster 计票、财务 period_kind
- 实网 pytest；缺 fixture 时编造「蓝思已修」
- 改 3/1、开 `credit_weighting_enabled`、部署、碰脏文件
- `@pytest.mark.asyncio`（仓内无插件；异步用 `asyncio.run`）

## 测试（TDD）

先在精确基线 SHA 上写**会失败**的测试，再实现。

`tests/test_news_event_coverage.py` 至少：

1. `published_at` 缺失/不可解析 → 拒绝 + unverifiable
2. future `published_at` > cutoff → 拒绝
3. cutoff 前事件 → hit；重复条目 → 单 cluster
4. R2 迷你 fixture：7/29 可见、8/11 不可见
5. coverage：请求主题 0 命中 → `suspected_gaps` 存在，且文案/字段不得表示「确认无新闻」
6. （若接线）news_analyst 返回含 `event_coverage` dict

验证命令：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest tests/test_news_event_coverage.py -q --tb=short
```

## 交付

1. 推送分支；完整 40 位 tip SHA；父提交必须是 `3466e05…`
2. 定向 pytest 精确数字（Cursor 会独立复跑，不采信口头）
3. 相对主干 `git diff --stat`；文件白名单内
4. 明确：未部署；未宣称 R2/蓝思案例已修
5. 完成后把卡置 `in_review`，@独立代码审核员可省略——Cursor 总控复审

## 不可违反

D-006/007/008/009/010；AGENTS.md 铁律。
