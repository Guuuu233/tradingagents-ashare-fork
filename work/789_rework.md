**返修卡。** 依据 DAV-789 打回（回退分支被 price_series/get_price_fn 短路，已有有效 price 被清成 data_missing）。前置 = 当前 trunk `04c8aa3`。

## 根因（总控已复现定位）

`shadow_credit.py:2117` 回退结构用 if/elif 互斥：
```python
if price_series is not None ...:      # 部分 ps 不含 T+5 → t5_price_val=None,不回退
elif get_price_fn ...:                # 返回 None → t5_price_val=None,不回退
elif target.get("t_plus_5_price"):    # ← 回退，被前两分支短路
```
回退到已有 price 仅在前两分支都不走时才触发。调用方传部分 price_series 或 get_price_fn 返回 None 时，进不了回退，已有 `t_plus_5_price` 被清。

## 唯一关注点

改为「无论取价路径如何，三来源都尝试后若仍无有效 T+5 价格，则统一兜底回退到 report 已有的有效 `t_plus_5_price`」——不用 if/elif 互斥，用「先试外部取价，失败再兜底已有值」的顺序结构。

## 允许改

- `tradingagents/agents/utils/shadow_credit.py`（`backfill_tplus5_shadow_for_report`）
- `tests/test_backfill_idempotency.py`（补场景，不改既有断言除非确需）

## red_team_scenarios（D-012 §5b：本清单已由总控+Codex 双人确认覆盖面，8 场景）

用 `_build_test_report` helper 构造合格 v2 报告（否则样本被判不合格、不进正确分支——这是总控上轮探针踩过的坑）。

| # | 场景 | 预期 |
|---|---|---|
| RT-1a | 有效已有 price + entry=None + **不传** price_series | 保留 price，不降级（候选已过） |
| RT-1b | 同上 + entry 为非数字文本（"逢反抽122.80减仓"） | 保留 price（候选已过） |
| **RT-1c** | 有效已有 price + 传入**部分** price_series（不含 T+5 日） | **保留已有 price，不降级 data_missing**（本次新增，修前失败） |
| **RT-1d** | 有效已有 price + `get_price_fn` 返回 None | **保留已有 price，不降级**（本次新增，修前失败） |
| RT-2 | price 有 entry 有（正常） | 行为不变 |
| RT-3 | 无 price 无 entry | 仍 data_missing（真缺失） |
| RT-4 | 幂等：重复 backfill | price/status 不变 |
| RT-5 | 停牌样本 | 779 停牌判定不受影响 |
| RT-FULL | 候选 SHA 真全量 `pytest -q -p no:randomly` | 对照 trunk 04c8aa3 基线 17 项，任何新增即打回 |

RT-1c/RT-1d 复现命令：
```python
import sys; sys.path.insert(0,"tests")
from test_backfill_idempotency import _build_test_report
from tradingagents.agents.utils.shadow_credit import backfill_tplus5_shadow_for_report, T_PLUS_5_STATUS_DUE_AND_EVALUATED as DONE
rep=_build_test_report(symbol="601012.SH",trade_date="2026-08-03",winner="tie",entry_price=None,existing_t5_price=15.6,existing_t5_status=DONE,existing_t5_hit=False)
assert backfill_tplus5_shadow_for_report(dict(rep),as_of="2026-08-15",price_series={"2026-08-03":15.0})["t_plus_5_price"]==15.6  # RT-1c
assert backfill_tplus5_shadow_for_report(dict(rep),as_of="2026-08-15",get_price_fn=lambda *a:None)["t_plus_5_price"]==15.6      # RT-1d
```

## 硬约束

- 只在副本库验证 backfill；禁止写生产库。
- 交付前 `git push` + `git ls-remote origin <分支>` 回读贴输出。
- 禁止 FF、部署、重启、写生产库、开加权。

## 交付

完整 40 位 SHA、第一父（须 04c8aa3）、diff --stat、diff --check、8 场景实测输出（含 RT-1c/1d 与 RT-FULL 真全量计数）、工作树状态。状态置 in_review。
