# TradingAgents-AShare v2 Phase 0 宿主回归与基线分类（只读）

## 固定环境

- checkout 必须是当前 target trunk 或独立 Phase 0 combined tree，不能用无关旧分支
- Python：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，Python 3.10.20
- 所有命令前使用 `env -u PYTHONPATH`
- 不修改代码、测试、配置、数据库、用户设置、providers、模型绑定、API Key；不重启服务

## 任务

1. 在 `45821dd4f21a5f65578dbf54f5d916970ae835c0` 基线重跑：
   `tests/test_swimlane_de_audit.py tests/test_debate_bundle_wash.py`
2. 对现有规格定向测试矩阵做文件存在性盘点；只运行真实存在的文件。
3. 对组合树（出现新的远端/临时 SHA 后）重跑同一集合，逐条将失败分类为 trunk 已有、组合新增、测试契约冲突或环境/API。
4. 运行 compileall 和 `git diff --check`；不得把未执行命令写成通过。
5. 若宿主全量 pytest 在精确组合树可用，运行并记录完整通过/失败/跳过；若 checkout 或解释器不可用，明确 blocked。

## 交付

给出精确 checkout/SHA、解释器、命令、原始通过/失败/跳过、失败分类和是否影响 Phase 0 gate。明确未合入、未重启、未上线。不要修改项目代码。
