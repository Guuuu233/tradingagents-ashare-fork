# Track A12：T+5 due 推断兑现注释契约（trade_date / t_plus_5_date）

## 背景（Cursor 实测 2026-09-02）

主干 tip：`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`（A11 已合入）。

本地库 69 份合格 v2：
- 全部有 `trade_date`，且相对今日均 ≥7 自然日；
- `shadow_credit_metrics` 存在，但 `t_plus_5_status` / `t_plus_5_date` / `t_plus_5_direction_hit` / `is_t_plus_5_due` **全为 null**；
- A11 后 Dimension 4 正确 FAIL：`due_count=0, rate=0, reason=no_due_samples`。

`evaluate_h1b_system_gates` 注释写明（约 L822）：

> If not explicitly marked, treat as due if trade_date exists and > 5 days ago or hit is not None

实际代码在 `is_t_plus_5_due is None` 时只看 `hit` / `t_plus_5_evaluated` / status ∈ {due_and_evaluated, data_missing}，**从不看 trade_date**。结果：已过 T+5 窗口但未回填的样本被当成「未到期」，分母诚实性仍差（应是「已到期未评估」而非「无到期样本」）。

D-006 / 门槛草案 §2.3：分母 = 已满 T+5 的样本数。未回填 ≠ 未到期。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `aa2750fb3d9e1580885c5a24ccc90c0ae66accea`
- 分支建议：`agent/dev2/a12-t5-due-inference`
- origin：`https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- **单关注点 commit。** 不要 FF / 部署。

## 只做这件事

1. 在 `evaluate_h1b_system_gates` 的 Dimension 4 中，当 `is_t_plus_5_due is None` 且 status 不是 `pending_due` / `suspension` 时：
   - 若已有 `t_plus_5_date`（样本顶层或 `shadow_credit_metrics`）且 `<= as_of`（默认今天，可测注入）→ **视为 due**；
   - 否则若有可解析 `trade_date`/`date`：用既有 `calculate_t_plus_5_date`（可注入 calendar）得到 T+5，若 `T+5 <= as_of` → **视为 due**；
   - 计算失败 / 无法解析日期 → 不臆造 due（保持不计或显式不可判定，须在测试钉死）。
2. due 且 `hit is None` → 计入分母但不计入 completed（拉低完整率），与 `data_missing` 语义一致。
3. A11 契约保留：真正 `due_count==0` 时仍 rate=0 / FAIL / `reason=no_due_samples`。
4. **禁止缩短 hold**；禁止编造 price/hit。
5. 定向测试：
   - N 份有 trade_date、无 status/hit、T+5 已过 → `due_count=N`、`completed=0`、FAIL
   - T+5 未到 → 不计入 due（pending）
   - 显式 `is_t_plus_5_due=False` / suspension → 仍排除
   - 显式 hit 齐全 → 回归 PASS

## 明确不做

- 生产库实写回填（另授权；`--dry-run` 可本地自测）
- 开加权 / 部署 / schema / A0
- 改 0.95 阈值
- 脏文件 trio

## 验收

- `tests/test_h1b_gates.py`（必要时 shadow_credit）绿
- push → 完整 40 位 tip → `in_review`；D-010

## 权威

D-006 §T+5；门槛草案 §2.3；A11；代码注释 L822 未兑现契约。
