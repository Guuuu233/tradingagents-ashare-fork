继续现有有效 run，不启动全量。S1 与 S3 已有真实 RED→GREEN；现在完成 S2 数字去污染与 S4 标签 GREEN，然后收口：

1. 运行 `tests/test_debate_metrics.py` 与 `tests/test_offline_ab_harness.py`；
2. 对三只 Golden 打印可复算 denominator/recycling摘要，确认不再 zero_denominator，股票代码/日期/INV编号不进入数字事实；
3. `compileall`、完整 diff-check；
4. changed files 仅允许 debate_metrics.py、对应 test、offline_ab_harness.py、对应 test；
5. 提交并推送 `agent/agent/2dea19aba46c`，发布精确 SHA；不跑全量，最终 sibling组合统一全量；
6. 禁止触碰 DAV-370 三文件、宿主树、主干、服务、配置。

[@代码运维测试员](mention://agent/f179edb8-9a81-4dbd-8787-afbfd307eda4)
