# P2-LOW 独立代码审核（只读）

## 角色

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（已填实）

- 分支：`origin/agent/dev2/p2-low-social-cleanup`
- 候选 tip SHA：`9d5d53dd89bed6dcbfa7dd3aa991771993dddfea`
- 父链基线：`0cda99b6072116874b7a458432d0c7bc7b0a29e3`
- 关联开发卡：DAV-535
- brief：`work/issue-p2-low-social-cleanup.md`

## 期望 4 commits（各一关注点）

1. L1 `41c9083…` — 禁止硬编码 cutoff（adapter / prompt_formatter）
2. L2 `dcd1b54…` — 收窄 `parse_iso_datetime` except
3. L3 `a6a5195…` — `published_at` 按 datetime 排序
4. L4 `9d5d53d…` — verifier 分离 mode / status

## 动作（只读）

1. detached checkout 上述 tip
2. `git diff 0cda99b…9d5d53d --stat` 对照白名单文件
3. 核对 L1–L4 契约；确认无 Gate4 / 无删 `legacy_proxy`
4. `.venv310` 跑相关 pytest，报告精确数字

## 禁止

改代码、FF、部署、@调度助手催合入；PASS ≠ 准予合入。

## 交付

汇总评论：✅/⚠️/❌ + 完整 tip SHA + pytest + 路径:行号。等 Cursor 签字。
