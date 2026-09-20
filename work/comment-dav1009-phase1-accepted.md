## ✅ 第一阶段闭环：判定为 **H2**，因果链经主控独立复核成立

**本条是裁定存档，不是实施授权。** 未授权任何代码改动，**不得开始第二阶段**，不得 commit、不得动生产文件。

---

### 一、四项证据，主控逐项独立复核（不采信转述）

| 项 | 主控实测 | 结论 |
|---|---|---|
| ① 阻塞点 | `socketutil.py:65 send_msg` → `default_socket.send(...)` | ✅ 与栈一致 |
| ② 连接唯一性 | observer 全进程 `socket.connect` 共 **3** 次：`127.0.0.1:58068`、`192.0.2.254:65534`（sentinel）、`public-api.baostock.com:10030`。指向 10030 的**仅 1 次**，窗口内**无并发竞争** | ✅ 唯一性成立 |
| ③ deny 配对 | observer `10:48:23.462515` → deny `10:48:23.462720`，**相距 205 微秒**，同 PID(89561)、同 nodeid | ✅ 1:1 配对成立 |
| ④ socket 身份 | 阻塞帧 socket 与 `context.default_socket` **同一对象** `py_id=0x115ea6fe0`、`fd=15`、`getpeername=ENOTCONN`、`gettimeout=60.0`、`so_nwrite=0/131072`、lsof `TCP *:* (CLOSED)` | ✅ 身份锁定 |

### 二、完整因果链（每一环都有独立出处）

```
① api/main.py:339   socket.setdefaulttimeout(float(os.getenv("TA_SOCKET_DEFAULT_TIMEOUT","60")))
                     → 此后新建 socket 一律 timeout=60（非阻塞 OS socket + Python 超时模式）
② baostock SocketUtil.connect() 创建 socket 并 connect(public-api.baostock.com:10030)
                     → observer 记录 .462515 → 卡 A 拦截 hook 抛 OfflineTestGuardrailError（.462720 DENY）
③ socketutil.py:39   except Exception:  ← 【吞掉护栏异常】，只 print"服务器连接失败"
④ socketutil.py:41   setattr(context,"default_socket", mySockect)
                     ← 【在 except 之外】，把【从未连接成功】的 socket 写入模块级全局
⑤ loginout.py:65     login() 继续走 send_msg()
⑥ socketutil.py:65   default_socket.send(...)
                     → 因 gettimeout=60.0（非 None、非 0），CPython sock_send 走 internal_select
                       等待「可写」；socket 从未建连 → 【select 睡眠，0% CPU】，最长 60s
⑦ interface.py:483   外层 future.result(timeout=45) 先到期 → 观察到的 ~45 秒停顿
```

**`so_nwrite = 0`** 是关键反证：发送缓冲**完全空**，排除了「发送队列打满」。阻塞不是因为数据发不出去，而是**socket 根本没连上，select 永远等不到可写**。

### 三、主控更正自己两处判断

1. 我此前预测「H2 路径应当很快返回（`send` 在未连接 socket 上抛 `ENOTCONN`），挂不到 45 秒」——**这是错的**。原因正是 ①：`setdefaulttimeout(60)` 把 socket 置于**超时模式**，`send` 于是走 `select` 等待而**不是立即失败**。**H2 完全可以挂住**。
2. 我此前假设「发送队列已满」——被 `so_nwrite=0` **直接证伪**。

### 四、关于「connect 台账里找不到同 ID 同 fd 的 connect」

这是**结构性结果，不是证据缺失**：`tests/conftest.py:307/308` 的两个 hook 在 **conftest 导入时**注册，早于 `-p` 插件 `pytest_configure` 注册的探针 hook。**拦截 hook 抛异常后，后注册的探针 hook 不再被调用**，所以台账里自然没有这条。
该环节由**卡 A 自己的 observer 日志**补齐（含 target/PID/nodeid），socket 身份则由探针从阻塞帧独立锁定——**两条独立来源互相印证**。

### 五、定性结论

- **判定表 v2 → 第一行：H2**（有配对 deny、该 socket 从未 ESTABLISHED）
- **卡 A 的护栏工作正常**——它**成功拦截**了这次外连。**不返回卡 A**，卡 A 冻结候选 `8589e65` 维持。
- **根因在 vendor 库 baostock**：吞掉异常（`:39`）+ 失败后仍写入全局（`:41`）+ 全包 0 处 `settimeout` + `recv` 循环无 EOF 判断。

---

### 六、下一步（**等主控授权，不得自行开始**）

第二阶段的最小修改点与文件白名单**尚未划定**。在收到明确的实施授权前：

- 不改任何生产代码
- 不 commit
- 保持工作树 `8589e65`、`status` 为空

主线 `5a0320f` 继续 HOLD；DAV-998（`33bc23d`）保持 `in_review` 不得合入。
