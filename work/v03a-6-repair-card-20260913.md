# DAV-865：修复 V-03a provenance 与硬编码统计回退

## 任务定位

这是 DAV-864 代码审查后针对同一候选的返修卡。审查结论为“有条件通过”，但以下两个 MEDIUM 问题触及 V-03a 的禁止伪造指标与来源可追溯要求；修好前不得合入 `c36fbc525355a880835db8c43d65fc445b6bdc95`。

固定起点：

```text
base candidate: c36fbc525355a880835db8c43d65fc445b6bdc95
base parent:    7cd1a8523e5f879712739576d7b6f101f2c1ff2c
trunk ancestor: a227cdc3bb466edf2e910419cb6013cfc021d309
review: DAV-864
```

不得修改生产数据库、API、provider、role/model 配置、加权开关、社交 active、历史报告或 DAV-808；不得部署、重启或合入。只在返修候选上提交代码，交付状态置为 `in_review`。

## 必须修复的两项

### 1. 运行服务 provenance 必须不混淆

当前 `BASELINE_RUNNING_SERVICE_SHA=a6d4540...` 是 2026-09-08 历史样本生成基线；现场 `/healthz` 当前运行服务是 `a227cdc3bb466edf2e910419cb6013cfc021d309`。不能继续把历史生成版本静态填进名为 `running_service_sha` 的当前运行字段。

要求：

- 明确区分“历史样本生成服务 SHA”和“当前运行服务 SHA”；如保留历史值，字段名必须表达历史生成语义；
- 当前运行服务 SHA 必须来自明确、可复核的来源（例如只读 healthz 探针或显式运行参数）；离线环境无法取得时必须输出 typed gap/明确的 offline replay 标记，不能悄悄填旧常量；
- 既有历史样本与当前服务的事实不能被抹掉，报告必须能看出两者的区别；
- 为当前服务可用、不可用/离线两种情况各加测试，禁止依赖网络才能让主流程通过。

### 2. 删除业务统计的硬编码回退

审查指出以下默认值会在上游没有传入真实统计时生成幽灵指标：`317/231/86/217`。涉及 `SnapshotManifest`、`EvaluationStamp`、`V03ReturnMeasureEngine.__init__` 和 `measure_dataset`。

要求：

- 不得在这些路径中把 `317/231/86/217` 当作默认事实；
- 统计缺失时必须 fail-closed（抛出明确错误）或输出显式 typed gap/`None`，不能生成貌似真实的报告；
- 正常 runner 仍必须从只读副本实时查询并传入真实统计；
- 加测试证明：空统计/缺统计不会回退成 `317/231/86/217`，真实统计仍准确回读；
- 不得用把默认值改成另一个硬编码数字来绕过门禁。

## 允许修改

仅允许继续修改：

```text
tradingagents/eval/v03_return_measure.py
scripts/run_v03_return_measure.py
tests/test_v03_return_measure.py
```

可新增与上述三条路径直接对应的测试，但不得删除、重命名或改松父候选 `c36fbc...` 已有的 17 个原测试和 RT-1～RT-16 测试。不得新增收益计算路径。

## 验收门禁

1. 新候选必须是完整 40 位 SHA，直接父为 `c36fbc525355a880835db8c43d65fc445b6bdc95`，远端分支明确可回读。
2. 白名单 diff 仍只有三条允许路径；`git diff --check` clean；`work/` 产物不进提交。
3. 定向 V-03 测试：既有 17 项和 RT-1～RT-16 仍按父候选结果执行；原有 `test_rt_s4` 基线失败如实记录，不得改硬编码掩盖。
4. 新增 provenance/缺统计测试实际通过，并在报告中列出精确测试数。
5. 候选提交后的离线 runner 必须重新运行：生产库前后 SHA、计数、`quick_check`、`integrity_check` 不变；报告 `code_sha` 是新候选 SHA；运行服务字段不再把 `a6d4540...` 冒充当前运行服务；统计来源可追溯或显式缺口。
6. 不得把这张卡标成 done/准予合入；交付后置为 `in_review`，下游由**代码审核员**针对新 SHA 再审，禁止派给独立代码审核员。
