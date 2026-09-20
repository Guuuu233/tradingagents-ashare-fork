# P3 入口：H1b 激活门槛草案（只规划，不写加权代码）

## 背景

P2 已收口（tip `11309037de9334820603eec6dd801f291172f6ed`，DAV-421 PASS，gaps 分类已部署）。规格 §11.1：H1b 加权前必须书面定义并经用户批准门槛；未批准前保持 H1a shadow-only。

## 本卡范围

只产出门槛草案文档（可写在 issue 评论或 `work/p3-h1b-activation-gates-draft.md`），**禁止改代码 / FF / 部署 / 改 3/1 / 模型绑定**。

## 必须覆盖的字段（给用户签字）

1. 最小样本量 N（建议同时给出总体与 bull/bear 分侧下限）
2. 时间跨度（日历天 / 交易日）
3. T+5 完整率门槛
4. bull/bear 样本平衡规则（比例或绝对差）
5. 模型×方向偏置冻结规则（何时禁止加权）
6. 与 H1a shadow 指标的对接：读哪些表/字段；加权默认 **feature flag off**
7. 回滚：关 flag 后裁决回 shadow-only，历史影子数据保留

## 交付

- 一页草案 + 推荐默认值 + 风险/反对意见
- 评论里 @项目评估师 做只读评审
- **不要** @项目调度助手；等用户批准数字后再开实施卡

## 参考

- 规格：`/Users/davidliu/Downloads/TradingAgents-AShare-v2-完整详细施工实施规格-2026-08-24.md` §11
- 交接：`work/2026-08-26-p2-complete-p3-entry.md`
- H1a 已落地：DAV-415 / DAV-417
