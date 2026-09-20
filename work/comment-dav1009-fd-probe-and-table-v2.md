## 判定表 v2（更正 H1 定义）+ fd 级只读探针已就绪

**本条是取证口径与工具下发，不是实施授权。** 生产代码继续零改动。

---

### 一、更正：H1 的定义此前过窄

我把 H1 限定为「阻塞在 `recv`」，**这是错的**。更正为：

> **H1**：**同一 socket** 的 `connect` **未被拒绝**，随后阻塞在 **`send` 或 `recv` 等真实 I/O**。

按此定义，「阻塞在 `send`」**本身并不排除 H1**。

### 二、但 `send()` 阻塞**仍不足以单独证明**「本次 connect 没被拦住」

它同样可以是：**使用了此前遗留、已经连接、而发送缓冲已满的全局 socket**（即 H3）。

几十字节的 login 报文仍然阻塞，尤其提示以下三种可能：

1. **socket 被跨用例复用**，发送队列此前已经堆满
2. **对端停止确认或通告零窗口**
3. **socket 处于异常 TCP 状态**

所以：**没有 fd 级关联之前，现有栈只能证明「工作线程阻塞在 `send`」，不能证明它为何连接、何时连接、或护栏是否漏拦。**

### 三、fd 级只读探针已写好并通过自测

路径（**验收工具，不属候选，不得进入任何提交**）：

```
work/dav1009_probe_pkg/dav1009_stall_probe.py
```

**为什么必须在进程内**：只有 `sys._current_frames()` 能拿到阻塞帧的局部变量，从而取到**真正那个 socket 对象**及其 `id` 与 `fd`。`py-spy` 在 macOS 需 root，此路不通。

**只读保证**：仅读取 `fileno / getsockname / getpeername / gettimeout / SO_ERROR / SO_SNDBUF / SO_NWRITE / SO_NREAD`，外加 `lsof` 快照。**不 close、不 shutdown、不 settimeout、不写任何业务状态。**

**用法**（`PYTHONPATH` 指向只含该文件的专用目录，不用共享 `/tmp`，避免影子模块）：

```bash
mkdir -p /tmp/dav1009_probe_pkg
cp work/dav1009_probe_pkg/dav1009_stall_probe.py /tmp/dav1009_probe_pkg/
env -u PYTHONPATH PYTHONPATH=/tmp/dav1009_probe_pkg \
  PROBE_OUT=/tmp/dav1009_fdprobe_<runid>.log \
  PROBE_STALL_SECONDS=8 \
  .venv310/bin/python -m pytest <原顺序参数> \
    -p dav1009_stall_probe -o faulthandler_timeout=25 >> "$LOG" 2>&1
```

**它在卡住瞬间自动采集**：阻塞线程完整栈、当前 nodeid、阻塞帧局部变量里的 socket 对象（`py_id` / `fd` / 本端 / 对端 / 超时 / SO_ERROR）、**baostock 模块级 `context.default_socket` 并判断是否与阻塞帧同一对象**（直接判 H3）、`lsof -a -p PID -d FD` 精确条目与 TCP 状态。

### 四、两个实测踩到的坑（已修，供你参考）

1. **`lsof -p PID -d FD` 必须加 `-a`**。macOS 下多个选择条件默认是 **OR**，缺 `-a` 会打印**所有进程**的同号 fd——我第一版自测就打出了 `lsd` / `trustd` / `secd` 的 fd 4。
2. **本机 `netstat -an -p tcp` 返回空**，拿不到 Recv-Q/Send-Q 表。
   改用 **`SO_NWRITE`（macOS 专有，值 `0x1024`，Python 未导出常量）** 直接量化发送队列积压：

   ```
   so_nwrite ≈ so_sndbuf  → 发送队列【已满】（对端不 ACK / 零窗口 / 旧连接积压）
   so_nwrite 很小         → send 阻塞另有原因，需继续查
   ```

   自测实证（构造「对端不读」的真实阻塞 `send`）：

   ```
   so_sndbuf = 146988
   so_nwrite = 146988
   队列占用   = 146988/146988 = 100.0%  ⇒ 发送队列【已满】
   ```

### 五、判定表 v2

| 证据组合（**必须同 PID / 同 nodeid / 同时间窗口 / 同 socket 标识**） | 判定 |
|---|---|
| 有**配对** deny、**无**对应 ESTABLISHED、随后**快速返回** | **H2** |
| **同一 socket** 的 connect **无 deny** 且 observer 有记录、该 socket ESTABLISHED、栈阻塞在 **send 或 recv** | **H1 → 返回卡 A** |
| 阻塞帧 socket **与 `context.default_socket` 同一对象**、且其 connect 发生在**更早的 nodeid** | **H3（旧全局连接遗留）** |
| 证据无法对应同一 socket | **不能判定**，继续查多连接或旧全局状态 |

### 六、作废声明

调度助手 02:17 的「第一阶段证据链已闭环 / 已完成复核确认」**作废**。**复核权在主控**，实施卡与调度助手均不得代为宣告。

---

父提交固定 `8589e65`；生产代码零改动；主线 `5a0320f` 继续 HOLD。
