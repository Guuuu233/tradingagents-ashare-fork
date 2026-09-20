Cursor 独立复审 DAV-499 / P2-T8：**不准予合入**。

候选 SHA：`0214a4f012af5141612f017b9343e1e0ff348f67`  
父提交：`ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`（线性）

隔离 worktree 复跑 brief 套件：**111 passed, 1 failed**。

失败：`test_collect_with_social_timeout_does_not_crash_and_produces_timeout_context`  
期望 `status=timeout`，实际 `failed` + `social_archive_missing`。

根因：`future.result(timeout=...)` 抛 `concurrent.futures.TimeoutError`，在 CPython 3.10.20 / 本仓库 `.venv310` 上 **不是** builtin `TimeoutError`；现有 `except TimeoutError` 捕不到，落入 `except Exception`。

返修说明见仓库 `work/issue-p2-t8-timeout-repair.md`。请在同一隔离分支上修超时映射（可顺手加强 ThreadPool spy），推新 SHA 后回 `in_review`。

白名单未越界；顺序/独立键/market_attention 方向看起来对，但超时契约未过，故本轮拒收。

**不准予合入。不准予部署。**

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
