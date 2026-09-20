# DAV-119 微任务：累计窗口完整性校验

## 精确基线

远端 target 主干：`codex/dav-4-p2a-trunk`

SHA：`f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`

只读该精确 SHA；不要读取父任务历史，不复用旧 run，不改配置、provider、模型绑定、API Key、主干或凭据。

## 唯一问题

`tradingagents/dataflows/fund_flow_evidence.py:summarize_evidence` 当前只按记录日期排序并计算窗口，未在累计前统一拒绝：

1. 全量重复日期；
2. 缺失交易日/非交易日断档；
3. `period_kind`、`window`/`time_window`、`raw_unit` 不一致或不是逐日 1d；
4. 记录数等于 5 但实际日期不构成完整连续窗口。

## 只做此微任务

只允许修改：

- `tradingagents/dataflows/fund_flow_evidence.py`
- `tests/test_fund_flow_evidence.py`

实现要求：

- 在任何 `netamount`/`r0_net` 求和前，先做日期去重检查、日期有效性检查、逐日 period/window/unit 一致性检查；
- 发现全量重复、缺日、非交易日断档或口径不一致时返回现有 `data_conflict`/`partial` 语义，禁止静默累计；
- 不要凭周末/节假日硬编码推断交易日；优先使用仓库已有交易日历 helper，若当前模块没有安全依赖，则至少拒绝重复/日期不连续并把“需要交易日历核验”作为结构化 reason；禁止向未来补日期；
- 保留现有来源族隔离、算法组隔离、单位换算、字段资格与 legacy Web 语义；不修改 provider、collector、analyst、manual calibration ledger；
- 测试至少覆盖：全量重复日期、缺失日期/断档、period/window/raw_unit mismatch、正常 5 个交易日窗口仍正确求和。

## 验收

必须从上述 SHA 产生并推送新远端 branch/SHA，报告：

- 实际改动文件；
- `.venv310` 定向测试精确结果；
- changed module compileall；
- git diff --check；
- 结构化 conflict/partial 输出中的 reason、dates、required_window_days，证明没有静默求和。

若再次 context-window 400，停止重放该父任务；此任务仅修窗口，不扩大到 manual_calibration_gap 或其他文件。新 SHA 经远端核验、只读审核和回归前，不合入、不重启、不解锁后续。

注意：`manual_calibration_gap ledger/provenance` 必须另开独立微任务，不在本任务中处理。
 in_progress 后立即核验是否出现新 run；无 run 时点名唤醒。
方法参考：`summarize_evidence` 及 `tests/test_fund_flow_evidence.py`。
