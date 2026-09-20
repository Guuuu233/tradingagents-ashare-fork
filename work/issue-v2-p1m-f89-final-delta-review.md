## 固定审核对象

- 远端组合分支：`agent/1/6c00ef564d96`
- 前一组合SHA：`34b1dcf62df7959669116d39c7326db85e996bbc`（DAV-377精确复审PASS）
- 最终组合SHA：`f89b6009544a60727499a02f2e7c585502802801`
- 测试修复原SHA：`4df60d338ed418e4b85a72afbe6626eae0f1355b`（DAV-379精确复审PASS）
- trunk基线：`50e115347b49bcb9e767c593296045a356099006`
- 全量由Hermes在同一精确checkout独立运行；本卡禁止运行任何pytest。

严格只读、0 code changes，禁止主干/服务/DB/配置。

## 审核

1. 远端分支精确为f89b600；其直接父为34b1dcf。
2. `git diff 34b1dcf..f89b600` 必须与 `git diff 50e1153..4df60d3` patch等价（patch-id或逐字节），仅`tests/test_global_indices_fallback.py`。
3. 最终ancestry线性：50e1153→33c6e6b→25e52c0→34b1dcf→f89b600；无merge commit/重复提交。
4. trunk至最终去重changed files恰好9个；第9个仅时间冻结测试，不改provider与防前视护栏。
5. 前一组合复审PASS与单文件修复复审PASS均可继承；检查重放无冲突/无内容漂移。
6. `git diff --check 50e1153..f89b600`、compileall可执行；禁止pytest。
7. 输出PASS/BLOCK、命令证据、0 changes；未合入/未重启/未上线，P1-B锁定。

不要 mention 项目调度助手。