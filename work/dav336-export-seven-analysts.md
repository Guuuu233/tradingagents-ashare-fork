# DAV-336 历史报告导出缺 3 位分析师 + 总监 Verdict 全景合并分析师

用户导出 `analysis-600036.SH-2026-08-21.md` / `analysis-688836.SH-2026-08-21.md` 后发现：数据库实际有 7 位分析师报告，导出的 markdown 只有 4 位；研究团队决策的 Verdict 全景也把 7 位合并成 4 位。两个独立缺陷，按文件归属拆分给两位开发者，互不冲突。

## Bug A（前端导出模板缺 3 节）— owner：资深开发2

**现象**：报告列表页导出的 markdown 只有 市场分析报告/舆情分析报告/新闻分析报告/基本面分析报告 + 研究团队决策/交易团队计划/最终交易决策。

**根因**：`frontend/src/pages/Reports.tsx` 第 160-168 行 `REPORT_EXPORT_SECTIONS` 只列了 4 个分析师字段，缺：
- `macro_report`（宏观板块报告）
- `smart_money_report`（主力资金报告）
- `volume_price_report`（量价分析报告）

而 `frontend/src/components/ReportViewer.tsx` 的 `REPORT_SECTIONS`（第 11-22 行）是完整 10 节。两处不一致。

**修复要求**：
1. `Reports.tsx` 的 `REPORT_EXPORT_SECTIONS` 与 `ReportViewer.tsx` 的 `REPORT_SECTIONS` 对齐：补齐上述 3 节，顺序保持 分析团队 7 节 → 研究团队决策 → 交易团队计划 → 最终交易决策。
2. 建议直接复用同一份数组（从 ReportViewer 导出或抽到公共模块），杜绝再次漂移；不强制，但两处必须内容一致。
3. 新增/更新前端测试：断言导出 sections 覆盖全部 7 个分析师 key（macro/market/sentiment/news/fundamentals/smart_money/volume_price）+ 3 个团队 key。
4. 跑 `npm test -- --run`、`tsc`、`npm run build` 全绿。

**禁止**：改后端、改导出文件名格式 `analysis-<symbol>-<trade_date>.md`、改免责声明。

## Bug B（总监 Verdict 全景未逐一列全 7 位分析师）— owner：高级开发·支援

**现象**：研究团队决策的「各分析师 Verdict 全景概览与动态加权」只写 4 个合并视角（如"微观资金分析师""量价微观分析师""基本面/宏观分析师"），7 位分析师没有逐一出现。

**已核实事实（不是数据丢失）**：`reports.result_data.investment_debate_state.report_manifest` 显示 7 份报告全部 `passed=true`（macro 4942 / market 2620 / sentiment 2080 / news 4285 / fundamentals 4193 / smart_money 3309 / volume_price 4241 字符），总监输入完整；纯属输出端未强制逐一点名。同一账户另一份报告（0e01618b）列了 6 位，说明行为随机、无硬约束。

**根因**：`tradingagents/prompts/zh.py` 研究经理 prompt（约 324-326 行）只要求"列出各分析师 verdict"，未强制逐一列出全部七位、未禁止合并视角。

**修复要求**：
1. 修改该段 prompt：必须逐一列出全部七位分析师——宏观板块、市场（技术面）、舆情（情绪）、新闻、基本面、主力资金、量价——各自的 verdict（看多/偏多/中性/偏空/看空）与权重；禁止把多位分析师合并成一个视角或省略任何一位；动态加权逻辑（短线/中线权重）保持不变。
2. 只改 prompt 文本与对应测试，不改 Python 逻辑、不加新字段、不动 MANAGER_VERDICT/VERDICT 机读块。
3. 新增/更新测试：覆盖 prompt 文本含七位分析师逐一列出的硬性要求（参考现有 prompt 断言测试模式）。
4. 用 `.venv310`（Python 3.10）跑定向测试（prompt 相关 + research manager 相关）全绿；系统 Python 证据无效。

**通用纪律**：从当前主干 `bb1b693156d60f2e582e9e0c2568e610d65295ab` 检出；`multica repo checkout`；分支推送到 target；交付评论必须带 `git ls-remote target` 可核验的精确 SHA 与父提交；禁止改用户配置/.env/主干；禁止 `git add .`。
