# 2026-09-14 worktree 与 Git 工件只读盘点

## 结论

本次只读盘点没有删除、prune、reset、回收或改写任何 worktree / Git 对象。

- 当前仓库登记 **198 个 worktree**；其中 **135 个**的 Git 元数据标记为 `prunable`，另有
  **63 个**仍被登记为非 prunable。
- 63 个非 prunable worktree 中，当前实际存在的目录数为 63；包含用户根 checkout、当前
  发布副本、旧发布副本、审查/回归隔离副本和少量历史施工副本。
- 根 checkout `/Users/davidliu/Documents/TradingAgents-AShare` 仍有用户既有修改；本次未触碰。
- `git count-objects -v` 报告 1 个临时 garbage object；`git fsck --full --no-reflogs --unreachable`
  发现 90 个 unreachable commit、206 个 unreachable tree、116 个 unreachable blob。它们可能仍
  对应历史审查、返修或未保留分支，不能仅凭“unreachable”判定可删。

## 当前发布相关工件

以下路径和提交仍有证据价值，清理前必须保留或先完成证据归档：

| 用途 | 路径 | 当前 HEAD |
|---|---|---|
| 用户根 checkout（有既有修改） | `/Users/davidliu/Documents/TradingAgents-AShare` | `4fa76815d5aa7d1cfab9942c8f9a9606034c279d` |
| 当前 P1-F 发布副本 | `/private/tmp/ta-release-p1f-6cc4e-20260914` | `81b211436e7595d9a06b8cbab8e346b8c443cc0d` |
| P1-F 旧发布副本 | `/private/tmp/ta-release-p1e-0263496-20260914` | `026349614a3f1b92a95dc06c0515f10ebec193bc` |
| P1-D 旧发布副本 | `/private/tmp/ta-release-9d702e7-20260914` | `9d702e7522c94bf3ac983cb1ede10943cfca1a4b` |
| P1-E2b 旧发布副本 | `/private/tmp/ta-release-63d5648-20260914` | `63d5648bca7c49f57e1211d848cbc1d02ff6b3a5` |
| P1-F 合入证据树 | `/private/tmp/ta-p1f-merge-6612aea-20260914` | `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1` |

## 清理边界

1. `prunable` 只表示 worktree 元数据已失去对应管理目录，**不等于其中的提交、测试输出或
   审查证据已经无价值**。
2. 在逐个确认“卡片/审查/回归/发布证据已落入仓库或另有可恢复副本”之前，不执行
   `git worktree remove`、`git worktree prune`、临时目录删除、reflog/对象回收或任何广泛清理。
3. 下一步若要清理，应先按路径建立保留/可回收清单，再逐项核对 issue、证据文档、分支引用和
   运行回退点；清理动作另开独立授权门。

## 复现命令

```text
git worktree list --porcelain
git count-objects -v
git fsck --full --no-reflogs --unreachable
```

