# P2-T8 返修：社交超时必须映射为 timeout（不得 failed）

## 结论

Cursor **不准予合入** `0214a4f012af5141612f017b9343e1e0ff348f67`。

隔离 worktree（精确 SHA）复跑 brief 命令集：**111 passed, 1 failed**。

失败用例：

`tests/test_data_collector_social_integration.py::test_collect_with_social_timeout_does_not_crash_and_produces_timeout_context`

期望 `social_data_context.status == timeout`，实际得到 `failed` + `social_archive_missing`。

## 根因（已复现）

`DataCollector._fetch_social_context` 写的是：

```python
except TimeoutError:
    # → SocialStatus.TIMEOUT
except Exception:
    # → SocialStatus.FAILED
```

在本仓库 `.venv310`（CPython 3.10.20）上：

- `future.result(timeout=...)` 抛出的是 `concurrent.futures._base.TimeoutError`
- 它 **不是** builtin `TimeoutError` 的别名（`TimeoutError is concurrent.futures.TimeoutError` → `False`）
- 因此超时落入 `except Exception`，被错误标成 `failed`

这违反 Task 8 契约：社交超时须独立隔离并产出 **timeout** typed context（reason 可用 `social_archive_locked`）。

## 基线

- 不要从主干另起无关功能
- 在现有隔离分支 `agent/dev2/p2-t8-data-collector-social` 上继续，或从 `0214a4f` 开返修 commit（仍单关注点）
- 父链须仍能线性 FF 到当前主干 tip：`ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 文件白名单（同 T8）

1. `tradingagents/graph/data_collector.py`
2. `tests/test_data_collector_social_integration.py`
3. 必要时 `tests/test_data_collector.py`（仅若回归需要）

禁止扩到 Graph / API / analyst / 删 legacy。

## 必改

1. 捕获 `concurrent.futures.TimeoutError`（可与 builtin 一并捕获，或 `except (TimeoutError, FuturesTimeoutError)`，以 **实际** `future.result` 类型为准）。
2. 超时路径：`status=timeout`、`REASON_SOCIAL_ARCHIVE_LOCKED`（或契约等价）、ledger status=`timeout`；不得写成 `failed`/`social_archive_missing`。
3. 先确认修复前该用例红、修复后绿；全套 brief 命令仍绿。

## 建议顺手（不阻塞若时间紧，但本卡内修更好）

`test_fetch_all_threadpool_does_not_submit_social_tasks` 当前 patch 掉了 `_fetch_all`，spy 几乎空转。可改为：不 patch `_fetch_all`、只 patch 市场 tools 为快返回，或断言 `_fetch_social_context` 不在 `_fetch_all` 源码/call graph 内（已有静态断言可保留，行为 spy 应真正覆盖 `_fetch_all` 内部 submit）。

## 验证命令

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_data_collector_social_integration.py \
  tests/test_data_collector.py \
  tests/test_social_data_collector.py \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py
```

## 交付

- 新完整 40 位 SHA + `git diff --stat` + pytest 精确数字
- `in_review`；不要 @调度助手；不要自行 FF
- Cursor 复审通过后才会「准予合入」

**不准予部署。**
