# 数学复算包

日期：2026-09-23。

这是统计方法与代数检查，未使用股票真实样本，也不访问 TradingAgents、网络、数据库或模型服务。

## 运行

在已有 Python 和 NumPy 的环境中运行：

```sh
python3 01_original_simulation.py
python3 02_independent_audit.py
```

核查环境为 Python 3.14.6、NumPy 2.5.1。requirements.txt 记录 NumPy 版本。本包不自动安装软件。

## 内容

- 01_original_simulation.py：原 10,000 次零效应模拟、样本量表、Brier 恒等式、收缩与融合检查。相对原脚本只修改了输出路径，使结果写在脚本所在目录。
- 02_independent_audit.py：计数级的独立 200,000 次模拟；Beta-Binomial 概率卷积所得原普通检验的有限样本误报概率；小改变量与三桶基率反例；舍入核对。
- 两份 recorded_results.json：本次已运行结果。脚本重跑后生成对应的 rerun.json，不覆盖记录。

## 解释边界

原模拟：40 个独立簇，每簇 20 个 Bernoulli 结果，簇内共享 Beta(4.5,4.5) 概率。A=0.45、B=0.55，边际真实概率为 0.5，两臂真实 Brier 差为零。簇内相关为 0.10。

计数级模拟与逐条 Bernoulli 模拟使用相同数据生成分布、不同实现和种子，因此模拟比例不应要求逐位相同。有限样本普通检验的精确概率通过卷积求得，约 25.3915%；DEFF 正态近似约 24.9761%，两者不是同一个计算。

所有功效数字均为情景或反例，不能视为本项目正式所需样本量。
