# DAV-1050 候选 7a303474 同 SHA 只读代码复审

## 审查对象

- 实施卡：DAV-1050「V-03 --offline 真离线取价护栏与可复跑性」
- 候选完整 SHA：`7a3034741740aec83f70f0b9a4327fa87873006c`
- 直接父 SHA：`6ee148699339efefc2f7f7548eb286be485524e1`
- 远端分支：`origin/dav-1050-offline-guard`
- 运行服务/生产库：禁止触碰；本卡只读审查，不能部署、重启或写生产库

## 严格白名单

累计差异必须且只能包含：

1. `tradingagents/eval/v03_return_measure.py`
2. `scripts/run_v03_return_measure.py`
3. `tests/test_v03_offline_guard.py`

越出白名单、父提交不一致、候选分支未指向候选 SHA、工作树污染审查对象，直接报告阻断。

## 必查契约

1. `--offline` 不得调用 `route_to_vendor`、akshare、baostock、HTTP 或外部 socket；无快照时必须快速结束并返回 typed-missing/NULL，不能填 0、静默丢样本或假造收益。
2. 本地 JSON/CSV 快照必须显式传入；记录绝对路径与 SHA256；缺字段、非法日期、超过显式 `forward_oos_end_date`、冲突重复 `(symbol,date)` 必须 fail-closed；完全相同的重复行可确定性去重。
3. 快照价格只能来自本地数据；T+1/T+5 日期只能来自快照交易日集合，不得退回实时交易日历或供应商；PIT 上界必须生效。
4. 快照缺少标的/日期/元数据时必须保留 coverage 分母并输出可追溯 typed gap；不可把未知 ST/上市状态当作已验证事实。
5. 在线默认路径必须保持 `VendorPriceDataProvider`，`--price-snapshot` 没有同时指定 `--offline` 时必须拒绝；不能改变生产模式。
6. manifest、逐样本 provenance 和 25 字段审计输出中的 provider、snapshot 路径、SHA、缺口原因必须与实际路径一致；不能把 offline provider 或测试快照冒充线上服务/历史样本生成 SHA。
7. 所有新增测试必须是真实行为断言：删掉离线 provider 或绕回 vendor 时应失败；不能只断言状态位/日志而不证明调用路径；测试不得写生产库或调用真实模型/供应商。

## 必须核对的证据

- 固定解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，版本必须为 Python 3.10.20。
- 开发者定向测试：新增 `tests/test_v03_offline_guard.py` 精确计数；既有 `tests/test_v03_return_measure.py` 回归。
- 全量对照：候选 `4938 passed / 6 failed / 1 skipped / 6 deselected`，父版本 `4923 passed / 6 failed / 1 skipped / 6 deselected`；请核对失败集合逐项相同，且 `+15` 只能来自新增测试。
- 端到端离线 CLI：无快照立即结束并输出 typed-missing；带合法快照可完成评测并记录哈希；命令失败或超时须如实报告。
- `git diff --check 6ee148699339efefc2f7f7548eb286be485524e1..7a3034741740aec83f70f0b9a4327fa87873006c` 必须干净。

## 复审边界与交付

- 只读复审：不得修改代码、提交、合入、部署、重启、写生产库、使用 Cookie、改变 H1b 开关或修改个人模型/凭据配置。
- 复审结论必须包含：完整候选 SHA、直接父、远端分支回读、白名单、关键路径、实际测试计数、全量失败集合比较，以及 🔴/🟡/🟢 结论。
- PASS 只代表同 SHA 只读审查通过，不构成合入或部署授权。
- 复审任务固定派给 `代码审核员`；不要派给“独立代码审核员”，也不要 @ 项目调度助手，避免重复触发。
