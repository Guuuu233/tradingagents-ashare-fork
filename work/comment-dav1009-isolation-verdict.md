## 603a92e 系统层离线隔离补证 —— 判定（数据驱动，非估计）

**口令 `GO_ISOLATION_EVIDENCE` 已执行完毕。** 同 SHA、零代码改动、无新 SHA。

---

### 一、运行参数

```
SHA      603a92ec85a43ce03ae899cc87a1559fa45f7016（未变）
工作树    HEAD=603a92e · status 0 项 · 远端 refs/heads/agent/1/a563fbe02617-b2 回读同一 SHA
命令      pytest -q -o faulthandler_timeout=120
         无额外 -k/路径/deselect；项目 addopts 自带 -m 'not network'（4 项 deselected 为配置过滤，非全覆盖）
环境      无 PYTHONPATH · 无代理 · 无 TA_OFFLINE_TESTING → 候选自身审计 hook 是唯一防线
采样      lsof 时间序列，间隔 2s，覆盖 pytest 主进程 PID 74003 + 全部后代（74000/74002）
```

### 二、三类独立证据（各自 SHA256）

| 证据 | 文件 | 大小 | SHA256 前 32 |
|---|---|---|---|
| 全量日志 | `/tmp/dav1009_rtfull_iso_603a92e.log` | 59,464B | `2be76bbe1478630ae1015b3818851f85` |
| **系统层连接采样** | `/tmp/dav1009_isolation_lsof.log` | 14,825B | `c88f3295729e367b371a85c35be9861a` |
| 护栏 deny 台账 | `/private/tmp/rt_deny_20260917_140909_74003_c83e2a.log` | 584,829B | `65d77b1031303c34336d83972b4b73b5` |
| 护栏 observe 台账 | `/private/tmp/rt_observe_20260917_140909_74003_c83e2a.log` | 586,965B | `d019a47a2ae9af3566c0b3a16b573981` |

### 三、全量结果

```
20 failed, 4851 passed, 1 skipped, 4 deselected, 182 warnings, 3 subtests passed
耗时 379.35s（6 分 19 秒）· rc=1 · 自然结束
```

**20 个失败文件与基线 `5a0320f` 逐项完全一致，零新增回归。**

### 四、护栏 deny 台账全量计数（直接解析日志，非 pytest 警告数）

```
总记录 2359 条 - sentinel 正控 5 条 = 2354 条真实拒绝
├─ socket.getaddrinfo    2010   (DNS)
├─ socket.connect         211   (TCP)
├─ curl_cffi.perform      136
├─ socket.sendto            1
└─ socket.gethostbyname     1

observe 台账 2372 条（observe ≥ deny，含 5 条 guard.curl_cffi 正控 + 3 条未升级为 deny 的观察）
deny 目标 top：push2.eastmoney(532) hq.sinajs(525) push2his.eastmoney(430)
              public-api.baostock:10030(206) ifzq.gtimg(201) finance.sina(201) ...
```

> 上次「1 条」是 pytest 主日志里显露的 warning 计数——本次直接解析 deny 台账得出真实全量 **2354**，更正口径。

### 五、系统层采样（核心补证）

```
采样点数        176（14:09:16 → 14:15:31，覆盖全量全程）
pytest 树非本地 ESTABLISHED 捕获   0 条（EXT-PID 记录 0）
pytest_ext_conns>0 的采样点         0 个
环境基线（系统其余进程）           28–50 条波动 → 采样器工作正常，能区分噪声
```

**判定措辞（边界化）**：**持续采样期间（176 点 × 2s，全量全程）未观察到 pytest 主进程及其全部后代 PID 的成功非本地连接。** 不宣称绝对零外连——采样有 2s 间隙，瞬态连接理论上可落在间隙；但护栏在 Python 层 deny 了全部 2354 次外连尝试，且 `socket.getaddrinfo`/`connect`/`curl_cffi`/`sendto`/`gethostbyname` 五类入口全被拦截，无漏网通道证据。

### 六、判定

```
✅ 代码回归门禁      PASS（20 失败全为基线，零新增）
✅ 完整回归门禁      PASS（自然结束，无卡死）
✅ 系统层离线隔离门禁  PASS（deny 2354 全拦截 + 采样期间未观察到成功非本地连接）
   ——此前「证据未闭环」的缺口（未知 C 扩展/子进程路径）已由 lsof 进程树采样补上
```

### 七、仍受约束

- 本结果**不构成合入授权**、**不构成部署授权**
- 主线 `5a0320f` 继续 HOLD · 生产库/服务未动
- 采样结论为「期间未观察到」，非绝对证明；候选合并后运行态仍需真实业务 smoke
- Phase 3 工具 / 卡 C / DAV-998 重放 均未启动
