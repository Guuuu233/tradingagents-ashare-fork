## 🔴 根因再次更正（第三次，也是最准确的一次）：baostock EOF 忙循环

我此前的结论「**根本不是死锁，只是被真实外网拖慢**」**只对了一半，且关键处不准确**，在此更正。感谢 Codex 的只读审计定位。

### 准确的机制

问题**不是**没网。实测网络路径当前可用：

- 普通 HTTPS 正常
- 在同样的 `http_proxy=127.0.0.1:9` 环境下，`baostock.login()` **0.077 秒成功**
- 查询历史行情 **0.681 秒成功**
- Clash 对 `public-api.baostock.com` 走 DIRECT，路径是通的

真正的缺陷在 baostock 客户端自身。`site-packages/baostock/util/socketutil.py:55` `send_msg`：

```python
receive = b""
while True:
    recv = default_socket.recv(8192)          # 对端关闭后永远返回 b""
    receive += recv                            # receive 永不变化
    if receive[-13:] == b"<![CDATA[]]>\n":     # 结束标记永不匹配
        break                                  # ← 永远到不了
```

**没有 EOF 检查，没有超时。** 对端一旦关闭连接，`recv()` 恒返回空字节，`receive` 永不增长，终止条件永不满足 → 进入 **100% CPU 的无限忙循环（livelock）**。

用人话说：不是路断了，是**对方挂了电话，旧客户端不会挂断，自己在原地空转**。

### 完整触发链

```
POST /v1/reports 保存 completed 报告
  → report_service 触发历史案例计算（该副作用早已存在）
  → historical_cases 调 baostock 取 T+1 行情
  → 对端关闭连接
  → socketutil.send_msg 进入无限忙循环
  → 线程池超时只能停止「等待」，无法杀掉已在运行的供应商线程
  → 表现为测试永久卡死
```

**上层 60s 超时对这种忙循环完全无效**——`future.result(timeout=60)` 只是放弃等待，工作线程仍在满速空转。

### 现场实证（非推测）

| 证据 | 数据 |
|---|---|
| DAV-996 的 pytest 进程 PID 6619 | **132.3% CPU，持续 1h19m** |
| 其持有的连接 | `198.18.0.1:65438->198.18.0.112:10030 (CLOSE_WAIT)` ×2 |
| DAV-992 遗留进程 PID 12371 | 99.5% CPU，持续 **4h28m**（该文件正常单跑 `56 passed in 29.53s`） |
| `sample` 采样 | 热点全在 `_PyEval_EvalFrameDefault` / `_PyFunction_Vectorcall`，**无任何系统调用等待** |
| 假 socket 离线复现 | **1 秒内 `recv()` 空转 4,332,446 次**，`receive` 长度恒为 0 |

`CLOSE_WAIT` 是决定性证据：它表示**对端已发 FIN、本端未关闭**，与源码中缺失的 EOF 处理完全对应。

### 为什么以前没暴露

- `report_service` 的历史案例副作用早就存在；
- 但 `tests/test_fund_flow_scale_consumption.py` 是 **9 月 11 日才新增**的；
- 该新测试把 **API 报告测试**与**直接 `create_report()`** 串在同一进程；
- `test_api_smoke.py` 里的报告创建**没有全局离线护栏**；
- `fund_flow` 测试自带的护栏**只拦 `connect`**，拦不住**已经建立的 baostock 全局 socket**（baostock 把 socket 存在**模块级 `context`** 上复用）；
- 以前单跑、或网络正常返回完整数据时，该缺陷不显现。

### 我此前三次归因的最终账

| # | 结论 | 判定 |
|---|---|---|
| 1 | 挂死用例是 rt7 | ❌ 错（由进度字符数反推序号，方法不可靠） |
| 2 | 根因是 `api/main.py:441` 关闭模块级全局 `_executor` | ❌ 未证实（DAV-989 按此方向修，复现命令仍卡同一位置） |
| 3 | 不是死锁，只是被真实外网拖慢 | ⚠️ **半对**。60s×3=181.3s 的耗时构成成立；但「不是死锁」不准确——存在真实的无限忙循环 |
| 4 | **baostock EOF 忙循环 + 线程不可回收** | ✅ 当前结论，有 CLOSE_WAIT + CPU + 源码 + 复现四重证据 |

需要区分两种**形态完全不同**的现象，此前我把它们混为一谈：

- **阻塞式慢**：`%CPU=0`、`STAT=S`，60s 超时 ×3 = 181.3s —— 耗时构成的主体
- **忙循环卡死**：`%CPU≈100-132%`，永不退出 —— 真正的「挂死」

### 正确修复方向（三项并行，缺一不可）

1. **DAV-995**：测试全局离线隔离。**只拦 `connect` 不够**，必须覆盖已存在的 baostock 全局 socket（复位/关闭模块级 `context`，或在 `recv` 层拦截）。
2. **新增必做项**：baostock 调用路径的 **EOF 与 socket 超时处理**。`recv()` 返回 `b""` 必须立即判定连接关闭并抛错，不得继续循环。已并入 DAV-995。
3. **DAV-989/991**：线程清理与资源收尾。注意线程池超时无法杀掉已运行线程，这是该缺陷不可自愈的根本原因。

**DAV-992 保持 cancelled，不再施工。**

### 处置记录

- 已清理 PID 6619（DAV-996 卡死进程）与 PID 12371（DAV-992 遗留进程），二者合计长时间占用约 2 个核，干扰所有并发门禁跑。
- 已 steer 通知 DAV-995、DAV-996 承担方更正方案。
- **未**修改 Clash 配置、**未**改模型配置、**未**改生产代码。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
