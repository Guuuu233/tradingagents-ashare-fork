独立审核：同花顺大单平级 + 参考可信度

对照实现卡与 `work/2026-09-04-fund-flow-lg-credibility-dispatch.md`。

核验：
1. tip 祖先是否含实现 SHA；diff 是否仅允许路径。
2. THS `buy_lg` 是否产出与东财平级的 `r0_net` 证据。
3. `netamount` 并存是否不再整页 `incomparable` 清空主力报告。
4. 可信度字段是否存在且语义为「参考」而非绝对判断。
5. 定向 pytest 是否真实跑过；smart_money 双源时非冲突空壳；仅 netamount 写「主力吸筹」仍阻断。

输出：PASS/FAIL + 精确 40 字符 SHA。不准予部署；不 FF。
