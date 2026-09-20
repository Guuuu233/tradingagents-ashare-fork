# DAV-217：DAV-214/215 三连提交独立只读终审

## 精确审查对象

- 主干：`target/codex/dav-4-p2a-trunk@7ef89f63662ce01bacdcda9dd0996060ce903c83`
- 候选：`target/agent/1/01a01fdf@7c2f19ab3f5d1176b235306bf3421fdbc14570b5`
- 提交链：`ce6073d5` → `97b597d1` → `7c2f19ab`

## 只读要求

不得改代码、不得提交、不得部署。独立检查：

1. DAV-214三个风险节点坏RISK_STATE隔离是否覆盖history和current_*_response；
2. 合法RISK_STATE、malformed、truncated、trailing prose、duplicate、缺冒号行为；
3. DAV-215单日/5日窗口语义是否真实分离；
4. 单源优先是否真的不被旧consensus_audit覆盖；
5. Tushare DC与THS不同字段是否被错误合并；
6. 仅THS netamount时是否禁止“主力”语义；
7. fallback attempt chain、gap、日期和单位是否保留；
8. 测试是否覆盖生产可达路径，而非只构造不可能fixture；
9. 精确复跑相关专项测试与diff-check；
10. 输出PASS或打回，必须标出文件:行号、严重级别和具体证据。

禁止修改任何分支。