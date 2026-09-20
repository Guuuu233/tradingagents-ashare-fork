## 返修前请先更正父提交：主线已不是 `8854853`

上一条调度指令要求「保持锁定父 `8854853cfc167fd9bb015528138ae36a1df6bf8f`」，**该信息已过期**。

`git ls-remote origin codex/dav-4-p2a-trunk` 当前为 **`331a32284b0b62599f1cf76806d09147fa680a4c`**（其后合入了 DAV-938 的纯前端候选 `66591467`）。按 D-012，新候选的**直接父必须等于交付当时的主线 tip**，请以 `331a322` 为准，并在提交前再次 `ls-remote` 复核（可能又已推进，见下）。

已合入且与本卡白名单无交集的文件：`api/main.py`、`cn_akshare_provider.py`、`macro_market_utils.py`、`industry_linkage_provider.py`、`frontend/src/pages/Dashboard.tsx`。本卡白名单仍为 `tradingagents/knowledge/historical_cases.py`、`tests/test_historical_cases.py` 两项，不变。

**预告**：DAV-946 候选 `483a1098` 已通过复审（DAV-986 PASS），运维正在做合入前的失败集合对照，通过后主线会再次推进。交付前务必重新 `ls-remote`，不要凭本条消息里的 SHA 直接提交。

## 本轮修复范围（沿用代码审核员 FAIL 意见）

1. 优先修 `backfill_pending_cases` 的 dirty-state / NULL 持久化问题——这是触及持久化与跨进程消费边界的问题，**不能用定向测试通过来替代**。
2. 将 legacy `【数据获取失败】` 字符串统一为 typed refusal。
3. 明确 API `to_dict()` / 格式化层对 refusal 的可序列化表示，确保跨进程消费端拿到的是可识别结构而非普通字符串。

## 回归证据要求

不要再尝试跑整套 pytest。运维已定位主干死锁根因（见 DAV-979）：`tests/test_api_smoke.py` 在同一进程内进出 `api.main.lifespan`，其关闭路径 `api/main.py:441` 把**模块级全局** `_executor` 永久 shutdown，导致后续用例（如 `test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation`）永久阻塞。这是**主干既有缺陷，不是你引入的**。

请改用分文件对照：对 `tests/test_*.py` 逐个独立进程执行（每个 120s 看门狗），在候选与其直接父两侧使用**完全相同的切分**，逐项对照失败集合，只看有无**新增**失败。参考基线（`b95a9b88` 实测）：`214 OK / 8 个失败文件 / 1 个挂死文件（test_knowledge_rag.py）`。

解释器必须是 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，报告贴 `-V`（须 `Python 3.10.20`）。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
