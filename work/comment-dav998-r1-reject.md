## ❌ 候选 `0f5b4a66` 不予放行：暂态供应商失败仍被永久冻结（同类错误复发）

### 已修对的部分

`RETRYABLE_REFUSAL_CODES` 引入 `future_eval_date` / `eval_date_not_closed` / `calendar_unavailable` / `t1_date_unavailable`，方向正确，原始复现场景确已解除。定向测试 54 passed、直接父正确、白名单正确。

### 未修的部分（阻断）

运维在候选工作树上实测（`.venv310`，Python 3.10.20）：

| 场景 | 判定 code | terminal |
|---|---|---|
| 供应商超时 60s | `vendor_refuse` | **True ❌** |
| `ConnectionRefusedError` | `vendor_refuse` | **True ❌** |
| `provider unavailable` | `vendor_refuse` | **True ❌** |
| baostock 连接被对端关闭 | `vendor_refuse` | **True ❌** |
| 对照：快照源限制 | `snapshot_refusal` | True ✅ 正确 |
| 对照：日线冲突 | `duplicate_bar_conflict` | True ✅ 正确 |

根因在 `_classify_refusal_code`（`historical_cases.py:137-158`）：它只正面识别 `duplicate_bar_conflict` 与 `snapshot_refusal` 两种模式，**其余一律 `return default_code`，即 `vendor_refuse`**；而候选把 `vendor_refuse` 放进了 `TERMINAL_REFUSAL_CODES`。

于是 `vendor_refuse` 成为「所有无法归类失败」的兜底桶，网络超时、连接被拒、供应商临时不可用全部落入，被判永久终态、永久排除出回填队列。

**这与本卡要修的原始缺陷是同一类错误，只是换了个入口。** 且这类失败恰恰是最常见的——尤其在 baostock EOF 忙循环（DAV-979）尚未修复的当下。

### 设计方向必须调整：兜底方向反了

**默认应为可重试；终态必须「正面识别」才成立。**

代价不对称：

- 误判**可重试** → 多跑一次回填，廉价、可自愈
- 误判**终态** → 案例收益永久停在 `【数据缺失】`，T+1 学习闭环永久丢失，**不可逆且静默**

不确定时必须倒向可重试。

### 返修要求

1. `vendor_refuse` 移出 `TERMINAL_REFUSAL_CODES`（或改为默认可重试）。
2. 终态集合只保留能正面证明「重试也永远不会成功」的 code：无效日期、非交易日、非 T+1 评估日、快照源限制、日线冲突等**确定性**缺陷。
3. 网络超时、连接被拒、供应商不可用、连接被对端关闭 → **一律可重试**。
4. 未知/未分类 code → **按可重试处理**，不得默认终态。请在代码注释中写明该兜底原则及理由。

### 必须补的测试（现有覆盖盲区）

现有测试只验「拒绝时的分类正确」，**没有一条验「失败之后恢复成功」**——这正是本卡两轮都漏掉同类缺陷的原因。请补端到端回归：

```
第一次调用 vendor 失败（超时 / 连接拒绝）
  → 案例进入回填队列，total_scanned > 0
第二次调用 vendor 成功
  → 收益正确回填，actual_outcome 不再是【数据缺失】
```

这条链路能跑通才算修好。

### 关于全量回归

本轮回归额外排除了一个用例、运行 60 分钟停在 96% 无最终汇总。**该停滞不是实现问题**：系 baostock EOF 忙循环所致（`socketutil.py:55`，`recv()` 在对端关闭后恒返回 `b""`，无 EOF 检查无超时 → 100% CPU 无限空转），详见 DAV-979 最新评论。

但结论不变：**额外 deselect 用例、或事后分拆补跑，都不能替代完整 RT-FULL**（D-012 §4b，`DECISIONS.md:71`），不据此放行。重新交付时请先确认无其他卡并发抢占资源，再跑一次不额外排除任何用例的完整回归；若再次高 CPU 不结束，记录 PID / `%CPU` / `lsof` 的 `CLOSE_WAIT` 连接后中止并如实上报，**不要干等，也不要靠 deselect 绕过**。

### 状态

主线维持 `5a0320f0618d`，**继续 HOLD**：不合入、不部署。返修后需同 SHA 独立复审（写审分离）。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3) [@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
