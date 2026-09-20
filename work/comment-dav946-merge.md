## ✅ 已合入主线

`origin/codex/dav-4-p2a-trunk`：`331a322` → **`483a10981c922b3f98e86ac55b88545cbd88dc04`**（快进，无 merge commit）

- 候选：`483a1098`（`origin/agent/support/dav946-on-b95a-r2`），直接父即合入前 tip `331a322`
- 审查：DAV-986 ✅ PASS（同 SHA 只读复审，Python 3.10.20，PIT 测试 106 passed）
- 白名单 7 文件：`interface.py`、`y_finance.py`、`alpha_vantage_fundamentals.py`、`alpha_vantage_news.py`、`providers/yfinance_provider.py`、`providers/alpha_vantage_provider.py`、`tests/test_historical_yfinance_pit.py`

### 运维独立核验（除审查外）

**1. 功能探针**（`.venv310`，历史日 `2024-01-02`）：

| 入口 | 结果 |
|---|---|
| `y_finance.get_balance_sheet` / `get_cashflow` / `get_fundamentals` / `get_income_statement` / `get_insider_transactions` | 全部返回 `VendorRefuse` ✅ |
| `alpha_vantage_fundamentals` 四个原始函数 | 底层 `_make_api_request` **零调用**，返回 `VendorRefuse` ✅ |

即 DAV-980 的两个 🔴（raw wrapper 返回普通字符串、Alpha Vantage 绕过 provider 护栏）均已消除，且 R2 已修部分未回退。

**2. 失败集合对照**（分文件口径，两侧相同切分，120s 看门狗，`.venv310`）：

| | 基线 `331a322` | 候选 `483a1098` |
|---|---|---|
| OK | 215 | 216 |
| 失败文件 | 8 | **同样这 8 个** |
| 挂死文件 | 1（`test_knowledge_rag.py`） | **同一个** |

唯一差异为候选自带的新测试文件 `tests/test_historical_yfinance_pit.py` → `106 passed`。**零新增失败**。

说明：此处未跑单进程 RT-FULL，因主干存在既有死锁（根因见 DAV-979，修复由 DAV-992 承接），分文件对照是当前唯一可取得可比基线的方法。

### 未触碰

未部署、未重启、未写生产库。`data/tradingagents.db` SHA256 合入前后一致 `94d2f6740db4f206...`。

### 后续影响

- DAV-943（Alpha Vantage 日期过滤）、DAV-953（yfinance 窗口校验）此前与本候选抢文件，**冲突现已解除**，可排期。
- DAV-941 返修中，交付时直接父须为 `483a1098`（或届时的最新 tip，须 `ls-remote` 复核）。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
