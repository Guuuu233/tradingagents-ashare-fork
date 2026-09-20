测试套件当前会向真实行情供应商发起外网请求，导致 RT-FULL 单轮 28 分钟、结果依赖网络状况，且单元测试触达真实 vendor 本身不可接受。本卡负责建立离线护栏并消除阻塞式外网调用。

**开工前必读**：DAV-979 的两条运维评论（「重大更正：根本不是死锁」与「主干首次取得完整 RT-FULL 基线」），其中包含完整证据链与基线数据。**不要**沿用更早那些已被撤销的结论（rt7 挂死、全局 executor 关闭）。

## 已确认的事实

1. 套件**不死锁**，能跑完：主线 `28d1adc6` 实测 `20 failed, 4777 passed, 1 skipped, 3 deselected in 1700.10s (28:20)`。
2. 耗时几乎全部来自真实网络超时。`--durations` 前 7 名同属 `tests/test_fund_flow_scale_consumption.py`，每条 **≈181.3s ≈ 60s × 3**，对应 `DEFAULT_PROVIDER_RESOURCE_POLICY.timeout_seconds=60.0` 在 vendor 链上的重试。
3. faulthandler 栈实证调用链：
   - 主线程 `tests/.../test_single_horizon_...` → `report_service.create_report` → `historical_cases.record_historical_case` → `calculate_t1_return` → `interface.py:440 route_to_vendor` → `future.result(timeout=60)`
   - 工作线程 `interface.py:366 <lambda>` → `cn_baostock_provider.get_stock_data` → `_fetch_hist_df` → `_session()`（`bs.login()`）→ `baostock/util/socketutil.py:65 send_msg`（**真实 TCP 发包**）
4. `http_proxy` / `https_proxy` **拦不住 baostock**：它走裸 TCP，不读代理环境变量。
5. 存在测试间污染：`test_single_horizon_report_persists_and_reads_all_scale_fields` 单独跑 `1 passed in 1.58s`；`tests/test_api_smoke.py` 单独跑 `56 passed in 29.53s`；两者合跑 `67 passed in 1305.87s`。说明 `test_api_smoke.py` 改变了进程内 provider 路由/缓存状态，使后续用例回落到真实 vendor 链。

## 目标（按优先级）

### P0 离线护栏

测试会话默认**禁止对外网络连接**，仅放行 localhost。需要真实联网的用例必须显式标记（项目已有 `pyproject.toml:74 addopts = "-m 'not network'"` 的 network 标记机制，请复用而非另造）。

实现方式自选（conftest 层 autouse fixture 在 socket 层拦截，或统一以 fake provider 注入 registry），但必须满足：
- 被拦截时抛出**清晰可识别**的错误，能一眼看出是哪个用例、试图连哪个地址；
- **不得**把网络失败静默转成空数据或 0 值（数据失败必须显式上报，这是项目红线）；
- 不得影响 `-m network` 标记用例的真实联网能力。

### P1 baostock socket 超时

`tradingagents/dataflows/providers/cn_baostock_provider.py` 的 `_session()` / `bs.login()` 路径必须有 socket 级超时，无网络时快速失败而非阻塞到上层 60s 超时。

### P2 定位并消除 `test_api_smoke.py` 的状态污染

找出它留下的哪个全局状态（registry / 缓存 / 配置 / env）导致后续用例走真实 vendor 链，并在用例间复位。这是让套件回到「秒级」的关键。

## 验收标准（逐条给出实际输出）

- **AC-1**：`tests/test_api_smoke.py tests/test_fund_flow_scale_consumption.py` 两文件合跑，从 **1305.87s** 显著下降（目标 < 120s），且仍为 `67 passed`。
- **AC-2**：完整 RT-FULL 跑完，总耗时较基线 **1700.10s** 显著下降，并给出 `--durations=20`。
- **AC-3**：**失败集合零新增**。基线为 20 项 / 10 个文件，清单见 DAV-979 基线评论，请逐项对照并贴出差异（预期为空）。
  - 注：`test_h1b_gates.py`、`test_recalculate_weekly_metrics.py` 这两项只在全量上下文中失败、单文件不失败，属既有污染，**不要求本卡修复**，但不得因本卡改动而变多。
- **AC-4**：新增测试证明护栏生效——存在一个用例，它在未标记 network 的情况下尝试外连会被明确拒绝。
- **AC-5**：说明改动对**生产运行**的影响。护栏必须只在测试会话生效，**绝不能**影响真实服务访问供应商的能力。这一条必须单独回答，不得省略。

## 白名单

预期涉及：`tests/conftest.py`（或新增测试支持模块）、`tradingagents/dataflows/providers/cn_baostock_provider.py`、必要的测试文件。若需改 `tradingagents/dataflows/interface.py` 或 `pyproject.toml`，请先在卡内说明理由再动手。

**禁止**为了让测试变快而放宽或删除既有断言、降低数据校验强度、或把 vendor 拒绝改成返回空值/0。

## 环境

解释器 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，配 `env -u PYTHONPATH`，报告贴 `-V`（须 `Python 3.10.20`）。`DATABASE_URL` 指向隔离临时库，禁止写 `data/tradingagents.db`。

⚠️ 跑全量时**不要设短看门狗**：套件本来就要 28 分钟，此前多次「挂死」误判都源于此。若必须设上限，不得低于 45 分钟，超时须记录 `--durations` 与 `PYTHONFAULTHANDLER=1` + `kill -ABRT <pid>` 的栈，不得直接判为死锁。

## 交付

完整 40 位候选 SHA、直接父（须等于交付当时的 `origin/codex/dav-4-p2a-trunk` tip，提交前 `git ls-remote` 复核）、精确远端 ref、白名单清单、`git diff --check`、clean 工作树。不合入、不部署、不重启、不写生产库。写审分离，复审另派代码审核员。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
