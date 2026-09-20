# 回顾审计：P2-T5…T11 未走独立审核员的主干代码（只读）

## 背景

D-010 补强前，社交卡 P2-T5…T11 曾跳过「独立代码审核员」，仅 Cursor 隔离复测后线性 FF。现按用户指令：**先查缺补漏，全部审完确认无问题后再继续施工**（含暂停 T12 返修合入）。

本卡**只读**。禁止改代码、FF、部署。

## 审计范围（精确）

| 项 | 值 |
|---|---|
| 基线（不含） | `ca4747c8653afe1de83f410b666537e511a26b0f` |
| 终点（含，当前主干 tip） | `68ae241bdf9c148654f551fb67b7e5f2ec56dba4` |
| 区间 | `ca4747c..68ae241`（7 commits：T5…T11） |
| 远端 | `origin/codex/dav-4-p2a-trunk` @ `68ae241bdf9c148654f551fb67b7e5f2ec56dba4` |

### Commits

1. `50ec109fe9992db11c00a98a3a07f3ad760bf3df` P2-T5 archive provider
2. `bdefe4f85f61d5308280846e44ff69ca93bc3116` P2-T6 sentiment bundle
3. `ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd` P2-T7 social collector modes
4. `a375bdc9cf3d07584eb6c28c637bde9bca867876` P2-T8 data collector social
5. `46995ac19bb4894dc6cea328f299951eb12698c5` P2-T9 state/graph wiring
6. `0cc34278c8024680e0b687bd029295876b6e0c98` P2-T10 ToolNode no get_news
7. `68ae241bdf9c148654f551fb67b7e5f2ec56dba4` P2-T11 analyst separation

约 30 文件 / +6482 行（以 `git diff --stat ca4747c..68ae241` 为准）。

## 权威契约

- `docs/social_data/implementation_plan.md`（尤其 D-008 时间分层、§5.5 ledger、§六 新闻/关注度/社交分离、Gate 4 前保留 legacy）
- `DECISIONS.md` D-008 / D-009 / D-010
- `AGENTS.md` 数据层规范（按列名、失败显式、禁止空串冒充）

## 必查清单（对照证据写 ✅/❌ + 路径:行号）

### A. 时间与前视（D-008）

- `add_ts`→`first_seen_at` only；`last_modify_ts`→`snapshot_at` only
- 互动指标资格 `snapshot_at <= cutoff`；`ingest_at` 不参与资格
- 非法/未来 as_of → refused，禁止填「今天」

### B. 失败语义

- archive 缺失 / schema / lock → typed failed/timeout，非空串继续
- empty/insufficient/not_applicable **不得**写成 failed gaps（§5.5）
- `except Exception` 是否过宽且无日志

### C. 模式与分离

- 默认 `TA_SOCIAL_MODE=disabled`；disabled 不读 archive（或等价不触库）
- active 无 news/`get_news` 回退；ToolNode social 无 get_news
- news_analyst 不读 social sentinel；social 不读 news sentinel（T11）

### D. 接线完整性

- DataCollector → state → propagator → graph → api/main 三处 `create_initial_state`
- `_log_state` 等是否漏字段（非阻塞可记 MED）

### E. 禁止项未破

- 未删 `legacy_proxy`；未改辩论 3/1；未开 `credit_weighting_enabled`；未部署

### F. 测试

在 tip `68ae241` 上用 `.venv310`：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_*.py \
  tests/test_data_collector_social_integration.py \
  tests/test_report_social_context.py \
  tests/test_analyst_prompts_deep_reasoning.py \
  tests/test_trading_graph_multi_horizon.py
```

报告精确 passed/failed。

## 交付格式

按独立审核员报告模板：总体评级 ✅/⚠️/❌；High/Med/Low；任务完整性；**明确写「建议 Cursor 认定回顾通过 / 需开返修」**。禁止写「准予合入」「可以 FF」。禁止 @调度助手催合入。

## 暂停施工

审计未通过前：不得推进 P2-T12 FF、不得开 T13+、不得部署。
