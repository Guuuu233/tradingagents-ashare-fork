## 阻断 4 续：诊断工具本身有两个盲区 —— 先修工具，再下结论

**本条是返修派工。** 基于对返修中工作树（HEAD `28c995d` + 未跟踪的 `tests/_temp_plugin.py`）的只读复核。

### 当前诊断插件测不出你要找的东西

```python
from tests.conftest import is_offline_network_guard_active, _guard_active_depth

def pytest_runtest_teardown(item, nextitem):
    if not is_offline_network_guard_active():
        print(f"GUARD INACTIVE AFTER: {item.nodeid} (depth={_guard_active_depth})")
```

**盲区 ①：`_guard_active_depth` 是整数快照，不跟随变化。**

`from X import name` 绑定的是**导入那一刻的对象**；int 不可变，后续 `tests.conftest._guard_active_depth` 怎么变，这个名字都不动。已实测：

```
from-import 变量 = 1  | 模块属性 = 99   ← 前者不跟随
```

所以打印出来的 `depth=` 永远是插件导入时的值，**无参考价值**。
改法：`import tests.conftest as guard`，动态读 `guard._guard_active_depth`。

**盲区 ②：只在 teardown 检查，且只看 depth。**

- 只在 `pytest_runtest_teardown` 采样，抓不到用例**中途关闭又恢复**的窗口
- 只看 `is_offline_network_guard_active()`（即 depth>0），抓不到 **depth>0 但 socket 方法已被还原为原生**的状态漂移

**depth 与真实 patch 状态是两回事**，只看 depth 必然漏判。

### 护栏正面门禁的最低要求

1. 每个非 `network` 用例在 **setup / call / teardown 三个阶段**都校验**八个 socket callable 的真实身份**（`connect` / `connect_ex` / `sendto` / `create_connection` / `send` / `sendall` / `recv` / `recv_into` 是否 `is` 到护栏版本），而非只看 depth
2. 记录**第一个**发生漂移的 `nodeid` 与阶段，便于定位是谁弄掉了护栏
3. 清代理变量要清**大小写全部变体**：`http_proxy`/`HTTP_PROXY`/`https_proxy`/`HTTPS_PROXY`/`all_proxy`/`ALL_PROXY`（含 `no_proxy`/`NO_PROXY`），只清小写三个不够

### 零外连证据的形式（关键）

**不能只放在 `pytest_sessionfinish`** —— 卡死时该钩子根本不会执行。前两次卡死都是被 `kill -9` 终止的，按当前设计什么证据都不会留下。

**计数器也不能只写在 `_guarded_connect` 内部** —— 完全绕过 wrapper 的连接它看不见，而**阻断 4 正是这种情况**（护栏已漂移为原生，wrapper 从未被调用，计数器全程为 0，却有十几条真实 TLS 连接）。

**可行方案（已实测可用）**：用 `sys.addaudithook` 监听 `'socket.connect'` 审计事件。该事件在 **CPython 层**触发，**独立于任何 monkeypatch**：

```
audit hook 捕获到的 connect 次数 = 1
目标地址 = [('127.0.0.1', 9)]
```

即使用原生 `socket.socket.connect` 直连（模拟护栏已被卸掉），hook 依然捕获到并拿到目标地址。

要求：把每次 connect 的 `(nodeid, 地址, 时间)` **逐行追加写入文件并 flush**，这样即使进程被 `kill`，证据仍留在磁盘上可事后复盘。

### 结论口径（写进交付报告）

在护栏拿到上述正面证据之前：

- DAV-995 的**「护栏有效」**不成立
- **「28min → 7min51s 提速」**归因不成立
- 此前所有 RT-FULL-OFFLINE 证据因与**代理黑洞**混淆而**失去独立性**，需重新建立

### 其余不变

加严 1（同一 loop 五步两断点 + 身份断言）、加严 2（真实链路、禁 `side_effect=` 注入、`bs.login()` 零调用）、阻断 3（硬化覆盖所有 baostock 使用点）、静态守卫三补（`scheduler/`、函数级放行、AST + 动态导入）、白名单 11 文件并在报告中说明。

主线 `5a0320f` 继续 HOLD。

（已同步 steer 至运行中的 run `01a0aa41`，送达成功；此评论为双发留档。）
