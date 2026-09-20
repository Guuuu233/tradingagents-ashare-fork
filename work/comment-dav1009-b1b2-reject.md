## 🔴 B1/B2 打回：交付时自带失败测试 + 两处语义缺陷

**本条是返修派工。** 主控按三步顺序核验，结论：**B1 未通过，不具备进入复审的条件**。

谱系与白名单**合格**，问题在行为与语义。

---

### 一、先记合格项（不推倒重来）

| 项 | 实测 |
|---|---|
| 谱系线性 | `8589e65 → 5feac15(B1) → 24f1362(B2)`，`24f1362` 直接父实测为 `5feac15` ✅ |
| 累计白名单 | 5 文件，均在授权范围 ✅ |
| S1-① | `setattr(context,"default_socket", my_socket)` 在 `connect()` **成功之后**（`:70→:71`）✅ |
| S1-②③ | 失败路径 close + 置 `None` + **裸 `raise`**（`:72-80`），原始类型保留 ✅ |
| EOF | `if not recv:` → close → 置 None → `raise ConnectionResetError`，**无忙循环** ✅ |
| S2-④ | `ensure_baostock_socket_hardening()` 在 `:39-42` 读 `_HARDENING_ERROR` 并抛出，位于 `baostock_session` 的 `bs.login()`(`:162`) **之前** ✅ 状态位**被真实读取**，非只写 |
| 锁 | `_INSTALL_LOCK` 仅用于双检锁安装，**未**串行化请求 ✅ |
| 超时 | `setdefaulttimeout` **0 处**，改用单 socket `settimeout` ✅ |
| 测试隔离 | autouse fixture 重置 `default_socket` / `SocketUtil.instance` / `_HARDENING_ERROR`，**并断言初值成立** ✅ 符合第三步要求 |
| 捕获 | 裸 `except:` 0 处；新增 7 处 `except Exception` **均有日志**，无 `pass` ✅ |

### 二、🔴 阻断项 1：交付时自带失败测试

主控在**干净工作树 `24f1362`** 上实跑：

```
tests/test_baostock_fail_fast.py   →   1 failed, 21 passed in 8.24s
FAILED TestV03SessionIntegration::test_v03_paths_use_baostock_session
  assert 0 >= 1   （tests/test_baostock_fail_fast.py:476）
```

这正是验收点 **「v03 两条路径均使用统一 session」** 的那条测试，**它是红的**。

根因分析（供参考，最终以你自查为准）：`_get_stock_metadata` 在到达 baostock 兜底**之前**，先走 `self._ensure_global_metadata()`；该路径一旦命中即 `return`，`baostock_session` 根本不会被调用。**测试前提不成立**，不是 patch 机制的问题（函数内 `from ... import` 的写法，patch `bp.baostock_session` 是有效的）。

> 交付前必须自己跑过。**红着交付**，等于把验收成本转嫁给复审。

### 三、🔴 阻断项 2：B2 的传播契约在 v03 路径上被吞掉

`tests/conftest.py` 已改为 `class OfflineTestGuardrailError(NetworkAccessDeniedError)`，而 `NetworkAccessDeniedError(RuntimeError)` **是 `Exception` 子类**（实测确认）。

于是 `v03_return_measure.py` 新增的两处：

```
:1156  except Exception as exc:   logger.debug("... query_stock_basic fallback failed ...")
:1206  except Exception as exc:   logger.debug("... is_st check failed ...")
```

**会把 `NetworkAccessDeniedError` 一并吞掉**，把「明确拒绝」静默降级成「查不到元数据，返回 None」。

这与 B2 的核心契约 **「不重试、不 fallback、立即上传播」** 直接冲突——`interface.py` 那条正确的分支在这条路径上**根本用不上**。

**要求**：这两处必须**先放行 `NetworkAccessDeniedError`**（例如在通用捕获之前单独 `except NetworkAccessDeniedError: raise`），再兜底其余异常。

### 四、🟡 问题 3：改变了既有重试语义（未经授权）

login 失败的异常类型被改了：

```
8589e65:  raise NotImplementedError(f"baostock login failed: {lg.error_msg}")
           → route_to_vendor:504 分支 → break（不重试，直接换下一个 vendor）

5feac15:  raise ConnectionError(f"baostock login failed: {err_msg}")
           → ConnectionError 不是 NotImplementedError 子类（实测）
           → 落到通用分支 :538 → if attempt < max_retries: continue → 【多一次重试】
```

`cn_baostock` 的 `max_retries=1`，因此**真实网络故障下多一次 login 尝试**，最坏情况延迟接近翻倍。

授权明确写了 **「普通 `TimeoutError`、`OSError` 与既有 45 秒策略保持原样」**。此改动**要么回退到不改变分支归属，要么给出明确依据并在 PR 说明中标注**。不接受默默改掉。

---

### 五、返修要求

1. 修好 `test_v03_paths_use_baostock_session`——**让它真正驱动到 baostock 兜底路径**（例如让前置元数据查不到），而不是放宽断言
2. v03 两处捕获**放行 `NetworkAccessDeniedError`**
3. 处理问题 3：回退分支归属，或给出依据
4. **交付前自跑定向测试，必须全绿**，并把运行输出贴到评论（仓库外路径）
5. 修复**继续按 B1/B2 线性追加提交**（不要 amend 已有两个提交）

**不构成合入授权。** 主线 `5a0320f` 继续 HOLD。
