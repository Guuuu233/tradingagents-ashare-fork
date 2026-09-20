## DAV-946 R3 返修指令（上一运行已被运维取消，原因见下）

### 为什么取消了上一个运行

上一个开发运行（`01a0a416-8f68-7cad-a206-d19cb7f1eb85`，08:02 起）空转 2 小时 25 分，没有产出候选。实测原因：

- 它执行的是 `uv run --python 3.10 --frozen pytest -q`，**缺 `-p no:randomly`、缺 deadlock deselect、且用的是任务工作区自带 `.venv` 而非项目 `.venv310`**。
- 该 pytest 进程（PID 97239）跑到 **96% 后停止输出 51 分钟**，`%CPU≈171`、15 个线程仍在跑——属高 CPU 空转，与 DAV-979 记录的 38% 处 `%CPU=0` 死锁**不是同一种**。
- 该进程及其父进程已由运维清理，未影响仓库与生产库。

两类病灶都会让全量回归无法收敛，因此**全量命令必须按下面固定写法执行**，不要用裸 `pytest -q`。

### 本轮必须修复（来自 DAV-980 同 SHA 复审的两个 🔴）

1. **版本声明不一致（阻塞项）**：交付评论写的是 `origin/agent/support/dav946-on-b95-r2`，而远端实际存在的是 `origin/agent/support/dav946-on-b95a-r2`（少一个 `a`）。候选 SHA `d9aedbff139cf5f97ac2cba940ca5a79d1856632` 本身**确实在远端**，运维已用 `git ls-remote` 核实。修法二选一：交付评论写精确的现有 ref，或把候选推到声明的那个 ref。**不要因为这条重写代码。**
2. **`y_finance.py:317-318` 等 raw wrapper 把历史拒绝返回成普通字符串**，未统一为 `VendorRefuse`。请与 Alpha Vantage 侧保持同一返回契约，并补对应断言。

### 已由运维独立验证、不需要重做的部分

在 `d9aedbff` 的只读检出、`.venv310`（Python 3.10.20）下实测：

- 历史日 `2024-01-02` 调用 `alpha_vantage_fundamentals` 的四个原始函数 → **底层 `_make_api_request` 零调用**，全部返回 `VendorRefuse`；
- 当前日 / `curr_date=None` → 正常调用 `OVERVIEW` 返回 dict，未误伤；
- `tests/test_historical_yfinance_pit.py` → **40 passed**。

即 DAV-980 指出的 Alpha Vantage 绕过路径已经堵上，R3 只需处理上面两条。

### 固定全量命令（必须照此执行并贴精确数字）

```
cd <候选只读检出目录>
env -u PYTHONPATH -u all_proxy -u ALL_PROXY PYTHONPATH="$PWD" \
  DATABASE_URL="sqlite:////private/tmp/dav946-r3-iso.db" \
  http_proxy="http://127.0.0.1:9" https_proxy="http://127.0.0.1:9" \
  no_proxy="127.0.0.1,localhost" NO_PROXY="127.0.0.1,localhost" \
  /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly \
  --deselect "tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields"
```

- 必须用上面这个**绝对路径解释器**并贴 `-V` 输出（须为 `Python 3.10.20`）；不得用任务工作区 `.venv`、系统 `python3` 或 `python3.14`（依赖集不同：pandas 3.0.5 对 2.3.0，dtype `object→str`、copy-on-write 语义均不同）。
- `-p no:randomly` 不可省；`DATABASE_URL` 必须指向隔离临时库，**禁止写 `data/tradingagents.db`**。
- 若全量在某处停住超过 10 分钟：**不要反复重跑**。记录进度百分比、`ps -o %cpu,etime` 与 `/usr/bin/sample <pid> 5 -mayDie` 的栈，作为 DAV-979 的补充证据回报，然后按定向测试 + 说明限制的方式交付。

### 交付格式

完整 40 位候选 SHA、直接父（须为交付时远端主线 tip）、**精确现有远端 ref**、白名单文件清单、`git diff --check`、clean 工作树、解释器 `-V`、各项精确测试数字。不得合入、部署、重启或写生产库。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
