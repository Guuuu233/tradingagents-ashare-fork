# 全量 pytest 失败归因（相对 a25d404）

## 已跑证据

命令（清代理）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u all_proxy \
  -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  PYTHONUNBUFFERED=1 .venv310/bin/python -u -m pytest tests -q --tb=line \
  --deselect 'tests/test_api_smoke.py::TestChatCompletionsEndpoint::test_unauthenticated_uses_local_default_user_fixture'
```

结果：**55 failed, 2110 passed, 1 skipped, 1 deselected**（约 17m）。

已知挂起（deselected，开工前即有）：`TestChatCompletionsEndpoint::test_unauthenticated_uses_local_default_user_fixture` **缺 `dry_run`**，会跑真分析。

## 已确认与本轮相关（另有 issue 修夹具）

4 个研究总监/dispute 失败 → A2/A4 fail-closed 与旧夹具冲突（见配套 issue）。

## 你要做（只读归因，默认不改产品代码）

1. 从主干 `a25d404` checkout 只读工作树或隔离分支。
2. 对其余 ~51 个失败按文件聚类，判定：
   - **pre-existing / 网络或外部源**（东财/新浪/行业联动 live）
   - **本轮 docs/B1–A4/social cherry-pick 引入**
3. 对“疑似本轮引入”的失败给出最小复现命令与根因一句话；不要大范围修。
4. 交付：issue 评论一张表（测试名 / 类别 / 证据）。禁止把 live 网络失败标成“已修复”。

参考失败簇（非完整）：`test_cn_akshare_backup_sources`、`test_sina_historical_fund_flow`、`test_financial_*` smoke、`test_industry_linkage`、`test_frozen_trade_date_isolation`、`test_recalculate_weekly_metrics` CLI。
