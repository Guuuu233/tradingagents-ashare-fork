# 卡 A：pytest 网络隔离架构（audit hook 双层）

**只做测试基础设施。B（baostock）、C（lifespan）本轮不建卡、不派工，不要顺手捎带。**

架构决定见仓库 `work/2026-09-16-guardrail-architecture-decision.md`（必读）。
失败复盘见 `work/2026-09-16-gate0-guardrail-fork.md`。

## 一、候选基线与白名单

- **直接父**：`5a0320f0618d203e95e7197e08b110c7850078d4`（= `origin/codex/dav-4-p2a-trunk` tip）
- **不得**基于 `28c995d` / `896eaca3` / `714f620e` 等已作废候选派生

**允许修改的文件（严格白名单）**

| 文件 | 范围 |
|---|---|
| `tests/conftest.py` | 护栏实现主体 |
| `tests/test_offline_network_guardrail.py` | 护栏自测 |
| `tests/test_v03_return_measure.py` | **仅真实网络用例的分类标记**，不得改业务断言 |
| `pyproject.toml` | **仅在确需调整 pytest 配置时**允许 |

**明确禁止纳入**

- ❌ provider 逻辑（`cn_baostock_provider.py` 等）→ 属卡 B
- ❌ lifespan / API 生命周期 → 属卡 C
- ❌ 历史案例逻辑（`historical_cases.py`）→ 属 DAV-998，已冻结
- ❌ `tests/_temp_plugin.py` 临时诊断文件 —— **不得提交进候选**

## 二、架构要求

### 1. 默认离线，显式联网授权

- **不要仅凭 `-m network` 推断运行模式**
- 必须有**明确的联网开关**（如环境变量 `RT_NETWORK_ENABLED=1` 或专用 pytest 选项）
- **遗漏开关时 fail-closed**（即默认离线，不是默认放行）
- RT-FULL-OFFLINE 与 RT-NETWORK **必须是不同进程**

### 2. 双 hook 分离

| 层 | 安装顺序 | 职责 | 日志 |
|---|---|---|---|
| 观察 hook | **先**装 | 无条件记录全部事件，**绝不拒绝** | 独立观察日志 |
| 拦截 hook | **后**装 | **只负责拒绝** | 独立 deny 日志 |

**必须覆盖的事件**（已实测，Python 3.10.20）：

```
socket.connect        ← connect() 与 connect_ex() 共用此事件，一个 handler 即可
socket.getaddrinfo    ← 必须拦！create_connection 的 DNS 解析发生在 connect 之前
socket.gethostbyname
socket.sendto         ← UDP
```

**hook 安装后不再按用例启停。彻底删除 `depth` 作为安全边界。**

### 3. 外部测试地址使用 TEST-NET 保留网段

```
TEST-NET-1  192.0.2.0/24
TEST-NET-2  198.51.100.0/24
TEST-NET-3  203.0.113.0/24
```

⚠️ **不得使用 `198.18.x`** —— 本机 Clash fake-IP 池就在该网段（阻断 4 的 `198.18.0.103/104` 即是），
用它会让「合成流量」与「真实外连」无法区分。
⚠️ 也不要用 `127.0.0.1:9`（代理黑洞地址，会与黑洞混淆）。

### 4. 既有用例级 socket mock 予以保留

- `tests/test_fund_flow_scale_consumption.py:48` 的 `guard_no_network_calls`（autouse，`patch("socket.socket.connect")`）
- `tests/test_horizon_return_labels.py:806` 的 `monkeypatch.setattr(socket, "socket", block_socket)`

这些是**先于全局护栏存在的、合法的用例级防护**，与 hook 分属不同层，**必须共存**。
**不得以「非法 mock」为由删除。**

护栏在被**覆盖 / 恢复 / 整类替换 `socket`** 的情况下**不得级联崩溃**——
上一轮正是 `socket.socket` 被替换成函数后，护栏仍按类读写 `.connect`，导致 5143 个用例级联 `AttributeError`。
检测到篡改时应**指名报出 nodeid 与阶段**，而非整体崩溃。

## 三、验收门禁

1. **无代理黑洞**的 RT-FULL 必须**自然结束**，并保持 **20 项基线失败**（零新增）。
   八个代理变量（`http_proxy`/`HTTP_PROXY`/`https_proxy`/`HTTPS_PROXY`/`all_proxy`/`ALL_PROXY`/`no_proxy`/`NO_PROXY`）全清并附环境快照。
   不得带任何 `--deselect` / `-k` / 路径限定。
2. 护栏自测产生的非本地事件，必须在**观察日志与 deny 日志中一一配对**（每条尝试都有对应 deny 记录）。
3. 观察日志与 deny 日志**均有正控 sentinel 记录，且 PID 等于 pytest 主进程 PID**。
   正控做法：同进程内起 loopback listener（`bind 127.0.0.1:0`），真实 connect 一次。
4. `lsof` 证据**只能表述为「持续采样未观察到成功外连」**，
   ❌ **不得写成绝对 OS 级证明**。同时**记录进程树监测范围**（监测了哪些 PID、采样间隔）。
5. **子进程边界**：Python audit hook 只覆盖当前解释器，子进程不继承（已实测）。
   要么监测全部后代进程，要么在报告中**明确限定结论范围**为「仅 pytest 主进程及其线程」。
6. **日志文件名必须本次运行唯一**（含时间戳或 run id）。
   ❌ **禁止 `/tmp` 通配清理**（如 `rm -f /tmp/xxx_*.log`），只删本 run 自己创建的具体路径。

### 能力边界（必须写进报告，不得越界）

PEP 578 保证 audit hook **不可被移除或替换**（实测 `sys` 无 `removeaudithook`），
但 PEP 578 **同时明确 Python 层 audit hook 不是安全沙箱**，恶意代码仍可绕过。
本方案定位为**「防测试误出网」的工程护栏**，❌ **不得升级为安全隔离承诺**。

### 判定纪律

**卡死 / 成功外连 / 漏拦 —— 出现一次即失败，不允许靠重跑洗掉。**
这几类都是间歇性缺陷，重跑变绿只说明没命中，不说明已修复。如实上报，不要美化。

## 四、交付与后续

- 交付**完整 40 位 SHA** + 上述全部证据
- 随后由**独立审核员**审**同一 SHA**（写审分离）
- **A 同 SHA 复审通过后**，再决定：先把基础设施单独合入，还是以它为父提交启动 B、C
- **A 未通过前，B、C 的全量证据均不具备可信基础** —— 故本轮不建 B、C

## 五、不变项

主线 `5a0320f` 继续 **HOLD**：不合入、不部署、不重启线上服务、不写生产库、不历史重写。
DAV-998（`33bc23d`）停在 `in_review`，**不得合入**。
进程清理按 PID 精确 `kill -9`，**严禁宽泛 `pkill -f pytest`**。
