# DAV-866：修复 V-03a 通用引擎默认 provenance 的当前服务字段回退

## 任务定位

这是 DAV-865 交付后的窄范围返修。DAV-865 候选 `8c69eab186bde58e49cbc134fb4da5f015c77e92` 已能在正常 runner 中通过只读 `/healthz` 显式取得当前运行服务 `a227cdc3bb466edf2e910419cb6013cfc021d309`，但静态复核发现通用引擎的无参数路径仍把历史样本生成 SHA `a6d4540feaa8043ff36b0607a31c1d2d5f004149` 放进 `EvaluationStamp.running_service_sha`：

```text
EvaluationStamp()                         -> running_service_sha = a6d4540...
V03ReturnMeasureEngine().measure_dataset  -> stamp.running_service_sha = a6d4540...
SnapshotManifest                         -> running_service_sha = offline_replay_gap
```

这会让同一份无服务上下文的结果出现两个不同语义：清单是离线缺口，报告 stamp 却冒充当前服务为历史版本。DAV-865 因此不能进入代码审查。

固定起点：

```text
base candidate: 8c69eab186bde58e49cbc134fb4da5f015c77e92
base parent:    c36fbc525355a880835db8c43d65fc445b6bdc95
trunk ancestor: a227cdc3bb466edf2e910419cb6013cfc021d309
upstream card:  DAV-865
```

## 必须修复

1. **无显式当前服务来源时禁止回填历史 SHA**
   - `EvaluationStamp.running_service_sha` 和 `measure_dataset` 生成的 stamp 必须输出与清单一致的显式离线/typed gap（推荐 `offline_replay_gap`，或等价的 `None`，但同一结果的两处必须一致）。
   - `HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA` / `historical_sample_generating_service_sha` 继续保留历史事实；它不得再作为名为 `running_service_sha` 的默认值。
   - 显式传入 `running_service_sha` 以及 runner 的 healthz 探针路径必须继续原样回读；不可因修复默认路径而丢失 `a227…` 与 provenance source。
   - 旧的 `BASELINE_RUNNING_SERVICE_SHA` 符号如为兼容而保留，必须明确不再代表当前运行服务，不能继续指向 `a6d…` 并被默认 stamp 消费。

2. **新增默认路径测试**
   - `EvaluationStamp()` 的当前运行字段不得等于历史样本生成 SHA。
   - `V03ReturnMeasureEngine().measure_dataset([])` 的 stamp 与 manifest 对当前运行字段必须一致，且显式标识离线缺口。
   - 现有 healthz 可用/不可用、显式 SHA、统计缺失测试继续通过。

## 允许修改

仅允许修改：

```text
tradingagents/eval/v03_return_measure.py
scripts/run_v03_return_measure.py
tests/test_v03_return_measure.py
```

不得改生产数据库、API、provider、role/model 配置、加权开关、社交 active、历史报告、DAV-808 或实验口径；不得部署、重启、合入。不得删除、重命名或放宽父候选已有的 17 个原测试和 RT-1～RT-16 测试；`work/` 产物不得进入提交。

## 交付门禁

1. 新提交为完整 40 位 SHA，直接父为 `8c69eab186bde58e49cbc134fb4da5f015c77e92`，远端分支可回读。
2. 白名单仍只有上面三条路径，`git diff --check` clean。
3. 定向 V-03 测试保留已知 `RT-S4` 基线失败，不得通过改硬编码或删断言掩盖；新增默认 provenance 测试实际通过。
4. 正常 runner 仍证明：生产库前后 SHA/计数/`quick_check`/`integrity_check` 不变；manifest、stamp、Markdown/JSON 对当前运行服务和历史生成服务语义一致；服务不可用时不填 `a6d…`。
5. 交付状态置为 `in_review`；下游先做独立红队覆盖复核，再交给**代码审核员**针对新 SHA 只读审查。禁止派给独立代码审核员，禁止实施者自审。
