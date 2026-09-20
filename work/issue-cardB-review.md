## 卡 B 候选独立复审（只读）

对 DAV-1009 卡 B 候选执行**独立只读复审**。**不得修改代码、不得提交、不得合入、不得部署、不得重启服务、不得写生产库。**

---

### 一、被审对象

```
候选 SHA   4cc436ac058b3d4a57c2e52ab3c6da3ab76ff96b
直接父     04fc2237d044ab97b91ce36bab44b8174737a85a
累计基线   8589e6526e470d6da056a865f21ffa1532952b8e （卡 A 冻结候选）
提交链     8589e65 → 5feac15(B1) → 24f1362(B2) → 04fc223(返修) → 4cc436a(返修)
工作树     /Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/dav-1009-a563fbe02617/workdir/tradingagents-ashare-fork
```

**累计白名单（5 文件，主控已核，均在授权范围）：**

```
tests/conftest.py
tests/test_baostock_fail_fast.py
tradingagents/dataflows/interface.py
tradingagents/dataflows/providers/cn_baostock_provider.py
tradingagents/eval/v03_return_measure.py
```

### 二、背景：本卡解决的根因（已由实证确立）

```
① api/main.py:339  setdefaulttimeout(60) → 新建 socket 一律 timeout=60
② baostock connect(10030) 被卡 A 护栏拒绝
③ socketutil.py:39  except Exception 吞掉护栏异常
④ socketutil.py:41  在 except 之外，仍把【从未连接成功】的 socket 写入模块级全局
⑤ login() 继续走 send_msg() → send() 因 timeout=60 走 internal_select 等「可写」
⑥ socket 从未建连 → select 睡眠，0% CPU，直到外层 future 45s 超时
```

### 三、复审必须逐条验证的点

**S1：`connect` 被护栏拒绝（发生在 `bs.login()` 内部）**

| # | 断言 |
|---|---|
| S1-1 | `bs.login.call_count == 1` |
| S1-2 | `send_msg` **零次**执行 |
| S1-3 | socket 已关闭 |
| S1-4 | `context.default_socket is None` |
| S1-5 | 耗时**明显低于 45 秒**，工作线程结束 |
| S1-6 | **保留原始异常类型**（清理后裸 `raise`，`__cause__ is None`） |

**S2：加固安装自身失败（尚未进入 `bs.login()`）**

| # | 断言 |
|---|---|
| S2-1 | `bs.login.call_count == 0` |
| S2-2 | 调用路径立即停止 |
| S2-3/4 | 转换为生产侧加固错误，**且 `__cause__` 是原始异常** |

**B2 传播语义**

- `NetworkAccessDeniedError`（生产侧定义）→ **不重试、不 fallback、立即上传播**
- `OfflineTestGuardrailError` **继承**它（依赖方向：测试 → 生产契约）
- v03 两处（`:1165`、`:1239`）**先 `except NetworkAccessDeniedError: raise`**，再兜底其余
- **普通 `TimeoutError` 与既有 45 秒重试策略保持原样**
- baostock **非零 login 结果** → 保持 `NotImplementedError`（不重试、转下一 vendor）

**通用纪律**

- 禁 `socket.setdefaulttimeout()`；锁**只允许**保护一次性安装，不得串行化请求
- 禁裸 `except:` 与 `except Exception: pass`；捕获须留日志
- `NotImplementedError` 不得用于表达**网络不可达**
- 不得在生产路径增加探测性连接
- `registry.py`、`api/main.py`、vendor site-packages、数据库、部署状态**均不得改动**

### 四、测试有效性审查（重点）

- **状态位不能只用于报告**：把状态位改名或删掉，行为断言是否仍成立？只写不读 = fail-open
- **删掉被测修复，测试会不会依然绿？**
- 是否用 `side_effect=` 注入了本该由真实路径产生的异常？
- 「清理 / 置空 / 计数为零」类**否定式断言**，是否被后续成功路径覆盖而假绿？
- v03 两条路径是否**各自独立**证明（不得用一个累计 `call_count >= 1` 代替）

### 五、主控已完成的实测（供对照，**请独立复跑，勿直接采信**）

```
tests/test_baostock_fail_fast.py                       25 passed in 2.55s
+ tests/test_v03_return_measure.py
+ tests/test_offline_network_guardrail.py              102 passed, 1 deselected in 6.81s
registry.py / api/main.py                              未改
```

### 六、交付

输出 🔴阻断 / 🟡建议 / 🟢认可 三级结论，每条给**文件:行号**与依据。

**本卡不构成合入授权。** 主线 `5a0320f` 保持 HOLD；DAV-998（`33bc23d`）保持 `in_review` 不得合入。
