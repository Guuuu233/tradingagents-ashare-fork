## 🔴 阻断 3：硬化存在未覆盖的第二入口 —— RT-NETWORK 刚在它上面活锁

**本条是返修派工。** 加严 1 / 加严 2 的要求不变，本条为新增。

### 现场（实测，非推断）

DAV-1003 的 RT-NETWORK 门禁刚刚卡死 10 分钟：

```
PID 8009  %CPU=99.8  存活 10:08  忙循环
  └─ TCP 198.18.0.1:58693->198.18.0.112:10030 (CLOSE_WAIT)
  └─ cwd: .../dav-1003-71c87704e236/workdir/tradingagents-ashare-fork
  └─ 命令: pytest -m network -v
```

`10030` 即 `BAOSTOCK_SERVER_PORT`（`baostock/common/contants.py:12`）。CLOSE_WAIT + 99.8% CPU = **本卡声称已修复的那个 EOF 忙循环**，确诊形态。已按 PID 精确 `kill -9 8009/8008`，未动任何其他进程。

### 根因

`ensure_baostock_socket_hardening()` 只在 `cn_baostock_provider.py` 的 `_bs()` / `_session()` 里调用（`:171` / `:192`）。但生产代码存在**第二个 baostock 入口，完全绕开它**：

```
tradingagents/eval/v03_return_measure.py:1148   import baostock as bs
tradingagents/eval/v03_return_measure.py:1202   import baostock as bs
```

卡住的用例 `tests/test_v03_return_measure.py::test_p0_real_provider_verifiable_metadata` 用的正是该模块的 `VendorPriceDataProvider`。**走这条路，baostock 从未被硬化**——原版 `send_msg` 无 EOF 检查、无超时，对端一关就 100% CPU 空转。

顺带澄清一个已排除的怀疑：baostock 内部调用方写的是 `import baostock.util.socketutil as sock` 后 `sock.send_msg(...)`，属**调用时的模块属性查找**，所以 monkeypatch `bssock.send_msg` 对它们是生效的。**patch 策略本身没问题，问题是 patch 根本没被安装。**

### 重要推论：RT-NETWORK 绿过一次，不能证明洞已堵

该缺陷需要**对端恰好关闭连接**才触发，是间歇性的。上一轮 `896eaca3` 报「RT-NETWORK: 5 passed」与本轮活锁**并不矛盾**——两者都是真实的，只是命中与否取决于时机。因此这一门禁的绿色**不构成**「第二入口已覆盖」的证据，必须靠结构性保证。

### 返修要求

1. **硬化必须对所有 baostock 使用点生效，不能是 per-call-site。** 建议收敛为唯一的 baostock 访问入口（如 `get_hardened_baostock()`），`v03_return_measure.py` 两处改为走它；fail-closed 语义同样适用于该入口。
2. **补一条静态守卫测试**：断言除该访问器外，`tradingagents/` 与 `api/` 下不存在任何直接 `import baostock`（正则扫源码即可）。这条是防止将来再开新洞的唯一结构性手段——因为运行时门禁是间歇性的，靠不住。
3. **重跑 RT-NETWORK 到自然结束**。若再次活锁，按 `work/check-stuck-tests.sh` 取证（PID / `%CPU` / `CLOSE_WAIT`）后中止并如实上报，**不要干等、不要用 `--deselect` 绕过**。

### 其余不变

加严 1（同一显式 loop、五步两断点、③⑤ 各加 `is prior_executor` 与 `_shutdown is False`）、加严 2（真实 provider 链路、禁 `side_effect=` 注入、`bs.login()` 零调用、refusal 非终态且 `total_scanned > 0`）。

新 SHA 重跑 RT-FULL-OFFLINE + RT-NETWORK，走新开复审卡；主线 `5a0320f` 继续 HOLD。

（已同步 steer 至运行中的 run `01a0aa41`，送达成功；此评论为双发留档。）
