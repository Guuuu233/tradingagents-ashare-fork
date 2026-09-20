## 运维独立复核：候选侧完整 RT-FULL 有 **2 项新增失败**，不得 PASS

候选 `b14f93c6bc84`（父 = `5a0320f0618d`，已 `git rev-parse` 核验），同口径完整全量（`.venv310` / Python 3.10.20 / `-q -p no:randomly` / 隔离 `DATABASE_URL` / **不设看门狗**）：

| | 基线 `28d1adc6` | 候选 `b14f93c6` |
|---|---|---|
| 结果 | `20 failed, 4777 passed, 1 skipped, 3 deselected` | `22 failed, 4817 passed, 1 skipped, 4 deselected` |
| 耗时 | **1700.10s (28:20)** | **519.08s (8:39)** |
| 新增失败 | — | **2 项 ❌** |
| 消失的失败 | — | 0 项 |

**护栏效果确实成立**：耗时降 **69.5%**，这是本卡的核心价值，予以确认。但按 D-012 §4b，有新增失败即不得 PASS。

### 新增的 2 项

```
➕ tests/test_v03_return_measure.py::test_p0_real_provider_verifiable_metadata
➕ tests/test_job_lifecycle.py::test_soft_timeout_emits_overtime_then_allows_completion
```

#### ① `test_p0_real_provider_verifiable_metadata`

```
tests/test_v03_return_measure.py:2012
>   assert is_st_kangmei is True
E   assert None is True
```

该用例 docstring 明写「真实 `VendorPriceDataProvider`」，实际调用 `provider.is_st("600518.SH", "2021-06-01")` 期待真实数据，**但没有 `@pytest.mark.network` 标记**。

**护栏在这里是判对了，不是误伤。**

> ⚠️ 澄清一个我一度怀疑、但查证后**不成立**的点：这里**不存在**「网络失败静默转空值」的红线违规。`v03_return_measure.py:1171` 的 `is_st` 签名即 `Optional[bool]`，文档写明 `None` 表示 unknown/unverifiable 且 **fail-closed**，下游按 `EXCLUDED_UNKNOWN` 排除出指标（同文件另有用例专门验证该路径）。请勿据此判红线违规。

**处置**：给该用例补 `@pytest.mark.network`（补后会被 `pyproject.toml:74` 的 `-m 'not network'` 默认排除），或改为 mock。

#### ② `test_soft_timeout_emits_overtime_then_allows_completion`

```
>   assert [event for event, _ in events] == ["job.overtime"]
E   AssertionError: assert [] == ['job.overtime']
E     Right contains one more item: 'job.overtime'
```

软超时事件未触发。该用例此前很可能是靠**真实网络延迟**（基线中 vendor 调用 60s×3 = 181s）才跑够时长，套件变快后不再触发。

属护栏的**正当副作用**，但必须在本卡修成**不依赖真实延迟的确定性测试**，不能放着不管。

### 返修要求

1. 上述两项必须在本卡内修掉。**不得**以「正当副作用」为由放行，**不得**靠 `--deselect` 绕过。
2. 返修后重跑完整 RT-FULL，失败集合须相对基线 20 项**零新增**。
3. 其余部分运维初步核验未见问题，**但请复审独立验证**，不要采信我的结论：
   - autouse fixture 位于 `tests/conftest.py`，仅 pytest 会话生效，生产路径未受影响
   - `@pytest.mark.network` 经 `offline_guard_disabled()` 豁免
   - 每个用例前后 `_reset_baostock_context()`，正好治模块级 `context` 复用（DAV-979 指出的绕过点）
   - `_check_socket_peer` 只对非本地 peer 抛错，`AF_UNIX` 与未连接 socket 提前返回，未见对本地连接误伤
   - **仍需确认**：`send`/`recv` 每次调用 `getpeername()` 的开销；monkeypatch 在异常路径下能否可靠还原

### 说明

本条原拟通过 steer 直达 DAV-1000 复审 run `01a0a9b6`，但该 run 已结束，**steer 返回非零、消息未送达**，故回落为 issue 评论。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
