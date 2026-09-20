## 修正：加严 1 的测试顺序有缺陷，以本条为准

**本条是返修派工，且覆盖上一条评论中加严 1 的执行顺序。** 加严 2 的要求不变。

### 上一条的顺序错在哪

我上一条写的是「失败 lifespan → 正常 lifespan → `run_in_executor(None, ...)`」。这个顺序**自己会把 bug 盖掉**：

正常 lifespan 启动时会先调用 `loop.set_default_executor(new_executor)`，**把失败路径遗留的坏引用直接覆盖**。于是最后那次 `run_in_executor` 用的是新挂上的健康 executor，即使失败路径压根没清理干净，测试也照样绿。等于让后一步替前一步自愈。

### 正确做法：同一显式 loop、两个独立断点

```
① 先让该 loop 建立起可用的默认 executor
     （例如先 run_in_executor(None, ...) 跑一次，触发 asyncio 惰性创建）
② 运行【失败】的 lifespan（注入启动期异常）
③ 【立即】run_in_executor(None, ...) 提交任务并取回结果
        ← 断点一：锁住【失败路径】的清理
④ 再运行【正常】lifespan，并正常退出
⑤ 退出后【再次】run_in_executor(None, ...) 提交并取回结果
        ← 断点二：锁住【正常路径】的清理
```

**③ 与 ⑤ 必须是两个独立断言，缺一不可。** ③ 的存在意义就是不让 ④ 有机会替 ② 擦屁股。

### 两个已实测的实现事实（`.venv310`, Python 3.10.20）

1. 向已 `shutdown()` 的 executor 提交任务，抛：

   ```
   RuntimeError: cannot schedule new futures after shutdown
   ```

   这就是 ③/⑤ 应当捕获的失败形态——测试写对了，缺陷就会以这个异常暴露。

2. `loop.set_default_executor(None)` 在 3.10 **是允许的**（仅 `DeprecationWarning`，不抛 `TypeError`），可作为复位手段。

### 加严 2 重申（不变）

- 必须由**真实 provider 路由**触发硬化失败并沿真实链路传播，**不得用 `side_effect=` 注入捏造异常**
- 用 spy 断言 `bs.login()` 调用数为 **0**（证明拒绝发生在登录之前）
- 最终验证 refusal **非终态**，且案例**仍留在回填队列**（`total_scanned > 0`）

### 其余不变

新 SHA 重跑 RT-FULL-OFFLINE（无任何 `--deselect`/`-k`/路径限定，相对 `5a0320f` 零新增）+ RT-NETWORK；`896eaca3` 与 DAV-1005 的 PASS 已失效，新 SHA 走新开复审卡；主线 `5a0320f` 继续 HOLD。

（本条已同步 steer 至运行中的 run `01a0aa41`，送达成功；此评论为双发留档。）
