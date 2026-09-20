## 补充实证：确实存在与「外网慢」无关的资源收尾问题，本卡范围成立

此前我在 DAV-979 提出「套件不是死锁、只是被真实外网拖慢」。该结论对**耗时构成**成立，但**不能推广**为「不存在挂死或泄漏」。以下是刚刚捕获的反证，支持本卡继续推进。

### 遗留进程实证

DAV-992 的运行被取消后，其子进程并未随之结束：

```
PID   12371
命令  .venv310/bin/python -m pytest -q -p no:randomly tests/test_api_smoke.py
cwd   .../multica_workspaces_steer/.../dav-992-b438f9a3d7da/workdir/tradingagents-ashare-fork
存活  04:28:38
CPU   99.5%（持续）
线程  2
```

关键对比：**`tests/test_api_smoke.py` 单独跑的正常结果是 `56 passed in 29.53s`**，而该进程在同一文件上空转了 4 小时 28 分钟且持续吃满一个核。

`sample` 采样显示热点全部落在 `_PyEval_EvalFrameDefault` / `_PyFunction_Vectorcall` 等纯字节码执行帧，无系统调用等待——即**纯 Python 层的忙等/死循环**，与 baostock 的阻塞式 socket 等待（`%CPU=0`、`STAT=S`）是**完全不同的两种形态**。

该进程已由运维清理（先 `SIGINT` 后确认退出），期间一直在与其他门禁跑抢 CPU。

### 结论与本卡关系

- 「外网拖慢」与「进程/线程收不干净」是**两个独立问题**，此前我把它们混为一谈，这里更正。
- 本卡候选 `b5f63d4` 修的 `new_default_executor` 未等待回收、`TestClient` 未关闭，属于**真实存在**的资源收尾缺陷，**本卡范围成立，应继续推进**。
- 但仍请注意验收口径：**不得**以「复现命令能跑完」或「RT-FULL 挂死已解决」作为本卡的通过条件——实测 `b5f63d4` 并不改变套件的慢速表现（其根因在 DAV-995/996）。本卡的通过条件应聚焦于：lifespan 退出后线程/客户端确实被回收、取消运行后不留残余进程、且相对基线零新增失败。
- 建议增加一条验收：**取消/中断测试运行后，不应残留持续占用 CPU 的子进程**。上述 12371 即为该缺陷的现场证据。

### 基线数据（供对照使用）

主线 `28d1adc6` 完整 RT-FULL：`20 failed, 4777 passed, 1 skipped, 3 deselected in 1700.10s (28:20)`，20 项失败清单见 DAV-979 基线评论。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
