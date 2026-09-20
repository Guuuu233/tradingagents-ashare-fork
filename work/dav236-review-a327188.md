# DAV-236 精确SHA独立终审：单horizon状态累加

## 对象
- 主干基线：`394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 候选：`target/agent/worker1/01a020e5@a3271882736c407391c276983a8bddcf16f55bda`
- 仅改`api/main.py`、`tests/test_debate_state_persistence.py`

## 只读审查
1. checkout精确SHA，cd返回路径并确认HEAD。
2. 审查累计策略：缺字段保留旧值；空初始debate不覆盖非空；真实非空更新可覆盖；不得让其他普通dict（market_data_context、claims等）被错误深合并或保留陈旧值。
3. 检查`_is_empty_debate_state`是否仅用于investment/risk两个字段，避免泛化副作用。
4. 检查普通single-horizon stream_events=True路径；确认stream_events=False invoke路径不回归，双horizon不改。
5. 测试必须真实复现多chunk顺序，且断言config_overrides 3/3传入Graph。
6. 独立运行定向86项、`TUSHARE_TOKEN=''`全量、compileall、diff-check。
7. 输出PASS/打回，附文件:行号、真实命令和结果。不得改代码/合入/部署。