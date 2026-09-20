Cursor 并行回顾结论（完整报告见仓库 `work/audit-retro-p2-t5-t11.md`）：

- tip `68ae241` 社交相关 pytest：**178 passed**
- **总评 ❌**：High H1 已复现——lookback 窗外无合格帖被标 `refused`+`observed_after_cutoff_excluded` 并进 structural ledger（期望 `empty`/`social_empty`、无失败条目）
- 另有 M1–M7（长度/硬编码 commit/字符串 tie-break/`_log_state` 漏字段等）
- D-008 核心资格、disabled 不读库、active 无 get_news、未删 legacy：**PASS**

请在你的只读报告中核对 H1；不要因 pytest 全绿写「可合入」。本卡交付后由 Cursor 汇总是否开返修（H1 返修卡将另开）。
