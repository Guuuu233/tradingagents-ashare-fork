**返修卡 V-03a-2（作用域修正，只读）。** 依据 DAV-802 打回：测量把全库 1408 条（多账号 + 失败）全算进去，污染了收益基线。前置 = 当前 trunk（DAV-802 候选 `257199eb` 之上，或其父，取最新）。

## 缺陷（总控实测确证）

DAV-802 报告"总报告数 1408"——**未按账号、未按状态过滤**。实测：1408 跨多账号（David 317 / 其他 281 / local-default 155 / …），全库 **616 条 `failed`**。角色模型绑定是**按账号**的，混账号 = 混配置 = 污染基线。

## 唯一关注点

**测量必须限定单一账号 + 仅成功报告**，参数化：
- `target_user_id`（默认 David = `429163f7-50b6-4982-8bdf-96ae99506843`）
- `status = 'completed'`（排除 616 失败/未完成）

## 干净总体（总控已核，用于校验重跑）

David 账号：总 317 / completed 231 / failed 86；**有方向的可评估候选 217**，日期 **2026-04-30 ~ 2026-09-08**（全部落 HISTORICAL_OOS；DEV=0、FORWARD=0）。
重跑后必须满足：该账号 completed 计数 = **231**，评估候选 ≈ **217**，DEV 段 = **0**、FORWARD 段 = **0**。

## 允许改

- `tradingagents/eval/v03_return_measure.py`（加账号 + 状态过滤，参数化）
- `tests/test_v03_return_measure.py`（补作用域测试）
- `scripts/run_v03_return_measure.py`、`work/*report*`（重跑产出）

## red_team_scenarios（D-012 §5b，第二双眼睛复核覆盖面）

| # | 场景 | 预期 |
|---|---|---|
| RT-S1 | 库含多账号 | 只计 `target_user_id`，别账号不进任何指标 |
| RT-S2 | 含 failed 报告 | `status≠completed` 全部排除，不进分母 |
| RT-S3 | 元数据盖章 | 报告显式标 target_user_id + "仅 completed"，并记该账号 total/completed/failed |
| RT-S4 | 计数校验 | 该账号 completed=231、评估候选≈217、DEV=FORWARD=0 |
| RT-1..10 | 原 10 项 | 仍全过（入场/成本/typed-missing/collision/OOS/基准/盖章/coverage 分离等） |
| RT-FULL | 真全量 `pytest -q -p no:randomly` | 对 trunk 基线零新增 |

## 硬约束

只读生产库、只在 `sqlite3 .backup()` 副本验证、禁止写生产库/FF/部署/重启。结果仍须显式标"半成品基线，非定性判断"。交付置 in_review。

## 交付

完整 40 位 SHA、第一父、diff --stat/--check、作用域校验输出（=231 / ≈217 / DEV=FWD=0）、14 个 RT + RT-FULL 实测、样例报告（账号 + 状态盖章）、副本库路径、工作树状态。
