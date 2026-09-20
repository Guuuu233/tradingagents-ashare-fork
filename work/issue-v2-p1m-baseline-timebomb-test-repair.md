## 精确基线与复现

- 基线：`target/codex/dav-4-p2a-trunk@50e115347b49bcb9e767c593296045a356099006`
- P1-M组合：`34b1dcf62df7959669116d39c7326db85e996bbc`，精确复审PASS；组合全量唯一失败与trunk相同。
- 失败测试：`tests/test_global_indices_fallback.py::TestGlobalIndicesFallback::test_sina_hq_int_symbols_live_format_mock`
- trunk与组合均同样失败：`assert "标普500" in snapshots`。

## 根因（已实测）

`_fetch_global_indices_sina_hq(curr_date="2026-08-21")` 对无来源日期的 `int_sp500/int_nasdaq/int_dji` 调用 `_get_latest_us_session_date()`。该helper使用纽约当前墙钟；当前日期晚于写死的2026-08-21，所以三项得到 `as_of > curr_date`，被防前视护栏正确过滤。欧洲/港股自带日期仍保留。

这是时间敏感测试未冻结时钟，不是provider bug。禁止修改provider、禁止把当前快照伪标为历史日期、禁止删除 `as_of > curr_date` 护栏。

## 严格范围

只允许修改：
- `tests/test_global_indices_fallback.py`

禁止任何其他文件、代码、配置、DB、服务、主干修改。

## TDD/修复要求

1. 在fresh trunk checkout先运行精确测试，确认当前 RED及断言与上述一致。
2. 最小修复：在测试现有 `requests.get` mock旁，patch模块路径：
   `tradingagents.dataflows.providers.cn_akshare_provider._get_latest_us_session_date`
   返回 `"2026-08-21"`。
3. 不改mock响应、断言、provider实现；必须继续验证int_sp500/int_nasdaq/int_dji解析和欧洲日期。
4. GREEN：运行精确测试与整个 `tests/test_global_indices_fallback.py`。
5. `git diff --check`、compileall该测试。
6. changed files恰好1个；提交推送远端branch/SHA。
7. 不跑全量；最终由Hermes把该测试修复cherry-pick到P1-M组合后统一全量。
8. 明确未合入/未重启/未上线；P1-B仍锁定。

不要 mention 项目调度助手。