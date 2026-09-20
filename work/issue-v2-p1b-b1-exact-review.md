## 固定审核对象

- trunk基线：`ccc4c53a4985f8db32353586e6cb4317aa34f8cd`
- 行为提交：`f8ffe4764773276c04e1228d2a76eb5013c51369`
- EOF机械子提交：`a2b7515c7a549a642d4c6438cab36132bb0228b5`
- 最终远端分支：`agent/2/1f3eb19a5a03`
- 线性链必须为ccc4c53→f8ffe47→a2b7515；相对trunk恰好9文件；相对f8仅两个EOF文件删4行。

严格只读：0 code/test/config/DB/service changes；宿主Python3.10窄测，不跑全量，不合入/不重启。

## 必审

1. O1默认v1/request级v2/deepcopy隔离；不得写个人/持久配置。
2. O2 v2 Opening恰好3条claim、3个不同合法battlefield；1/2/4条数量闸；responded/target/resolved为空；Bear message2不触发legacy B/C；v1 DAV-346保持。
3. stage权威：accepted Bull1后state opening；accepted Bear2后round_message/claim自身opening但state challenge；invalid/missing attempt不推进count/stage/claims。
4. O3动态隔离：history/current_response/claims/focus/unresolved/summary/memory；七报告字节对称；authoritative state保留。
5. 静态模板：同一bull_prompt/bear_prompt使用marker+统一render helper，不新增并行prompt key；Opening最终中英文无INV-1..5和回应/target对手指令；legacy最终保留原三轮、INV示例和respond/target规则。检查marker缺失/重复时的行为风险与正则边界。
6. retry Opening只要求3条/3战场/空responded-target-resolved，无对手ID泄漏；legacy retry不变。
7. 文件范围9个；api/main.py、conditional_logic.py、setup.py、manager/challenge/tiebreak/DB/config未改；diff-check无输出。
8. 独立宿主3.10：Opening16、prompt/custom186、核心248、replay、compileall。可采样但必须复跑关键专项与至少完整核心矩阵；不得只采信开发报告。

输出PASS/BLOCK、文件:行号、命令终态、精确SHA。明确未合入/未重启/未上线，3/1不变。不要mention项目调度助手。