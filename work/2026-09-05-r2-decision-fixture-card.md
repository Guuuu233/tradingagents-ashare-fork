# R2：补决策语义完整离线夹具（工业富联，不宣称案例已修）

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` 须为含 C-09/C-05旁证文档的 tip（开工写 40 位 SHA）。  
**一个关注点：** 把 R2 从「仅新闻 PIT 切片」补成与 R1/R3 同目录的决策语义冻结夹具。  
**禁止：** 改生产分析链路、部署、开加权、编造缺失的价格/财报/公告数字、声称工业富联案例已修复。

## 现状

- R1：`tests/fixtures/decision_semantics/r1_goertek_fixture.json`
- R3：`tests/fixtures/decision_semantics/r3_lens_fixture.json`
- R2：`tests/fixtures/decision_semantics/manifest.json` 仍指向 `../news_events/r2_news_fixture.json`

## 允许改

- `tests/fixtures/decision_semantics/r2_foxconn_fixture.json`（新建）
- `tests/fixtures/decision_semantics/manifest.json`
- `tests/test_decision_semantics_fixtures.py`
- 可继续引用既有 `tests/fixtures/news_events/r2_news_fixture.json`，不要改坏其 cutoff/hit_count 钉子

## 契约

1. 键集合对照 R1（缺的字段显式 `missing`/`unavailable`，禁止填今天、禁止填 0 冒充已取到）。
2. `symbol=601138.SH`，`trade_date=2026-07-30`。
3. 新闻 PIT 既有断言仍绿：`hit_count==1`、`unverifiable_count==2`、`future_rejected_count==1`。
4. 测试不得把 R2 标成 `calibration_eligible=true`（D-009：夹具齐备前不得当校准样本）。
5. 权威句仍有效：R1/R2/R3 离线 fixture 齐备并通过前，不得声称历史案例已修复。本卡只能声称「R2 决策语义夹具文件存在且测试按冻结值通过」。

一个 commit，push，40 位 SHA。禁止 FF/部署。
