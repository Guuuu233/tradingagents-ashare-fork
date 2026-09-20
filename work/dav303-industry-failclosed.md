# DAV-303 P1：产业链联想 fail-closed 注入

**基线父提交：`ab3cda62bc676562c9421f809c0f1fa95d62f34e`。独立分支，不合主干。**

## 已坐实

`format_industry_linkage_for_prompt(None)` 返回空串（`industry_linkage.py` ~2531）。宏观/基本面因此不插入「【产业链联想数据】」。京东方 `81f63cad` 宏观该标记 **0 次**，违反 §4.5「报告中出现【产业链联想数据】段落（即使部分指标缺失）」。

## 契约

1. `format_industry_linkage_for_prompt`：**永远返回非空**。无映射/无采集时输出：
   `【产业链联想数据】：【数据缺失】（未映射行业或采集失败，不得据此推断景气中性）`
2. 有映射但指标失败：保留标题+行业名，失败指标行写【数据缺失】，禁止丢整段。
3. 宏观、基本面分析师在 pool 无数据时仍必须把上述段落放进 human prompt（不要 `if text:` 才 append）。
4. 不改 INDUSTRY_LINKAGE_MAP 规模；不买付费源；yfinance 失败继续标缺失。

## 白名单

- `tradingagents/dataflows/industry_linkage.py`
- `tradingagents/agents/analysts/macro_analyst.py`
- `tradingagents/agents/analysts/fundamentals_analyst.py`
- `tests/test_industry_linkage_prompt_injection.py`（无则新建 `tests/test_industry_linkage_failclosed.py`）

## 验收

空输入不再返回 `""`；有数据仍含「【产业链联想数据】」。定向 pytest + compileall + git diff --check。推独立分支。不得 @项目调度助手。
