## 固定审核对象

- trunk父SHA：`50e115347b49bcb9e767c593296045a356099006`
- 远端分支：`agent/agent/1ce6e677d670`
- 精确SHA：`4df60d338ed418e4b85a72afbe6626eae0f1355b`
- 变更应恰好1文件：`tests/test_global_indices_fallback.py`
- 只读审核，0 code changes；禁止全量、主干、服务、DB、配置。

## 必审项

1. 直接父为trunk 50e1153；文件范围恰好1个，无provider实现改动。
2. RED复现：未patch时显式历史日测试因当前纽约会话日晚于curr_date，int美股项被防前视正确过滤。
3. GREEN修复仅在该测试中patch模块路径 `_get_latest_us_session_date` 返回`2026-08-21`；不能改mock数据、断言或删护栏。
4. 验证patch路径确实命中cn_akshare_provider模块全局helper，不是无效mock。
5. 精确测试与整文件10项、diff-check、compileall通过；防前视future-as_of测试仍通过。
6. 给出PASS/BLOCK与命令结果；0 changes；未合入/未重启/未上线，P1-B锁定。

不要 mention 项目调度助手。