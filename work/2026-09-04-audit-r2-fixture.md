# 只读审计：工业富联 R2 完整 fixture 来源与字段清单

**禁止改功能代码、禁止 commit、禁止 push、禁止部署。禁止编造缺失字段。**

## 已知
- R1 歌尔 `tests/fixtures/decision_semantics/r1_goertek_fixture.json` 完整
- R3 蓝思 `tests/fixtures/decision_semantics/r3_lens_fixture.json` 完整
- R2 目前主要是 `tests/fixtures/news_events/r2_news_fixture.json`，不是端到端 decision_semantics 冻结重放
- 目标日期：工业富联 **2026-07-30**

## 要交付
对照 R1/R3 schema，列出 R2 完整冻结回归样本需要的：
1. 已有文件与字段
2. 缺失文件与字段
3. 每类字段的真实来源（DB 报告、分析 JSON、外部接口、无法取得）
4. 哪些字段必须 cutoff 冻结，哪些是事后评价（不得进入方向/风控）
5. 不要实现 fixture；只出清单与来源。

对照 SHA：`c72dd7b6098297efbec80931dda8bf509c8d8709`（或当时最新主干，须写明）。
