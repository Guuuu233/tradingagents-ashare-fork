# v2 P1-S：H1a 影子信用采集（总卡）

## 精确基线

- 目标主干：`target/codex/dav-4-p2a-trunk` @ `0554216305b3c860cbe893681335b6b1a29e17ef`
- P1-B 已部署。规格第 9 节。
- 与 P1-FE 并行，**禁止**改对抗核心文件与前端抽屉。

## 目标

新报告完成后写入影子指标，但 `credit_weighting_enabled` 恒为 false，信用分不进总监 prompt，不改变裁决。

首期为降低迁移风险：写入已有 `result_data`/`investment_debate_state.shadow_credit_metrics` JSON，带 `schema_version`，保留后续迁表接口。本卡不建 `agent_credit_events` 表。

## 红线

禁止改 3/1、模型/绑定/Key；禁止 FF 主干；禁止重启；禁止把 T+5 未到期记为失败；缺行情 typed gap，禁止填 0。

完成后按统一交付格式评论，mention 独立代码审核员，不要 mention 项目调度助手。
