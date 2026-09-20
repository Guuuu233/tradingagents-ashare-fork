# D-009：补齐 R1 / R3 离线冻结夹具（决策语义）

## 背景

D-009 / `work/2026-08-27-decision-semantics-workflow.md` §回归样本：

- R1 歌尔 `002241` / 2026-05-28
- R2 工业富联 `601138` / 2026-07-30（已有 `tests/fixtures/news_events/r2_news_fixture.json`）
- R3 蓝思 `300433` / 2026-05-06（七失败 INVALID）— 仅有单元级形态，**无 typed 冻结失败报告夹具**

权威句：**R1/R2/R3 离线 fixture 齐备并通过前，不得声称历史案例“已修复”。**

## 基线

- tip：`98fe5d199e8874ae829d2b492882d82339c836f0`
- 分支：`agent/support/d009-r1-r3-fixtures`

## 只做这件事

1. 在 `tests/fixtures/` 增加 R1、R3 **可重复加载**的冻结数据（manifest + 最小必要字段），不要依赖外网。
2. R3：体现七分析师失败 → `analysis_status=INVALID_RUN`（或现行枚举等价）且 **不得** 合格进校准；可用脱敏/裁剪后的历史形态，禁止把真实密钥写入仓库。
3. R1：按审计稿钉子覆盖该日点（至少 PIT/证据或状态机相关可断言路径）；若完整报告过大，允许「切片 + 契约断言」但须在 manifest 写明覆盖边界。
4. 测试调用夹具并断言具体字段（禁 `assert is not None`）。
5. 不改产品行为，除非发现夹具无法挂上现有 API——若需改代码，**先停并评论说明**，勿擅自扩 scope。

## 明确不做

- 部署 / 加权 / 社交 / A0 / industry 迁移
- 宣称「线上案例已修」

## 验收

- 新测试在 tip 上红→绿可复现；push → `in_review`；D-010

## 权威

D-009 §6；decision-semantics-workflow §回归样本；审计 G5。
