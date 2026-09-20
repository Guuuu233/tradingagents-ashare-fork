# 终版审计结论与唯一收口路线（2026-09-16）

核验时间：2026-09-16 19:02 (UTC+8)。本文为裁定文档，取代此前分散在各卡评论中的口径。

## 0. 裁定

**主线继续 HOLD。现有 4 个候选一律不得单独合入、不得部署。**
必须从 `5a0320f` 派生**唯一整合候选**，完成双层门禁 + 同 SHA 独立复审后，才可讨论快进。

---

## 0b. 第二轮：DAV-1003 候选 `714f620e` 已打回（2026-09-16 晚）

整合方向正确，§7 的 B1–B6 基本落实（已复验：`_session` 全局超时污染**确已删除**、捕获已收窄、`is_baostock_hardened()` 状态位已暴露、`diff --check` 干净、10 文件白名单合规、直接父为 `5a0320f`）。但出现**两个新缺陷**，DAV-1004 的 PASS 同时失效：

**阻断 1 —— lifespan 启动期异常仍泄漏全局状态**
`api/main.py` 时序：`:341` 改全局 socket timeout → `:368/:372` 挂 executor → `:378` `_get_runtime_identity()` → **`:442` 才 `try:`**。`try/finally` 起点在所有全局副作用**之后**，故 341→442 间任何异常绕过 `finally`。实测：`socket_after=60.0`、`default_executor_shutdown=False`。
→ 与 DAV-989/996 原本要修的是同一类泄漏，只是触发条件从运行期换成启动期。

**阻断 2 —— baostock 硬化失败仍是 fail-open**
`ensure_baostock_socket_hardening()`（`:110`）失败时记日志并 `return False`，但 `_bs()`（`:164`）**丢弃返回值**，照常返回未硬化的 baostock。实测：`returned_baostock_module=baostock`、`hardening_state=False` → EOF 活锁照旧复活，只是多了一条日志。

**两者的共性（写进复审口径）**：状态位/返回值**只用于报告，没用于决策**。今后复审遇到「新增状态位/返回码」的改动，必须追问：**谁在读它？读到坏值时行为变了吗？**

**返修要求**：`try:` 上移至第一处全局副作用之前、`yield` 置于同一 `try` 内；`_bs()`/`_session()` 硬化失败时 **fail-closed** 报错。
**fail-closed 在此安全**：DAV-998 已将 vendor 类失败归为**可重试**，拒绝会被归类为可重试 refusal，案例留在回填队列，**不造成永久数据丢失**；反之 fail-open 得到的是静默活锁 + 线程挂死。

**必补两条测试**：①启动期异常也恢复状态；②硬化失败时调用方 fail-closed（并断言该失败被归为可重试，锁住与 DAV-998 的耦合）。

**新增流程禁令**：禁止对 `/tmp` 共享路径通配删除（本轮审核员跑过 `rm -f /tmp/rt_full_*.log /tmp/test_db_*.sqlite3`，会删掉其他 run 的日志与测试库）。只删本 run 自己 `mktemp` 出的具体路径——与已禁止的宽泛 `pkill -f pytest` 同类危害。

## 1. 现场边界（已实测核验，非转述）

| 项 | 实测值 | 核验方式 |
|---|---|---|
| 远端主线 | `5a0320f0618d203e95e7197e08b110c7850078d4` | `git ls-remote` |
| 生产库 SHA256 | `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010` | `sha256` on `data/tradingagents.db`（285,339,648 B，未变） |
| 服务 8000 | 无监听 | `lsof -nP -iTCP:8000 -sTCP:LISTEN` 空 |
| 活跃 run | 0（全部结束） | `steer` |
| 卡住进程 | 无 | `work/check-stuck-tests.sh` |

## 2. 四个候选（**父提交全部为 `5a0320f`，是平行分叉，不是链**）

| 卡 | 候选 SHA | 改动文件 | 状态 | 复审 |
|---|---|---|---|---|
| DAV-989 | `4b0c9eeb` | `api/main.py`, `tests/test_api_smoke.py` | in_review | DAV-991 done |
| DAV-995 | `85db74d1` | `tests/conftest.py`, `tests/test_job_lifecycle.py`, `tests/test_offline_network_guardrail.py`, `tests/test_v03_return_measure.py`, `cn_baostock_provider.py` | in_review | DAV-1001 done（**漏审**） |
| DAV-996 | `c8038980` | `api/main.py`, `conftest.py`, `tests/test_api_smoke.py`, `cn_baostock_provider.py` | in_review | DAV-999 done |
| DAV-998 | `4efe4163` | `tests/test_historical_cases.py`, `historical_cases.py` | in_review | DAV-1002 done |

**实测冲突矩阵**（按改动文件交集）：

```
cn_baostock_provider.py : DAV-995 ∩ DAV-996     ← 两者都实现了 baostock context 清理
api/main.py             : DAV-989 ∩ DAV-996
tests/test_api_smoke.py : DAV-989 ∩ DAV-996
```

所以「多个候选各自 PASS」**不能**推导为「可以依次快进」。

## 3. 根因的统一口径（终版）

不是单一原因，是四层叠加：

1. **baostock 走裸 TCP**，不读 `http_proxy`/`https_proxy`，故普通代理隔离拦不住它，测试会穿透到真实行情服务器
2. **网络并非不可用**——受控登录/查询可成功，不能把问题归结为「没网」
3. **阻塞式慢确实存在**：%CPU≈0，单次 ≈60s 且叠加重试
4. **永久活锁也确实存在**：对端关闭后原版 `send_msg` 的 `recv()` 恒返回 `b""`，循环不退出，单核高 CPU

「只是慢」和「网络断了」都不准确。**CLOSE_WAIT 是确诊依据，CPU 不是**（两种形态 CPU 特征完全相反）。

## 4. DAV-995 —— 测试门已过，代码规范门未过

**测试侧成立**：独立 RT-FULL `20 failed, 4818 passed, 1 skipped, 5 deselected, 471.01s`，失败集合与 `5a0320f` 基线（DAV-941 合入时所跑：`20 failed, 4801 passed, 1 skipped, 3 deselected`）**逐项一致，零新增**。耗时 28min → 7min51s，护栏与 EOF 修复效果真实。

**基线争议澄清**：交付报告把对照写成 `28d1adc` 而非直接父 `5a0320f`，属引用不规范；但 `5a0320f` 的同口径全量基线确实存在（DAV-941 那一刀留档），可直接比对。**不构成功能阻断。**

**`@pytest.mark.network` 分类是正确做法，不是放宽断言**——但默认 RT-FULL 会 `-m 'not network'` 排除它，所以必须固化 RT-NETWORK 独立门禁，不能只靠一次人工联网复跑（`1 passed, 51 deselected in 14.83s`）。

### 4.1 阻断项：违反 AGENTS.md:115-116 铁律

`AGENTS.md:115` 禁止裸 `except:` 与 `except Exception: pass`，捕获必须具体且留日志；`:116` 禁止静默失败。

85db 在生产文件 `cn_baostock_provider.py` 新增 **9 处 `except`**。按后果分级（已逐处核验，**不能一刀切**）：

**🔴 真静默，必须改（3 处）**

- **`:102` —— 最严重。** 包住整个 `ensure_baostock_socket_hardening()`：

  ```python
  bssock.SocketUtil.connect = safe_connect
  bssock.send_msg = safe_send_msg
  _BAOSTOCK_HARDENED = True
  except Exception:
      pass          # ← baostock 内部 API 一变，硬化静默失效，退回原版活锁实现
  ```

  后果：无日志、无上报、`_BAOSTOCK_HARDENED` 保持 False，**本卡修的活锁静默复活**。这不是普通的吞异常，是「修复本身可以静默消失」。
  改法：`except (ImportError, AttributeError) as e:` + `logger.warning` + 暴露可查询的硬化状态位。

- **`:170`** `bs.logout()` 的 `except Exception: pass`
- **`:179/:182`** context 清理的双层 `except Exception: pass`

**🟡 清理后重抛，等级低但仍需收窄（6 处）**

`:44-51`、`:88-97` 结尾都是 `raise`，属 cleanup-then-reraise，**不是静默失败**；内层包 `sock.close()` 的 pass 应收窄为 `except OSError`。

> 对 DAV-1001「无中高问题、全面 PASS」的判定：确系漏审。但漏审范围应精确到上述 3 处真静默 + 6 处待收窄，而非笼统的「多处静默吞错」。

### 4.2 新增阻断项（本次核验发现，原审计未覆盖）

`85db` 的 `_session()`：

```python
orig_default_timeout = socket.getdefaulttimeout()
try:
    socket.setdefaulttimeout(timeout)      # ← 进程级全局
    lg = bs.login()
finally:
    socket.setdefaulttimeout(orig_default_timeout)
```

`socket.setdefaulttimeout` 是**进程级全局可变状态**。在 FastAPI + executor 的多线程运行时下：

1. 该窗口内**任何其他线程**新建的 socket 都会继承这个 5s 超时（污染无关调用方）
2. 两个线程并发进出该窗口时，`finally` 的恢复会互相覆盖，可能把超时永久留在全局

**这与 DAV-989/996 正在修的 lifespan 全局状态泄漏是同一类 bug**——本卡一边修全局污染，一边引入了新的全局污染。
且硬化后的 `safe_connect` 已经做了 `mySockect.settimeout(timeout)`（**每 socket** 级），全局设置很可能是冗余的。
改法：删除全局 set/restore，只依赖 per-socket `settimeout`；若 login 路径确需超时，走 baostock 硬化入口而非全局。

### 4.3 待决策（非阻断）

`DEFAULT_BAOSTOCK_SOCKET_TIMEOUT = 5.0s` 是 per-recv 超时，一般够用；但拥塞链路下的大区间历史查询存在误杀风险。已支持 `BAOSTOCK_SOCKET_TIMEOUT` 覆盖，需在整合时明确记录这是一个**有意识的默认值选择**。

## 5. DAV-996 —— 有价值，但定位必须降格

**有价值**：lifespan 恢复全局 socket timeout、executor 重入、清理 baostock context 与库残留、阻止 api_smoke 穿透真实行情。

**不能宣称「根治 EOF 活锁」**：`_cleanup_baostock_context()` 在 `bs.login()` / 查询 / `bs.logout()` **返回之后**才执行。若调用已进入 EOF 无限循环，**控制流永远到不了清理代码**。它治的是「残留污染」，不是「活锁」。活锁只能由 DAV-995 的 `if not recv: raise` 在循环内部治。

**其余缺陷**：

- `api/main.py` 复位逻辑在裸 `yield` 之后，无 `try/finally` —— 运行或关闭阶段抛异常时复位被跳过
- `test_api_smoke.py` 每个用例后**删整张表**，当前串行隔离库下可行，但过宽，会清掉 session 级夹具或未来并发用例的数据；应改事务回滚 / 独立库 / 按本用例 ID 精确清理
- DAV-999 的「完整 RT-FULL」**额外带了 `--deselect`**，不是正式完整门禁
- 它的 `_cleanup_baostock_context` 同样是 `except Exception: pass` × 3

## 6. DAV-998 —— 核心正确，随整合候选一并收口

逻辑已改对：默认可重试；仅正面识别的确定性缺陷为终态；`vendor_refuse`/超时/连接拒绝/供应商不可用/EOF 全部可重试；旧格式与历史误标 `terminal=True` 记录能自愈；已补「失败→恢复成功」端到端回填测试（4 个场景参数化）。

证据：定向 `59 passed`；完整全量自然结束 `21 failed, 4812 passed, 1 skipped, 3 deselected`，多出的 1 项正是那个未标记的真实网络用例——**即 DAV-995 后续分类为 `network` 的同一个用例**，核心逻辑零新增失败。DAV-1002 同 SHA PASS。

唯一形式问题（已复验）：

```
tests/test_historical_cases.py:2055: new blank line at EOF.
```

须在整合候选中清掉，保证 `git diff --check` 全干净。

## 7. 唯一收口路线（DAV-1003）

从 `5a0320f` 新建整合分支，**人工择优合并，禁止整提交盲目叠加**：

1. 纳入 DAV-998 的 T+1 refusal 返修（`4efe4163`）
2. 纳入 DAV-995 的离线护栏 + baostock EOF/超时修复（`85db74d1`）
3. **修掉 §4.1 的 3 处真静默**（尤其 `:102` 硬化安装），收窄 §4.1 的 6 处清理捕获为 `OSError`
4. **删掉 §4.2 的 `socket.setdefaulttimeout` 全局污染**，改为 per-socket
5. `cn_baostock_provider.py` 的 baostock 清理**必须收敛为唯一一份实现**（DAV-995 的硬化 + DAV-996 的 `SocketUtil.instance = None`），不得两套并存
6. 从 DAV-989 / DAV-996 择优取生命周期、executor、socket timeout、库隔离改动，**人工解冲突**
7. lifespan 状态复位改 `try/finally`
8. 库测试清理改事务隔离或按本用例 ID 精确删除，不再删全表
9. 清掉 DAV-998 的 `diff --check` 告警

### 双层门禁（整合 SHA 上重跑）

- **RT-FULL-OFFLINE**：默认离线全量，**不带任何 `--deselect` / `-k` / 路径限定**，失败集合相对 `5a0320f` 基线（20 项）**零新增**
- **RT-NETWORK**：显式执行 `-m network` 的真实网络用例，单独报告 成功 / 失败 / 外部不可用

外加定向：`tests/test_historical_cases.py`、离线护栏专项、`api_smoke + fund_flow` 合跑。

### 放行条件

独立审核员审**同一个最终整合 SHA**，明确给出「准予合入」或「打回」。
**部署是独立授权 + 独立验证步骤，不由代码 PASS 自动推导。**

## 8. 主干 20 项既有失败（基线，非新增）

```
test_cninfo_disclosure_metadata(1) / test_dav27_report_semantics(2) / test_debate_state_persistence(5)
test_game_theory_integration(1) / test_h1b_gates(1) / test_provider_date_guards(1)
test_recalculate_weekly_metrics(1) / test_signal_processing(3) / test_social_data_api(1)
test_two_stage_analyst_topology(4)
```
