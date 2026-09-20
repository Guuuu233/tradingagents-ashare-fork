## 固定审核对象

- trunk基线：`50e115347b49bcb9e767c593296045a356099006`
- 远端组合分支：`agent/1/6c00ef564d96`
- 精确组合 SHA：`34b1dcf62df7959669116d39c7326db85e996bbc`
- 预期线性链：50e1153 → 33c6e6b → 25e52c0 → 34b1dcf（重放8ccd639 patch）
- DAV-373与DAV-375分别复审两个sibling PASS。
- 全量正在由Hermes对同一精确checkout单独执行；本卡禁止全量测试。

严格只读，0 code changes，禁止主干/服务/DB/配置。

## 必审项

1. 远端SHA、线性ancestry、无merge commit、无重复33c6e6b。
2. 从trunk去重changed files恰好8个；禁止api/main、researcher、conditional_logic、manager、prompt、DB、前端、provider、配置。
3. 确认两个sibling patch均完整等价：
   - `25e52c0`本身为直接祖先；
   - `git diff 25e52c0..34b1dcf` patch应等价于 `33c6e6b..8ccd639`（可用patch-id或逐文件diff，不可因SHA重放不同误判）。
4. 生产挂载与指标语义共同工作：
   - state初始化v1默认与深拷贝；
   - horizon result顶层/debate state可见metadata/flags/metrics；
   - real Golden轮次分母>0、数值去污染；
   -合法空值契约；legacy nested state兼容；无自嵌套。
5. feature flag默认关闭，legacy图路由/6消息/prompt不变。
6. 静态检查、compileall、P1-M 38项、关键Phase0矩阵与replay；不跑全量。
7. 明确开发时错误smoke是wrapper层级取错，纠正后3只Golden：296/169/231正分母；不能再把错误脚本当代码问题。
8. 输出PASS/BLOCK、文件:行号、命令结果；0 changes；未合入/未重启/未上线，P1-B锁定。

不要 mention 项目调度助手。