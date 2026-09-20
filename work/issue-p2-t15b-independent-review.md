# P2-T15b / Gate4 独立代码审核（只读）

## 角色

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（已填实）

- 分支：`origin/agent/dev2/p2-t15b-gate4-remove-legacy`
- 候选 tip SHA：`503aa1606161918ba25e77dad40ec2e8df652461`
- 父 / 基线：`d2f8aa05579d0520abe942972889a82060ea65e7`
- 关联：DAV-543
- brief：`work/issue-p2-t15b-gate4-remove-legacy.md`
- 用户已批准「开 Gate4」

## 期望（T-H4）

- `tradingagents/` 产品路径 **无** `legacy_proxy` 符号
- `disabled` → `not_applicable` / 不可用，**不**回退 news
- shadow：可持 bundle，但 `direction_allowed=false`，不得用新闻冒充社交
- 单 commit；不开加权；不部署

## 动作

detached checkout tip；diff 对照范围；`.venv310` 跑社交定向测（至少 rollout / analyst separation / e2e + 相关）。

## 禁止

改代码 / FF / 部署 / @调度助手催合入；PASS ≠ 准予合入。

## 交付

✅/⚠️/❌ + 完整 tip SHA + pytest + 路径:行号。
