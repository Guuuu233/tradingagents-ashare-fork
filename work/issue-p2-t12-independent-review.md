# P2-T12 独立代码审核（只读）

## 角色

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 时机

等 DAV-507 开发交付完整 40 位候选 SHA 并 `in_review` 后，由 Cursor 把本卡候选 SHA 填实并唤醒。**在 SHA 未填实前不要开工。**

## 候选（开工前由 Cursor 填实）

- 候选分支：`agent/dev2/p2-t12-social-report-gates`（或开发实际分支名）
- 候选 SHA：`TBD_FULL_40_CHAR_SHA`
- 基线 / 父提交必须为：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`

## 动作（只读）

1. detached checkout 候选 SHA
2. `git diff 68ae241bdf9c148654f551fb67b7e5f2ec56dba4..<SHA> --stat` 对照白名单（见 DAV-507 / `work/issue-p2-t12-social-report-gates.md`）
3. 按正确性 / 契约 / 测试 / 越权维度审查；HIGH 必须打回
4. `.venv310` 跑 issue 建议 pytest，报告精确数字

## 禁止

- 改代码、FF、部署、@调度助手催合入
- 把 PASS 写成「准予合入」
- 审错 SHA / 只看开发自评

## 交付

一条汇总评论：总体评级 ✅/⚠️/❌ + 完整 SHA + pytest 数字 + 路径:行号证据。然后等待 Cursor 签字。
