修复全量测试套件的进程级线程池污染，使 RT-FULL 能够跑完。根因已由运维定位，见 DAV-979 的「污染源已定位」评论，请先完整阅读该评论再动手。

## 问题

`api/main.py` 的 `lifespan` 在退出时执行：

```python
# api/main.py:441-443
_executor.shutdown(wait=True)                   # ← 关闭的是模块级全局单例
if new_default_executor is not None:
    new_default_executor.shutdown(wait=False)   # ← 不等待回收
```

而 `_executor` 是模块级全局：

```python
# api/main.py:607
_executor = ThreadPoolExecutor(max_workers=int(os.getenv("TA_MAX_WORKERS", "2")))
```

`tests/test_api_smoke.py:471 test_lifespan_can_restart_on_same_event_loop` 在同一 pytest 进程内用 `asyncio.run` 进出 `lifespan` 两次，于是把**整个进程范围内**的 `api.main._executor` 永久关闭。后续用例再依赖它就永久阻塞——实测表现为 `tests/test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation` 在全量套件里挂死（`%CPU=0`、`STAT=S`、不自行结束），而该用例**单独跑 0.18s 通过**。

附带两处泄漏：
- 每次进入 lifespan 新建 `max_workers=64`、`thread_name_prefix="ta-asyncio"` 的 executor 并 `loop.set_default_executor(...)`，退出时 `shutdown(wait=False)` 不等回收；模块级 `_default_executor` 仍指向已关闭/已失效对象（`api/main.py:4567-4569` 的 healthz 探针会读它）。
- `tests/test_api_smoke.py:59 _get_client()` 返回 `TestClient(app, raise_server_exceptions=False)`，既未用 `with` 也未 `.close()`。

## 严格白名单（只许改这两个文件）

1. `api/main.py`
2. `tests/test_api_smoke.py`

如果确认必须改动其他文件才能修好，**先在卡里说明并等确认**，不要自行扩大范围。

## 修复要求

1. **lifespan 只关闭自己创建的资源**。模块级全局 `_executor` 不应因为一次 lifespan 退出而对整个进程永久失效：或改为 lifespan 内创建独立实例，或在退出后将模块级引用复位，使后续使用方能拿到可用的 executor。**不得**简单删掉 shutdown 了事——要说明生产路径下线程如何回收，避免变成资源泄漏。
2. `new_default_executor` 退出时应 `shutdown(wait=True)`，并把 `loop.set_default_executor(None)`、`_default_executor` 复位为 `None`，避免 healthz 探针读到失效对象。
3. `_get_client()` 改为可关闭形式（`with TestClient(app) as client:` 或等价的 pytest fixture），确保 portal 线程回收。
4. `test_lifespan_can_restart_on_same_event_loop` 需保证退出后进程级状态可恢复，不污染后续用例。

**生产行为不得改变**：真实启动时仍须正确配置 asyncio 默认 executor（默认 64 workers，可由 `ASYNCIO_DEFAULT_EXECUTOR_WORKERS` 覆盖），关闭时仍须优雅回收线程。请说明你的改法在真实 uvicorn 启停下的行为。

## 验收标准（必须逐条给出实际输出）

### AC-1 复现命令必须能正常结束

修复前该命令 240s 内必挂，修复后必须跑完并给出精确数字：

```
cd <候选只读检出>
env -u PYTHONPATH -u all_proxy -u ALL_PROXY PYTHONPATH="$PWD" \
  DATABASE_URL="sqlite:////private/tmp/ac1.db" \
  http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 no_proxy=127.0.0.1,localhost \
  /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly \
  $(ls tests/test_*.py | sort | head -85 | tr '\n' ' ') \
  tests/test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation
```

### AC-2 完整 RT-FULL 必须能跑完

```
... 同上环境 ... -m pytest -q -p no:randomly
```

给出 collected / passed / failed / skipped 精确数字。这是本卡的核心价值：**主干此前拿不到一次完整 RT-FULL 基线**。若仍在某处挂死，记录位置、`ps -o %cpu,etime`、`/usr/bin/sample <pid> 5 -mayDie` 栈后回报，不要反复重跑。

注：已知 `tests/test_knowledge_rag.py` 在分文件执行时两侧均挂，若它仍挂请单独标注，不必在本卡内一并解决。

### AC-3 不得引入新增失败

与直接父在**分文件口径**下对照：对 `tests/test_*.py` 逐个独立进程执行（每个 120s 看门狗），两侧相同切分。参考基线（`b95a9b88` 实测）：`214 OK / 8 个失败文件 / 1 个挂死文件`，失败文件为 `test_cninfo_disclosure_metadata`、`test_dav27_report_semantics`、`test_debate_state_persistence`、`test_game_theory_integration`、`test_provider_date_guards`、`test_signal_processing`、`test_social_data_api`、`test_two_stage_analyst_topology`。

### AC-4 新增回归测试

需有测试证明：lifespan 进出之后，模块级 executor 仍可正常提交并完成任务（即本缺陷不会再次出现）。

## 环境（不满足则证据无效）

必须用 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python` 并 `env -u PYTHONPATH`，报告贴 `-V`（须 `Python 3.10.20`）。`DATABASE_URL` 必须指向隔离临时库，**禁止**写 `data/tradingagents.db`。

## 交付

完整 40 位候选 SHA、直接父（须等于交付当时的 `origin/codex/dav-4-p2a-trunk` tip，提交前 `git ls-remote` 复核）、精确远端 ref、白名单清单、`git diff --check`、clean 工作树。

不合入、不部署、不重启服务、不写生产库、不改个人配置。写审分离：本卡由你实现，复审将另派代码审核员。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
