# DAV-199 正确账户三股 3/3 回归（0b10041 已上线）

Hermes 代表用户解锁本卡。旧 2/1 报告与 count=0 报告一律作废。

## 已核验基线（不要再考古）

- 主干/宿主：`0b10041f9e68b5d0116b76c36cd629acb365f10d`，PID 约 86680，healthz 已对齐
- 正确账户：`davidliu022305@gmail.com` / `429163f7-50b6-4982-8bdf-96ae99506843`
- 持久配置必须保持 3/1，禁止写回
- 招商银行合格样本已独立核库：`2b81b2d905ed411e951563bc91683fef`
  - `investment_debate_state.count=6`，bull/bear history 均非空，claims=9，judge 存在，无 DEBATE_STATE 残留
  - `risk_debate_state.count=9`，三方 history 存在，judge 存在，无 RISK_STATE 残留
  - 资金流 `tushare_eastmoney_moneyflow_dc` / `r0_net=0.912207` / `hard_guard.blocked=false`
  - 宏观/基本面/新闻含「传导」「产业链」
  - 本卡可将该 ID 记为招商银行已通过，**不必重跑**，除非你核库发现与上列不符

## 必须新跑（串行，禁止并发撞配额）

1. 京东方A `000725.SZ`
2. 宁德时代 `300750.SZ`

每次：指定账户登录态；单 horizon；`config_overrides={"max_debate_rounds":3,"max_risk_discuss_rounds":3}`；事后核库仍为 3/1。

每只必须新 report_id，且：

- user_id 正确
- completed：`count=6` 且 `count=9`，history/claims/judge/feedback 完整，无机读标签残留
- 失败：status=failed + 精确 error，不得伪 completed
- 资金流记录 selected_source/field/value/window/as_of；合法 r0_net 不得误阻断
- 对照《项目加强方案》：宏观/基本面/新闻至少出现量化传导或产业链段落，缺数据必须写【数据缺失】

禁止改代码、`.env`、providers、模型绑定。禁止用 `local-default-user`。不得 @项目调度助手。

交付：两只新 report_id + 招商银行沿用 `2b81` 的核库摘要；明确 DAV-200 仍锁定直到本卡 PASS。
