# 唯一整合候选：从 5a0320f 收口 DAV-989/995/996/998

## 背景

终版审计裁定：现有 4 个候选**父提交全部是 `5a0320f`，是平行分叉不是链**，且存在真实合并冲突，**不得依次快进**。必须形成唯一整合候选。

完整裁定见仓库内 `work/2026-09-16-final-audit-and-integration-plan.md`（**开工前必读**）。

## 输入候选（全部 parent = `5a0320f0618d203e95e7197e08b110c7850078d4`）

| 卡 | SHA | 改动文件 |
|---|---|---|
| DAV-998 | `4efe4163bf848f54039424e31e0bb18eaf9be203` | `tests/test_historical_cases.py`, `tradingagents/knowledge/historical_cases.py` |
| DAV-995 | `85db74d1c8fe724a11b62ba40827a5e34d32689c` | `tests/conftest.py`, `tests/test_job_lifecycle.py`, `tests/test_offline_network_guardrail.py`, `tests/test_v03_return_measure.py`, `tradingagents/dataflows/providers/cn_baostock_provider.py` |
| DAV-996 | `c8038980956b3fd0516e057b21f39b1f9ced8fe6` | `api/main.py`, `conftest.py`, `tests/test_api_smoke.py`, `tradingagents/dataflows/providers/cn_baostock_provider.py` |
| DAV-989 | `4b0c9eeb` | `api/main.py`, `tests/test_api_smoke.py` |

冲突矩阵：`cn_baostock_provider.py` = 995∩996；`api/main.py` 与 `tests/test_api_smoke.py` = 989∩996。

## 任务

从 `5a0320f` 新建分支，**人工择优整合，禁止整提交盲目 cherry-pick 叠加**。

### A. 纳入（基本照收）

1. DAV-998 的 T+1 refusal 返修全部内容。
2. DAV-995 的离线网络护栏（`tests/conftest.py`、`tests/test_offline_network_guardrail.py`）与 baostock EOF/超时硬化。

### B. 必须修掉的阻断项（在整合候选中一并完成）

**B1. `cn_baostock_provider.py` 的 3 处真静默**（违反 `AGENTS.md:115-116`）

最严重的是包住整个 `ensure_baostock_socket_hardening()` 的那处：

```python
        bssock.SocketUtil.connect = safe_connect
        bssock.send_msg = safe_send_msg
        _BAOSTOCK_HARDENED = True
    except Exception:
        pass          # ← baostock 内部 API 一变，硬化静默失效，退回原版活锁实现
```

后果：无日志、无上报、`_BAOSTOCK_HARDENED` 保持 False，**本卡要修的活锁静默复活**。
要求：收窄为 `except (ImportError, AttributeError) as e:`，`logger.warning` 记录，并暴露可查询的硬化状态位（供测试断言「硬化确实生效」）。

另两处：`bs.logout()` 的 `except Exception: pass`、context 清理的双层 `except Exception: pass`。同样收窄 + 记日志。

**B2. 6 处 cleanup-then-reraise 的内层捕获**（`safe_connect` / `safe_send_msg` 中包 `sock.close()` 的）

这些结尾有 `raise`，**不是静默失败**，等级低；但内层包 `close()` 的 `except Exception: pass` 应收窄为 `except OSError`。

**B3. 删除进程级全局超时污染**

`_session()` 中：

```python
orig_default_timeout = socket.getdefaulttimeout()
try:
    socket.setdefaulttimeout(timeout)      # ← 进程级全局
    lg = bs.login()
finally:
    socket.setdefaulttimeout(orig_default_timeout)
```

`socket.setdefaulttimeout` 是进程级全局可变状态。多线程运行时下：该窗口内其他线程新建的 socket 会继承此超时；两线程并发进出时 `finally` 的恢复会互相覆盖，可能把超时永久留在全局。
**这与 DAV-989/996 正在修的 lifespan 全局状态泄漏是同一类 bug。**
硬化后的 `safe_connect` 已做 per-socket `settimeout(timeout)`，全局设置很可能冗余。
要求：删掉全局 set/restore，只依赖 per-socket；若 login 路径确需超时，走 baostock 硬化入口实现。

**B4. baostock 清理收敛为唯一一份实现**

DAV-995 与 DAV-996 各写了一份 context 清理，必须合成一份：保留 995 的 EOF 硬化 + 996 的 `SocketUtil.instance = None`。**不得两套并存。**

**B5. 从 DAV-989 / DAV-996 择优取值**（人工解冲突，不盲目叠加）

- lifespan 恢复全局 socket timeout、executor 重入
- `api/main.py` 的状态复位**必须 `try/finally`**（现版在裸 `yield` 之后，运行/关闭阶段抛异常时复位被跳过）
- 阻止 `api_smoke` 穿透真实历史行情计算
- `test_api_smoke.py` 的清理**不得再删整张表**，改为事务回滚 / 独立库 / 按本用例创建的 ID 精确删除

**B6. 清掉** `tests/test_historical_cases.py:2055: new blank line at EOF`，保证 `git diff --check` 全干净。

### C. 定位口径更正（写进 commit message / 交付报告）

**DAV-996 不得描述为「根治 baostock EOF 活锁」。** 其清理逻辑在 `login`/查询/`logout` **返回之后**执行；若调用已进入 EOF 无限循环，控制流永远到不了清理代码。它治的是**残留污染**，活锁只能由 DAV-995 循环内部的 `if not recv: raise` 治。

### D. 默认值需显式记录

`DEFAULT_BAOSTOCK_SOCKET_TIMEOUT = 5.0`（per-recv）在拥塞链路 + 大区间历史查询下有误杀风险。已支持 `BAOSTOCK_SOCKET_TIMEOUT` 覆盖。请在交付报告中写明这是**有意识的默认值选择**及理由。

## 验收门禁（在最终整合 SHA 上跑，缺一不可）

1. **RT-FULL-OFFLINE**：默认离线全量，**不得带任何 `--deselect` / `-k` / 路径限定**。判据：失败集合相对 `5a0320f` 基线 20 项**零新增**（基线见裁定文档 §8）。
2. **RT-NETWORK**：显式跑 `-m network`，单独报告 成功 / 失败 / 外部不可用。
3. 定向：`tests/test_historical_cases.py`、`tests/test_offline_network_guardrail.py`、`api_smoke + fund_flow` 合跑。
4. `git diff --check` 输出为空。
5. 生产隔离证明：隔离环境导入生产代码后 `socket.socket.connect/send/recv` 仍为原生。
6. **新增**：断言 baostock 硬化**确实生效**的测试（对应 B1 的状态位）——不能出现「硬化静默失效但测试仍绿」。

## 硬约束

- 解释器 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（3.10.20），必须 `env -u PYTHONPATH`，报告中附版本输出
- 全量跑**不设低于 45 分钟的看门狗**；命令超时无输出立即 `bash work/check-stuck-tests.sh` 判别，两种形态（%CPU≈100% 忙循环 / %CPU≈0% 阻塞）都不自愈，**CLOSE_WAIT 是确诊依据**
- 清理卡死进程按 PID 精确 kill，**严禁宽泛 `pkill -f pytest`**（会误杀他人门禁跑）
- 禁止：合入主线、部署、重启线上服务、写生产库、历史重写
- 写审分离：本卡只交付候选 SHA，由独立审核员审同一 SHA
- 交付报告中的基线对照**必须写直接父 `5a0320f`**，不得写成 `28d1adc`（上一轮的引用不规范问题）
