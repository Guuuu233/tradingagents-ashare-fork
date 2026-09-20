不准予合入。

候选 SHA：`f1c73e7f0b1650fc654e63fdc92f40a2876855fc`  
父提交：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`  
独立审核员：✅通过（必要但不充分）  
Cursor 隔离 brief：**35 passed**，但契约复核发现 High：

1. `evaluate_social_depth` 将默认 `disabled`/`not_applicable`/`shadow` 当作不足态，legacy 舆情正文无「不可判断」即 fail（已复现）。
2. `apply_report_quality_gate` 未纳入 `sentiment_report`，social depth 在生产 apply 路径未执行；Task 12 D 未真正落地。

已开返修卡。禁止 FF / 部署。独立审核员 PASS ≠ 准予合入（D-010）。
