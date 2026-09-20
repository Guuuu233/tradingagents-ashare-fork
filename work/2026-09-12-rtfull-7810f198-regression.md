# RT-FULL 复核：当前主干 `7810f198` 相对线上 `70b5b47` 新增 2 项失败（2026-09-12）

执行者：Claude（只读测试，未改代码、未部署、未改看板）。起因：Codex 指出 `59713d3` 之后没有全量回归证据。

## 方法

同一台机器、同一临时 worktree（`/private/tmp/ta-rtfull-20260912`，已删除）、同一解释器 `.venv310`（Python 3.10），命令 `env -u PYTHONPATH python -m pytest -q -p no:randomly`，先后 checkout 两个 SHA 各跑一次全量。

## 结果

| 版本 | 结果 |
|---|---|
| 当前主干 `7810f19875890725cd26e414cde272063fb3606a` | 21 failed, 4231 passed, 1 skipped, 3 deselected（1306s） |
| 线上 `70b5b47bcff0618e0db1258a438b0875440d4ac5` | 19 failed, 4094 passed, 1 skipped, 3 deselected（1201s） |

新增失败 2 项（线上不失败）：

- `tests/test_confirmation_gate.py::test_bull_bear_symmetry_lifecycle`
- `tests/test_confirmation_gate.py::test_focus_or_adopted_claim_contradicted_remains_wait`

被修复项：无。

## 定位（逐版本跑 `tests/test_confirmation_gate.py`）

| SHA | 提交 | 结果 |
|---|---|---|
| `70b5b47` | D-04 合入 | 17 passed |
| `59713d3` | DAV-844 量价填充率 | 17 passed |
| `4dc18b0` | DAV-846 Bull 命题边界 | 17 passed |
| `33d81ae` | DAV-848 Bear 命题边界 | 17 passed |
| `1ef79a7` | **DAV-850 E-03c evidence_verifier / decision_status** | **2 failed, 15 passed** |
| `7810f19` | DAV-852 E-03d research_manager | 2 failed, 15 passed |

引入点为 `1ef79a7`（DAV-850）。

## 失败内容

`test_focus_or_adopted_claim_contradicted_remains_wait`：核心/已采纳命题被反驳时，`confirmation_state` 仍正确为 `UNRESOLVED`，但 `trade_action` 从 `WAIT` 变为 `NO_TRADE`：

```
assert st.trade_action == ACTION_WAIT
E  AssertionError: assert 'NO_TRADE' == 'WAIT'
```

`test_bull_bear_symmetry_lifecycle` 断言摘要：

```
def test_bull_bear_symmetry_lifecycle():
        assert c_state == CONFIRM_CONFIRMED
        assert "all_core_claims_verified:BEAR-1" in r_codes
        assert "audited_rejected_claims:BULL-1" in r_codes
        assert status_bear.confirmation_state == CONFIRM_CONFIRMED
        assert status_bear.trade_action == ACTION_SELL
        assert status_bear.direction == DIRECTION_BEAR
        assert is_non_executable_status(status_bear) is False
        # 5b. Bear winner with contradicted bear focus claim -> WAIT
        assert status_bear_contra.confirmation_state == CONFIRM_UNRESOLVED
>       assert status_bear_contra.trade_action == ACTION_WAIT
E       AssertionError: assert 'NO_TRADE' == 'WAIT'
E         
E         - WAIT
```

## 与 DAV-850 规格的对照

- DAV-850 明确要求：PIT 失败 / 采纳被拒命题 → `ABSTAIN` / `NO_TRADE` / `BLOCKED`；观察假设类命题「不无条件把整单压成 WAIT/NO_TRADE」。
- DAV-850 **未**要求把「核心/已采纳命题被反驳」这条既有路径由 `WAIT` 改为 `NO_TRADE`。
- `1ef79a7` 的变更集为 5 个文件（`decision_status.py`、`evidence_verifier.py` 与三个测试文件），**不含** `tests/test_confirmation_gate.py`，既有契约测试未同步。
- 复审卡 DAV-851 的记录只有定向与扩展关联回归（86 / 402 / 110 passed 等），未见 RT-FULL 全量对照，故未拦住。

## 影响评估

- `WAIT` 与 `NO_TRADE` 同属非执行动作，**不产生错误下单风险**。
- 但两者是不同样本类别（观望 vs 不可执行），会改变统计口径与回测样本筛选；且与既有状态机契约不一致。

## 待总工裁定（二选一，均需按 D-013 走复审与放行）

1. **判为回归**：把「被反驳」路径恢复为 `WAIT`，保留 PIT 失败 → `NO_TRADE` 的新行为；不改既有测试。
2. **判为有意收紧**：更新 `tests/test_confirmation_gate.py` 契约并记录决策（为何反驳类也应阻断），同时说明对统计口径的影响。

## 部署建议

D-013 §3/§4 要求「相对基线零新增失败」，当前不满足，因此不建议直接部署 `7810f198`。可选：先处理上述裁定后部署主干；或按总工判断先部署已零新增失败的 `59713d3`（含 E-01 producer、博弈论、结算管道、量价填充率），把 E-03b/c/d 留到修好后再上。
