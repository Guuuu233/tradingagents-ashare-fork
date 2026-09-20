# DAV-867：V-03a DAV-866 默认 provenance 返修红队复核

## 复核对象

只读复核候选：

```text
candidate:       020d3e3b18147f5ea20e90d3878b20952d20fd97
direct parent:   8c69eab186bde58e49cbc134fb4da5f015c77e92 (DAV-865)
trunk ancestor:  a227cdc3bb466edf2e910419cb6013cfc021d309
remote ref:      origin/agent/1/14ab0be94894
upstream card:   DAV-866
review type:     read-only red-team coverage; no repair, merge, FF, deploy, or restart
```

## 红队问题

1. `EvaluationStamp()`、`V03ReturnMeasureEngine().measure_dataset([])` 和 `SnapshotManifest` 的无参数路径是否全部输出同一明确 `offline_replay_gap`/typed gap，且绝不把历史样本生成 SHA `a6d4540feaa8043ff36b0607a31c1d2d5f004149` 冒充当前运行服务。
2. 历史样本生成 SHA 是否仍在命名正确的字段中保留；显式运行服务 SHA、healthz 可用和 healthz 不可用路径是否仍保持正确来源与报告回读。
3. `BASELINE_RUNNING_SERVICE_SHA` 兼容符号是否已不再指向历史 SHA，且没有别的默认路径继续消费旧常量。
4. DAV-865 的统计缺口修复、25 字段审计、消融快照一致性和生产库零写入契约是否被 DAV-866 意外破坏。
5. 父候选的 17 个原测试及 RT-1～RT-16 是否逐项仍存在、原断言未删除/放宽；新增测试是否只增加覆盖而非掩盖失败。

## 必须取得的证据

- `git diff --name-status 8c69eab..020d3e3` 只含：
  `tradingagents/eval/v03_return_measure.py`、`scripts/run_v03_return_measure.py`、`tests/test_v03_return_measure.py`；
- 完整 SHA、直接父、远端 ref、`git diff --check`；
- 定向 V-03 测试的精确结果；已知 `RT-S4` 因当前生产库全量计数已从旧快照 317 变为 318 的失败必须单列，不能算新回归；
- 默认 provenance 的正反例：默认路径统一离线缺口，显式 `a227…` 仍准确回读；
- 生产库只读核验（如复跑 runner）：前后 SHA、计数、`quick_check`、`integrity_check` 不变；临时 `work/` 产物不进入提交。

## 结论门槛

只有在上述覆盖无 HIGH/MEDIUM 缺口、且 DAV-866 的窄修没有改变收益实验口径时，才允许进入下一张只读代码审查卡；代码审查必须派给**代码审核员**，禁止派给独立代码审核员。红队卡本身不授权合入或部署。
