Cursor 独立复审 `5e360af95d3cd1c1a5ad88b35ce7fbd325b197fa`：**不准予合入。**

隔离 worktree `/tmp/ta-p03b-5e360af` 复跑 `tests/test_financial_period_kind.py` + `tests/test_financial_announce_cutoff.py`：**68 passed**。接线、H1 `not_derived`、无 Q1 的 `missing_q1` 都在。不够。

High：`INCOME_DERIVATION_WHITELIST` 把展示用的 `基本每股收益`/`稀释每股收益` 当金额减。同 SHA 实测 H1 EPS 2.50 − Q1 1.00 → 派生块写出 Q2 EPS 1.5。这不是单季 EPS。派生用的是 `filtered` 全表，压缩掉 EPS 也挡不住。

返修 DAV-474。不要 FF、不要部署。独立审核员 PASS 与项目主管建议合入均不够（D-010）。
