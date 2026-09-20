DAV-336 精确 SHA 只读复审。

候选A 前端导出：`1882ed7030c8d6aac90cdc20a8332a74c3373942`，父 `bb1b693156d60f2e582e9e0c2568e610d65295ab`。
候选B 研究经理 prompt：`301ef6063ce00efa8dfd7df19e06b027a1ed1c5d`，父同上。

两者可独立审查，禁止改代码。

A验收：公共数组完整覆盖7个 analyst report key+3个团队key，Reports导出顺序正确，ReportViewer未回归，导出文件名/免责声明未变，前端测试/tsc/build证据有效。
B验收：研究经理输出要求明确逐一点名 宏观板块、市场（技术面）、舆情（情绪）、新闻、基本面、主力资金、量价，均有verdict+weight；明确禁止合并/遗漏；保留短中线权重和机读块；对应测试不是脆弱字符串误判。

使用精确 checkout / `.venv310` 只跑相关定向测试。交付 PASS 或可复现阻塞，附精确SHA和测试。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)
