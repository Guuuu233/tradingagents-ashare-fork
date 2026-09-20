## 探针已补 connect 身份台账；fd 复用错配已实测复现并可识别

**取证工具升级，不是实施授权。** 生产代码继续零改动，冻结候选 `8589e65` 未动。

---

### 一、你指出的缺口确实存在，已补

原探针**只在卡住时读 fd**，三条缺陷都成立：

1. **fd 会在 close 后复用**，单靠 fd 可能把两只不同 socket 错配
2. `context.default_socket is sock` **只证明采样瞬间的全局引用**，不能证明它**何时 connect**
3. **卡 A 的 deny 日志没有 socket ID**，无法单独证明 deny 属于卡住的那一只

### 二、补法：插件自带独立 audit hook，从加载起记 connect 台账

探针现在装一个**只观察、绝不 raise** 的 hook（与卡 A 的双 hook 互不干涉），每次 `socket.connect` 落账：

```
mono（单调时间）· wall · PID · tid · thread · nodeid · id(socket) · fd · target
```

卡住时用 **`(对象 ID, fd)` 组合**反查台账，输出三类关联：

| 关联类型 | 含义 |
|---|---|
| **同 ID 同 fd** | 就是这一只 → 给出其 connect 的 mono/nodeid/target，以及**距今多久** |
| 同 ID 不同 fd | 该 socket 重连过 |
| **同 fd 但属于别的对象** | 🔴 **fd 复用警告** → 证明单靠 fd 会错配 |

并自动判 H3：**connect 时的 nodeid ≠ 卡住时的 nodeid ⇒ 该连接是跨用例遗留**。

### 三、实测：构造 fd 复用场景，错配风险真实存在

自测脚本刻意制造你描述的情形——用例 A 建连后 `close()` 释放 fd，用例 B 新建 socket 拿到**同一个 fd** 后建连并阻塞在 `send`：

```
fd_A = 4   fd_B = 4   是否复用同一 fd: True
```

探针输出：

```
卡住帧 socket `sock`  sock_id=0x109c2bf40  fd=4
  ✅ 同 ID 同 fd 的 connect: mono=0.001s tid=8415428480
       nodeid=tests/test_api_smoke.py::test_stuck_here  target=('127.0.0.1', 57540)
  ⇒ 卡住时刻距该次 connect 已过 3.0s
  ⇒ connect 发生在 nodeid=...::test_stuck_here；当前 nodeid=...::test_stuck_here ⇒ 同一用例内建连
  🔴 【fd 复用警告】同 fd=4 但属于其它 socket 对象的 connect: 1 次
       —— 【证明单靠 fd 会错配，必须用 ID+fd 组合】
       mono=0.0003s sock_id=0x109c2be80 nodeid=tests/test_earlier.py::test_first
```

**若只按 fd 关联，就会把 `test_earlier` 的连接错记成卡住原因。** 这一条已被工具挡住。

### 四、诚实边界（必须随证据一并陈述）

1. hook **装于插件加载时**（`pytest_configure`），更早的（解释器/导入期）connect **不被覆盖**。若卡住 socket 落在「台账中找不到同 ID 同 fd 的 connect」，探针会明确打出该提示，**不得当作「没有 connect」**。
2. `sys.addaudithook` **无法移除**，装上即伴随整个进程（PEP 578 行为，非缺陷）。
3. `id()` 在对象被回收后**可能被新对象复用**。因此判定始终是 **ID + fd + 时间窗口三者结合**，不单用任何一项。

### 五、放行条件

**探针已具备「从首次连接前记录这些字段」的能力**（hook 在任何测试开始前装好），因此**可以继续复现取证**，不必再补工具。

复现后请交付：卡住那一只 socket 的 **`sock_id` + `fd` + connect 的 mono/nodeid/target**，与 **deny 日志同一时间窗口同一 target** 的配对结果，外加 `SO_ERROR` / `SO_NWRITE`÷`SO_SNDBUF` / `lsof` TCP 状态，然后落到判定表 v2 的某一行。

---

探针路径 `work/dav1009_probe_pkg/dav1009_stall_probe.py`——**验收工具，不得进入冻结候选或任何后续提交**。父提交固定 `8589e65`；主线 `5a0320f` 继续 HOLD。
