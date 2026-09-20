## 补充：验收基线数据与两条硬约束（原 DAV-997 已作废，内容转入本卡）

### 可直接用作验收的实测数据

主线 `28d1adc6` 完整 RT-FULL（`.venv310`，`-q -p no:randomly`，隔离 `DATABASE_URL`，**不设看门狗**）：

```
20 failed, 4777 passed, 1 skipped, 3 deselected, 182 warnings, 3 subtests passed in 1700.10s (0:28:20)
```

耗时构成（`--durations`）：前 7 名全部来自 `tests/test_fund_flow_scale_consumption.py`，每条 **≈181.3s ≈ 60s × 3**，合计约 21 分钟，即整轮的绝大部分。`181.3` 对应 `DEFAULT_PROVIDER_RESOURCE_POLICY.timeout_seconds=60.0`（`providers/base.py:24`）在 vendor 链上的重试叠加。

局部对照（可作为最快的验收指标）：

| 跑法 | 实测 |
|---|---|
| 单跑 `test_single_horizon_report_persists_and_reads_all_scale_fields` | `1 passed in 1.58s` |
| 单跑 `tests/test_api_smoke.py` | `56 passed in 29.53s` |
| 两文件合跑 `api_smoke + fund_flow_scale_consumption` | `67 passed in 1305.87s` |

**建议 AC**：两文件合跑从 `1305.87s` 降至 **< 120s**，且仍为 `67 passed`；完整 RT-FULL 总耗时较 `1700.10s` 显著下降；失败集合相对基线 **零新增**（基线 20 项清单见本卡父卡 DAV-979 的「主干首次取得完整 RT-FULL 基线」评论）。

### 硬约束一：护栏只能作用于测试会话

**绝不能影响生产服务访问真实供应商的能力。** 请在交付报告中单独回答这一条，说明护栏的作用域如何限定（仅 pytest 会话 / conftest 层），以及如何证明生产路径不受影响。这一条不得省略。

### 硬约束二：不得把网络失败伪装成数据

被护栏拦截时必须**显式失败并给出清晰错误**（能看出是哪个用例、试图连哪个地址）。**禁止**把网络失败静默转成空数据、0 值或「无数据」，这是项目红线（数据失败必须显式上报）。同样禁止为了让测试变快而放宽既有断言或降低数据校验强度。

需要真实联网的用例请复用项目已有机制：`pyproject.toml:74` 的 `addopts = "-m 'not network'"` 与 `network` 标记，不要另造一套。

### baostock 具体证据（socket 隔离部分）

`http_proxy` / `https_proxy` **拦不住 baostock**——它走裸 TCP，不读代理环境变量。faulthandler 实证栈：

```
baostock/util/socketutil.py:65 in send_msg          ← 真实 TCP 发包，阻塞
cn_baostock_provider.py:66 in _session              （bs.login()）
cn_baostock_provider.py:87 in _fetch_hist_df
cn_baostock_provider.py:126 in get_stock_data
tradingagents/dataflows/interface.py:366 in <lambda>
```

建议一并为该 provider 增加 socket 级超时，使无网络时快速失败，而不是阻塞到上层 60s 超时再重试。

### 测试方法注意

⚠️ 跑全量**不要设短看门狗**（套件基线即 28 分钟）。此前多次「挂死」判定都是看门狗误判。若必须设上限，不低于 45 分钟；超时须记录 `--durations` 与 `PYTHONFAULTHANDLER=1` + `kill -ABRT <pid>` 的栈，不得直接判为死锁。

⚠️ **不要使用宽泛的 `pkill -f pytest`**：会误杀他人正在进行的门禁全量跑（本轮已发生过一次，候选侧全量被静默中断）。清理请按 PID 精确处理。
