# DAV-808 红队场景覆盖面复核（第二轮）

这是 D-012 §5b 的只读覆盖面复核，不是代码审查。不得改代码、改 DAV-856 实施卡、派开发、合入、部署或触发调度助手。

## 复核对象

- 实施卡：DAV-856，当前面向 `work/dav808-implementation-card-20260913.md` 的 RT-1 至 RT-16；RT-13～RT-16 是首轮 DAV-857 `NEEDS_ADD` 后新增的强制场景。
- 目标基线：`54077b6ad4a287bbd8c43d386e91427662a5e786`。
- 复核者：项目评估师（`2c03cc8f-6628-4464-954a-84c47079fdf3`）。

## 必查点

逐条确认 RT-1 至 RT-16 是否覆盖：

- global、`group=arbiter`、`role=research_manager` 及最终 resolved 拼接；
- bull、bear、trader、risk_manager 四个非 manager 角色命中时的整单停机；
- before/after placement 与注入开关关闭；
- 既有旧行、迁移入口、直接 factory 绕过 API 的三层分别验证；
- `VIOLATION`、`SAFE_NEGATION_OR_UNRELATED`、`AMBIGUOUS` 三态；
- `cluster_id` / `independent_cluster_count` 单独出现时的零容忍；
- `NO_TRADE`、reason code、prompt hash、五角色不注入、零 LLM 调用、报告可回读的联动结果；
- 合法 prompt 的现有行为不回归。

## 交付

- 逐条给出 RT-1 至 RT-16：`COVERED` / `NEEDS_ADD` / `BLOCKED`；
- 如仍有遗漏，给最小新增场景、固定输入、预期输出和对应入口；
- 给出是否解锁 DAV-856 后续代码审查的结论；
- 不要在评论末尾提及或链接项目调度助手，避免触发重复调度 run。
