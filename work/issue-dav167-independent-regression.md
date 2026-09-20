# DAV-167 精确 SHA 独立回归

## 固定对象

- 分支：`agent/2/7eb2100b`
- SHA：`935c189476d71f513be324e13c26037e29a38e47`
- 基线：`codex/dav-4-p2a-trunk@cb9c62e6be92f18aa15cf5a513f9430d0409bf0b`
- 环境：宿主 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310`，Python 3.10.20

## 任务

只读检出精确 SHA，不修改代码、配置、数据库、用户设置或主干。执行：

1. 核验 target 远端分支和 SHA、基线 ancestry、changed-file scope。
2. 运行资金流定向套件：
   - `tests/test_cn_akshare_backup_sources.py`
   - `tests/test_sina_historical_fund_flow.py`
   - `tests/test_fund_flow_evidence.py`
   - `tests/test_smart_money_fund_flow_semantics.py`
   - `tests/test_vendor_chain_semantics.py`
   - `tests/test_data_collector.py`
3. 运行 `compileall` 和相对基线的 `git diff --check`。
4. 固定 live probe：`601398.SH`、`002167.SZ`，日期 `2026-08-14`；只报告 source、algorithm_group、requested/actual as-of、failure category、direction_allowed。不得把新浪 legacy 当新算法成功。
5. 给出 PASS/FAIL；明确未合入、未重启、未上线。

交付评论必须包含精确 branch/SHA、命令与计数，并真实 mention 项目主管。