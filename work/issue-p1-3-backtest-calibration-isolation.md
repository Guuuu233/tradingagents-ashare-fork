# P1-3：回测 / 校准隔离（禁止缩短 hold_days；INVALID/WAIT 不得当 HOLD）

## 目标

D-009 / 审计稿 §P1-3：堵住回测与校准统计污染。

**主缺口（主干仍在）**：`api/services/backtest_service.py`

1. `_get_price_after` 在 `len(df) < hold_days` 时**缩短** `hold_days`（约 179–180 行）——禁止。
2. `_classify_decision` 只认文本 BUY/SELL，其余一律 **HOLD**——会把 `INVALID/ABSTAIN/WAIT/NO_TRADE` 坍缩成合格 HOLD 样本。

**校准侧**：`calibration_service.py` 已有 `is_calibration_eligible` + 部分 `exclusion_stats`；本卡补齐审计要求的计数与回归钉，**不要**重写整条校准管线。缺什么补什么。

本卡不做 P1-4 provider 红灯、不做社交、不部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`
- **新建**隔离分支，例如 `agent/dev2/p1-3-backtest-calibration-isolation`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要 FF 主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §7 P1-3、§5 回测污染
- `work/2026-08-27-decision-semantics-workflow.md` P1-3：eligible/excluded counts；严格 T+N
- 复用 `decision_status.is_calibration_eligible` / `NON_DIRECTIONAL_TRADE_ACTIONS`（含 WAIT）

## 行为契约（锁死）

改**原路径**；禁止 `_v2`。

### A. backtest_service

1. **禁止缩短 hold_days**：价格序列不足目标交易日数 → 该样本 `outcome_status=incomplete`（或等价），`return_pct=None`，**不得**用更短窗口假装完整 T+N。可对齐校准侧 `_get_price_after_strict` 语义（不足返回 None）。
2. **`_classify_decision`（或替换为读结构化字段）**：
   - 优先读记录/结果上的 `trade_action` / `analysis_status` / `decision_status`
   - `INVALID_RUN` / `DATA_ERROR` / `ABSTAIN` / `PARTIAL` → **不得**进方向胜率分母；记 exclusion
   - `WAIT` / `NO_TRADE` → **不得**映射为 HOLD 成交样本；记 exclusion
   - 仅 `VALID` + `BUY`/`SELL`（及明确允许的 HOLD）可进既有 win_rate 路径；HOLD 是否进方向命中按现有 stats 逻辑，但**绝不能**由失败/观望坍缩而来
3. 每条 record 至少保留：`analysis_status`、`trade_action`、`price_basis`（若可得，否则显式 `unknown`）、`entry_price_as_of`、`exit_price_as_of`、`outcome_status`（`ok` / `incomplete` / `excluded` 等）
4. `_compute_stats`：只对合格方向样本计 win_rate；输出或附带 `excluded_*` 计数（至少 invalid/abstain/wait_or_no_trade/incomplete）

禁止：裸 `except Exception: return None` 静默吞错——至少打日志（与 AGENTS 一致）。

### B. calibration_service（最小补齐）

已有过滤则保留。必须可测：

- 仅 `analysis_status=VALID` 且 `trade_action` 非 WAIT/NO_TRADE 且有 probability 进 Brier/reliability（已有则写回归钉）
- `exclusion_stats` 含：`excluded_invalid`、`excluded_abstain`、`excluded_no_trade`（含 WAIT）、`excluded_incomplete_outcome`（或与现有 `skipped_incomplete` 对齐并在 API/返回体暴露）
- 样本门槛未到时不得捏造指标——不足则 `insufficient sample`（若已有则钉测）

不要用当前 qfq  silently 重算旧基准（本卡若不动价格源，至少注释/字段标明 `price_basis`；禁止新开实网依赖）。

## 允许修改

- `api/services/backtest_service.py`
- `api/services/calibration_service.py`（仅缺口）
- 必要时 `api/main.py` 校准/回测响应字段（只加 exclusion 字段，不大改 API）
- `tests/test_backtest_calibration_isolation.py`（新建）和/或扩展 `tests/test_calibration_service.py`、`tests/test_backtest_security.py`
- 禁止实网：价格获取在测中一律 mock / patch

## 禁止

- 社交、新闻、VPA、confirmation、资金流、provider 红灯大修
- 改 3/1、开加权、部署、碰脏文件
- `@pytest.mark.asyncio`

## 测试（TDD）

先在基线写失败测再实现。至少：

1. mock 短价格序列：`hold_days=5` 但只有 2 根 → **不**缩短；outcome incomplete；不进 win_rate
2. `analysis_status=INVALID_RUN` 文本像 BUY → **不**进方向样本；excluded_invalid++
3. `trade_action=WAIT` → **不**变成 HOLD 胜率样本
4. `VALID`+`BUY`+完整窗口 → 仍可计 return（mock 价格）
5. 校准：INVALID/ABSTAIN/WAIT 进 exclusion_stats；eligible 集不含它们
6. （若改 API）响应含 exclusion / incomplete 计数

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_backtest_calibration_isolation.py \
  tests/test_calibration_service.py \
  -q --tb=short
```

## 交付

1. tip 完整 40 位 SHA；父提交必须是 `7e36d6c…`
2. 定向 pytest 精确数字
3. `git diff --stat` 白名单内
4. 未部署
5. 卡置 `in_review`

## 不可违反

D-006/007/008/009/010；AGENTS.md 铁律。
