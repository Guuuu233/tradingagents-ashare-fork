## 🔴 阻断 4：离线护栏在全量里没有生效，RT-FULL-OFFLINE 正在真实外连并活锁

**本条是返修派工。** 这是本轮最严重的一条，且**动摇了 DAV-995 的核心归因**。

### 现场（实测）

DAV-1003 的 RT-FULL-OFFLINE（`pytest -q -p no:randomly`）卡死：

```
PID 14059   %CPU=99.4   存活 4:48   16 线程
lsof:
  59743->198.18.0.104:https  (ESTABLISHED)
  59524->198.18.0.104:https  (CLOSE_WAIT)
  59779->198.18.0.104:https  (ESTABLISHED)
  59606->198.18.0.104:https  (CLOSE_WAIT)
  59781->198.18.0.103:https  (ESTABLISHED)
  59612->198.18.0.104:https  (CLOSE_WAIT)
  ... 另有多条 ESTABLISHED
```

已按 PID 精确 `kill -9 14059/14056`，未动其他进程。

### 两个结论

1. **离线护栏在全量会话中没有生效。** 端口是 `443` 不是 `10030`，与 baostock 无关。非 `network` 用例本该在 `connect` 时就抛 `OfflineTestGuardrailError`，实际却**建立并持有了大量外部 TLS 连接**。
2. **存在第二个 99% CPU 忙循环点**，位于 HTTPS 供应商客户端路径上，不是 baostock。

### ⚠️ 方法论要害（必须写进交付报告）

此前所有绿色 RT-FULL 都带了 `http_proxy="http://127.0.0.1:9"` 的**代理黑洞**，而这次的命令里没有。

这意味着：**之前的隔离效果可能主要来自代理黑洞，而不是护栏本身**——护栏的有效性从未被独立验证过。连带地，`28min → 7min51s` 的提速归因也存疑（黑洞让外连瞬间失败，同样会大幅提速）。

这是典型的「两个变量同时改，把效果算到其中一个头上」。必须拆开验证。

### 返修要求

1. **根因定位护栏为何在全量里失效。** 重点查 `tests/conftest.py` 的 `_guard_active_depth` 记账——`enable()` 只在 `depth == 0` 时打补丁、`disable(force=True)` 直接把 depth 清零，而 `offline_guard_disabled()` / `offline_guard_enabled()` 两个上下文管理器、`autouse` fixture、`pytest_sessionstart`、conftest 导入期 `enable()` 这**五处叠加**，存在把护栏永久关掉的路径。**请实证复现，不要只读代码下结论。**
2. **护栏必须提供正面证据**：RT-FULL-OFFLINE 必须在**不设任何代理黑洞**的环境下跑，并在会话结束时断言**零次非本地外连**（例如由护栏统计「拦截数 / 放行数」，放行数必须为 0）。
3. **定位并修掉 HTTPS 侧那个 99% CPU 忙循环点**，或证明它在护栏生效后不可达。
4. 重跑到自然结束；再卡死就按 `work/check-stuck-tests.sh` 取证后中止上报，**不要干等、不要 `--deselect` 绕过**。

### 其余不变

加严 1（同一 loop 五步两断点 + 身份断言）、加严 2（真实链路、禁 `side_effect=` 注入、`bs.login()` 零调用）、阻断 3（硬化覆盖所有 baostock 使用点）、静态守卫三补（`scheduler/` 纳入、函数级放行、AST + 动态导入检测）、白名单更新为 11 文件并在报告中说明。

主线 `5a0320f` 继续 HOLD。

（已同步 steer 至运行中的 run `01a0aa41`，送达成功；此评论为双发留档。）
