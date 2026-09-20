## 补充：污染证据与验收口径（原 DAV-997 已作废，内容转入本卡）

### 现有证据（迹象充分，但**尚未定性**）

| 跑法 | 实测 |
|---|---|
| 单跑 `test_single_horizon_report_persists_and_reads_all_scale_fields` | `1 passed in 1.58s` |
| 单跑 `tests/test_api_smoke.py` | `56 passed in 29.53s` |
| 两者合跑 | `67 passed in 1305.87s` |

同一条用例单独跑 1.58 秒，跟在 `test_api_smoke.py` 后面就要走真实 vendor 链并反复 60s 超时。此外 delta debugging（在 `b95a9b88` 上，增量删除 + 补集法）显示：**移除 `tests/test_api_smoke.py` 后慢速现象消失**，其余文件呈累积效应而非单一搭档。

⚠️ **重要限定**：以上只能证明 `test_api_smoke.py` 是必要条件，**尚未证明**具体是哪个全局状态（registry / 缓存 / 配置 / env / 连接池）被它改变。本卡的第一项产出应当是**定位并证明**该状态，而不是直接改。请先给出可复现的最小证据（例如在 `api_smoke` 前后打印/对比该全局对象，证明其值发生变化且该变化导致路由回落到真实 vendor），再动手复位。

可疑面（仅为线索，不是结论）：`api.main.lifespan` 在测试中被执行，其中包含 `init_db()`、`_load_cn_trade_dates()`、`_load_cn_stock_map()`、`get_job_store().clear()`、构建 provider registry 等全局副作用。

### 验收口径

- **AC-1**：给出「哪个全局状态被污染」的直接证据（前后对比 + 该状态如何改变 `route_to_vendor` 的选路）。
- **AC-2**：复位后，`api_smoke + fund_flow_scale_consumption` 两文件合跑耗时从 `1305.87s` 显著下降，且仍为 `67 passed`。
- **AC-3**：完整 RT-FULL 失败集合相对基线**零新增**。基线（主线 `28d1adc6`）：`20 failed, 4777 passed, 1 skipped, 3 deselected in 1700.10s`，20 项清单见 DAV-979 的「主干首次取得完整 RT-FULL 基线」评论。
  - 其中 `tests/test_h1b_gates.py`、`tests/test_recalculate_weekly_metrics.py` 两项**只在全量上下文失败、单文件跑不失败**，很可能与本卡要查的污染同源，若能一并解决请说明；不能解决也须在报告中说明结论。
- **AC-4**：复位机制不得影响生产运行，须单独说明作用域。

### 与相邻卡的边界

- **DAV-995**：负责测试会话离线网络拦截与 socket 隔离（含 baostock 裸 TCP 超时）。
- **DAV-989 / DAV-991**：负责 executor、TestClient、后台线程收尾。
- **本卡**：只负责定位并复位 `test_api_smoke.py` 的全局状态污染。
- **DAV-992**：已 cancelled，不再施工。

三者独立但相关：即使 DAV-995 的离线护栏落地让套件变快，本卡的污染问题依然需要单独解决——否则测试仍在走错误的选路路径，只是失败得更快。

### 测试方法注意

⚠️ 跑全量不要设短看门狗（基线 28 分钟），此前多次「挂死」判定均为看门狗误判。⚠️ 不要用宽泛的 `pkill -f pytest`，会误杀他人正在进行的门禁跑。
