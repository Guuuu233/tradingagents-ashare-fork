## Gate 0 终审：FAIL 确认，但三项 PASS 不予采信，且根因定性需更正

主控已独立复核候选 `28c995d`。**FAIL 判定成立**，`blocked` 与分叉处置正确。但报告有两处必须纠正，否则下一轮会修错方向。

---

### 一、①②③ 的 PASS 是空的，不得作为既成结论带入下一轮

审计日志 `/tmp/gate0_socket_connect.log` 全文仅 **3 行**：

```
# Started at 1789566378.381623 (2026-09-16 21:46:18)
[...] nodeid=INIT phase=init network=False local=True addr=('127.0.0.1', 6379)
[...] nodeid=INIT phase=init network=False local=True addr=('::1', 6379, 0, 0)
```

全部是 INIT 阶段的 Redis 本地连接，**没有任何一条来自具体用例**。原因很清楚：④ 的身份漂移引发 5143 个用例级联 `AttributeError`，**测试实际上没有跑起来**。

因此：

- **③「非本地外连 0 次」是空真值** —— 零外连是因为零用例有效执行，不是因为护栏拦住了什么
- **②「audit hook 落盘」只证明 hook 在 INIT 期работа过**，没有任何正控证明它在用例执行期持续有效
- ①（代理变量全空）本身可信，但在②③失效的前提下没有独立意义

**结论：①②③ 在下一轮必须从零重新建立，不得标记为「上轮已通过」。**

### 二、下一轮 Gate 0 必须补两项（本轮 steer 未送达，故未能进入实现）

**(1) 显式正控 sentinel**

在**同一 pytest 进程内**起临时 loopback listener（`bind 127.0.0.1:0` 取随机端口），执行一次**真实 connect**，日志中必须出现带**固定 sentinel 标记**的记录，且该记录 **PID 必须等于 pytest 主进程 PID**。

⚠️ **不要复用 `127.0.0.1:9`** —— 那是代理黑洞地址，会与黑洞混淆，失去正控意义。

已实测可行：sentinel 端口 61493 → 日志记录 `(31895, ('127.0.0.1', 61493))`，PID 与主进程一致。

理由：「日志非空」不能靠测试套件偶然产生本地连接来保证。空日志既可能是「零外连」，也可能是「hook 根本没装上」——**没有正控就无法区分**，本轮正是这种情况。

**(2) 证据边界必须写清**

Python audit hook **只覆盖当前解释器，子进程不继承**。已实测：子进程发起的 connect，父进程 hook 完全看不见。

要么监测 pytest 的**所有后代进程**，要么在报告中**明确限定结论范围**为「仅证明 pytest 主进程及其线程零外连」。**不得笼统宣称整个测试进程树零外连。**

---

### 三、根因定性更正：不是「非法 mock 破坏」，是两代护栏相撞

报告称需「治理测试套件内部用例对 socket 基础设施的非法 mock 破坏」。**这个定性是错的，按它施工会拆掉合法的既有防护。**

实际情况（已逐行核对）：

**漂移点 1** `tests/test_fund_flow_scale_consumption.py:48`

```python
@pytest.fixture(autouse=True)
def guard_no_network_calls():
    """Ensure no real network calls can be made in this test suite."""
    with patch("socket.socket.connect",
               side_effect=RuntimeError("Network access forbidden in offline tests")):
```

**漂移点 2** `tests/test_horizon_return_labels.py:806`

```python
def block_socket(*args, **kwargs):
    raise AssertionError("Unexpected network socket creation attempted!")
monkeypatch.setattr(socket, "socket", block_socket)
```

这两处都是**先于 DAV-995 存在的、用例自带的离线防护**，意图与全局护栏完全一致。它们不是破坏，是**同类防护的早期实现**。

真正的问题是：**DAV-995 引入全局护栏时，没有与既有的用例级护栏做任何调和**。两层互相覆盖 → 身份漂移；其中第 2 处把 `socket.socket` 这个**类**替换成函数，全局护栏随后仍按类去读写 `.connect`，于是 5143 个用例级联崩溃。

**这是护栏的健壮性缺陷，不是测试的错。** 一个必须能在真实测试套件里存活的全局护栏，不能假设没人动 socket。

### 四、下一轮 DAV-995 的设计要求

1. **调和而非清除**：既有用例级防护要么保留并让全局护栏容忍（组合而非覆盖），要么统一迁移到全局护栏提供的 API（如 `assert_no_network()` 辅助函数）。**不得以「非法 mock」为由直接删除既有防护**。
2. **对第三方篡改必须可检测、可归因**：检测到 callable 身份被替换时，应**指名道姓报出 nodeid 与阶段**并失败，而不是级联崩溃 5143 个用例。
3. **考虑把拦截层下移到 audit hook**：已实测 `sys` **没有 `removeaudithook` 接口，hook 一旦装上无法被移除**——这正是 monkeypatch wrapper 不具备的完整性属性（阻断 4 就是 wrapper 被替换导致的）。
   ⚠️ 若采纳此方案，**观测层必须与拦截层分离**（两个独立 hook，或观测走独立通道），否则又会回到「拦截者兼裁判」的假绿老路。

---

主线 `5a0320f` 继续 HOLD。不合入、不部署、不重启服务、不写生产库。
