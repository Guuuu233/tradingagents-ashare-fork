## ❌ 候选 `714f620e` 不予放行 —— 两个阻断项，DAV-1004 的 PASS 同时失效

**本条是返修派工，不是存档。请按下述要求改代码并产出新 SHA。**

### 已确认修对的部分（不要回退）

- B3 进程级全局超时污染**已删除**：`cn_baostock_provider.py` 全文已无 `socket.setdefaulttimeout`，改为 per-socket ✅
- B1 捕获收窄已到位：`except (ImportError, AttributeError)` + `logger.warning`，`ensure_baostock_socket_hardening()` 返回 True/False，`is_baostock_hardened()` 状态位已暴露 ✅
- B2/B4/B5/B6 均已落实；`git diff --check` 输出为空；改动严格限于 10 文件白名单；直接父确为 `5a0320f` ✅
- 两轮全量、网络门禁、定向测试均有真实运行记录 ✅

方向是对的。以下两条是**新引入/未闭合**的缺陷，不是旧问题复发。

---

### 🔴 阻断 1：lifespan 启动阶段异常仍然泄漏全局状态

`api/main.py` 的时序（行号为候选内实际位置）：

```
:341  socket.setdefaulttimeout(60)        ← 全局状态已被改写
:368  new_default_executor = ThreadPoolExecutor(...)
:372  loop.set_default_executor(...)      ← 全局 executor 已挂上
:378  identity = await asyncio.to_thread(_get_runtime_identity)
...
:442  try:                                ← try 从这里才开始
:443      yield
:444  finally:                            ← 只能保护 yield 之后的运行期
```

`try/finally` 起点在**所有全局副作用之后**，因此 341→442 之间任何异常都会绕过 `finally`。

实测（让启动在 `_get_runtime_identity()` 处失败）：

```
socket_before=None
socket_after=60.0
default_executor_present=True
default_executor_shutdown=False
```

全局 timeout 未恢复、64 线程 executor 未关闭。**这正是 DAV-989/996 原本要修的那类泄漏，只是把触发条件从「运行期异常」换成了「启动期异常」。**

**改法**：把 `try:` 上移到**第一处全局副作用之前**，`yield` 放进同一个 `try` 内：

```python
prior_socket_timeout = socket.getdefaulttimeout()
new_default_executor = None
try:
    socket.setdefaulttimeout(...)
    ...  # executor / identity / backfill 全部在 try 内
    yield
finally:
    # 恢复 timeout、shutdown executor、复位 _default_executor
```

注意 `loop.set_default_executor()` 挂上去的引用也应一并复位，不要只关闭对象。

---

### 🔴 阻断 2：baostock 硬化失败仍是 fail-open

`ensure_baostock_socket_hardening()` 失败时会记日志并 `return False`（`:110`）——但 `_bs()`（`:164`）**不检查返回值**：

```python
        ensure_baostock_socket_hardening()   # 返回值被丢弃
        return bs                            # 照常返回未硬化的 baostock
```

实测（模拟 `SocketUtil` API 消失）：

```
returned_baostock_module=baostock
hardening_state=False
```

后果：baostock 内部 API 一变，**EOF 活锁照旧复活，只是多了一条日志**。这与「加了状态位」的初衷相反——状态位只用于报告，没用于决策。

现有 `test_baostock_hardening_failure_is_logged_and_detectable` 只验了「失败可被检测」，**没有验「调用方拒绝继续」**，属覆盖盲区。

**改法**：`_bs()` / `_session()` 在硬化失败时 **fail-closed**，拒绝登录并抛出明确异常（建议沿用本文件既有的 `NotImplementedError` 风格，消息写明「baostock 硬化失败，拒绝使用未硬化客户端以避免 EOF 活锁」）。

**此处 fail-closed 是安全的，请一并在交付报告中说明这条耦合**：DAV-998 已把 vendor 类失败归入**可重试**，所以这个拒绝会被分类为可重试 refusal，案例留在回填队列里等下次，**不会造成永久数据丢失**。若反过来 fail-open，得到的是静默活锁 + 线程挂死，代价严重得多。

---

### 必须补的两条测试

1. **启动期异常也恢复状态**：注入启动阶段异常（如 `_get_runtime_identity` 抛错）→ 断言 `socket.getdefaulttimeout()` 与进入前一致，且 executor 已 shutdown、无残留 `ta-asyncio` 线程。
2. **硬化失败时调用方 fail-closed**：令硬化失败 → 断言 `_bs()`/`_session()` **抛异常**且不返回未硬化模块；并补一条断言该失败被 `historical_cases` 归类为**可重试**（与 DAV-998 的耦合不被后续改动破坏）。

---

### 门禁

新 SHA 必须**重跑双层门禁**：`RT-FULL-OFFLINE`（不带任何 `--deselect`/`-k`/路径限定，相对 `5a0320f` 基线 20 项零新增）+ `RT-NETWORK`，外加定向测试与 `git diff --check`。
**`714f620e` 与 DAV-1004 的 PASS 随本条打回同时失效**，新 SHA 须由独立审核员重新复审。

### 现场（已复核）

远端主线仍 `5a0320f`，候选远端 SHA 未变，未合入、未部署、未重启、未写生产库（`94d2f674…`），8000 无监听，无残留 pytest 进程。主线继续 HOLD。
