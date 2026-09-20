# 护栏架构决定 + Gate 0 判据修正（2026-09-16 定案）

> 状态：**架构已定，实施卡待拆。** 按裁定：先完成 DAV-998 独立候选与只读审查（**仍禁止合入**），
> 随后按本文件拆卡实施。**不再建「大 DAV-995」。**

## 一、为什么换架构

Gate 0 FAIL 的根因不是某个 bug，是**架构选错了层**：
护栏建在 monkeypatch wrapper 上，而 wrapper 本身可被任何测试替换（阻断 4 / 身份漂移皆源于此），
且 `depth` 计数与真实 patch 状态是两回事，无法作为安全边界。

## 二、架构决定

### 1. audit hook 作为离线 pytest 进程的**主要拦截层**

### 2. 观察与拦截**彻底分离**

| 层 | 安装时机 | 职责 | 日志 |
|---|---|---|---|
| **观察 hook** | **先**安装 | **无条件记录全部事件**，绝不拒绝 | 独立观察日志 |
| **拦截 hook** | **后**安装 | **只负责拒绝** | 独立 deny 日志 |

两者**使用独立日志与独立状态**，杜绝「拦截者兼裁判」的假绿。

### 3. 必须覆盖的审计事件（已实测确认，Python 3.10.20）

```
sock.connect()              -> socket.connect
sock.connect_ex()           -> socket.connect          ← 同一事件，一个 handler 即可覆盖两者
socket.getaddrinfo()        -> socket.getaddrinfo
socket.gethostbyname()      -> socket.gethostbyname
socket.create_connection()  -> socket.getaddrinfo, socket.__new__, socket.connect
sock.sendto()               -> socket.sendto           ← UDP 独立事件
```

⚠️ **必须同时处理 DNS 事件**：`create_connection` 的事件序列证明 **DNS 解析发生在 connect 之前**，
只拦 `socket.connect` 会放过真实的域名解析。

### 4. 进程分离

**RT-FULL-OFFLINE 与 RT-NETWORK 必须是不同进程。**
离线进程中 hook 安装后**不再按用例启停**，**彻底删除 `depth` 作为安全边界**。

### 5. 既有用例级 socket mock **予以保留**

它们与下层 audit hook **可以共存**（hook 在解释器层，mock 在 Python 对象层，互不覆盖）。
**不得以「非法 mock」为由删除既有防护。**

## 三、能力边界（必须写进报告，不得越界宣称）

- **PEP 578**：audit hook **不能被移除或替换**（实测 `sys` 无 `removeaudithook`）——
  这是 wrapper 不具备的完整性属性
- **但 PEP 578 同时明确**：Python 层 audit hook **不是安全沙箱**，恶意代码仍可绕过
- 因此定位限定为：**「防测试误出网」的工程护栏**，
  ❌ **不得升级为安全隔离承诺**
- **hook 只覆盖当前解释器，子进程不继承**（已实测）→ 子进程须**另行监测**

## 四、Gate 0 判据修正

改用 audit-hook 拦截后，**护栏自测会故意产生非本地 audit 事件**，
因此**不能再要求「非本地事件绝对为零」**。修正为五条：

1. **除明确列出的护栏自测 nodeid 外**，非本地尝试为零
2. **每个非本地尝试都有对应的独立 deny 记录**（观察日志中的每条非本地事件，都能在 deny 日志中找到配对）
3. **OS 层没有成功建立的非本地连接**（外部佐证，如运行期 `lsof` 采样，不依赖 Python 自陈）
4. **观察日志与 deny 日志均有正控 sentinel 记录，且 PID 与 pytest 主进程一致**
5. **子进程另行监测**；若未监测，报告须明确限定结论范围为「仅 pytest 主进程及其线程」

判定纪律不变：**卡死 / 成功外连 / 漏拦，出现一次即失败，不允许靠重跑洗掉。**

## 五、拆卡规则（关键，防止重蹈覆辙）

**不再建「大 DAV-995」。** 三者**分别成卡、分别提交**，最后才形成整合候选：

| 卡 | 范围 |
|---|---|
| **卡 A** | pytest 网络隔离架构（观察/拦截双 hook、事件覆盖、进程分离、删除 depth） |
| **卡 B** | baostock 唯一入口 + EOF 硬化 + fail-closed（含静态守卫：`scheduler/`、函数级放行、AST + 动态导入） |
| **卡 C** | lifespan 全局状态恢复（同一 loop 五步两断点 + `is prior_executor` / `_shutdown is False` 身份断言） |

理由：上一轮把**测试基础设施 + 生产 provider + API 生命周期**滚进同一个候选，
导致目标持续移动、每次返修都引入新缺陷、证据反复作废。分卡后每张卡的证据边界清晰、可独立判定。

## 六、DAV-998 的边界（不变）

两文件独立候选，定向测试 + 只读复审，**最多到达「候选审查完成」**。
在全局 RT-FULL 门禁重新可信之前**不得合入**。
