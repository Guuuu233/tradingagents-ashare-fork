# DAV-215：主力资金稳定性与窗口/单源优先语义修复

## 用户现象

主力资金数据有时获取不到或报告被guard阻断。

## 真实证据

1. 服务环境正确：DB/no_proxy正常；`.env`中`TUSHARE_TOKEN`存在且`api.main.load_dotenv()`后可用。
2. 同股同日服务路径实测Tushare可返回：
   - `tushare_eastmoney_moneyflow_dc.r0_net = +2.211971亿元`，1d，2026-08-20；
   - `tushare_ths_moneyflow_ths.netamount = -1.928246亿元`，1d，字段不同；
   - priority selection正确选择Tushare-东财为主力方向。
3. 外部链有波动：AkShare/东财有ProxyError或首次90秒超时，fallback有时只剩THS即时总净额；attempt chain可审计。
4. 确定性缺陷：`fund_flow_evidence._selection_group_summary()`对历史daily源取最近5条求和，但返回仍标`time_window=1d`、`as_of=最新日`。招商银行当天值`+2.21197136`，5条求和约`-0.7180142`，顶层被误标为1d/outflow。
5. 规则冲突：项目现行政策是“单个有效高优先级新算法源即可给方向”。旧`consensus_audit`仍会因同字段只有1个来源标`insufficient_sources/data_conflict`。最终guard不得让旧多源共识覆盖合法priority selection；但模型正文必须与被选来源同字段/同窗口/同值一致。

## 施工目标

A. 修正窗口语义：
- priority selection用于方向时，对historical_daily源应选requested_as_of当天记录，`selected_value`就是当天值，`window_days=1,time_window=1d`；
- 如需要5日累计，必须独立字段/结构，明确`time_window=5d,window_days=5`，不得冒充1d；
- 不跨字段混算`r0_net`与`netamount`。

B. 单源优先与校验一致：
- 合法selection（日期/字段/单位/值均通过）可授权方向；
- `consensus_audit`仅作旁证/差异审计，单源不足不能覆盖priority selection为blocked；
- `validate_model_summary()`仅验证被选来源、被选字段、被选窗口和值；
- 若模型正文与selection不一致仍必须blocked；
- 只有THS `netamount`时只能表达“总资金（非主力口径）”，不得称主力资金。

C. 稳定性/可观测性：
- 保留attempted_sources/fallback_errors/failure_categories/final_source；
- Tushare/AkShare/东财失败时继续fallback，不把字符串失败当成功；
- 不要求购买新数据源，不改Token/代理/用户配置。

## TDD必测

1. 5条东财daily记录：当天`+2.21`、5日和`-0.718`，priority selection必须返回1d`+2.21/inflow`；
2. 独立5d summary如存在必须标5d，不得污染1d selection；
3. 单个Tushare-DC `r0_net`有效：selection允许方向，旧consensus_audit insufficient_sources不阻断最终guard；
4. DC `r0_net`与THS `netamount`方向相反：按优先级选DC，保留THS旁证，不跨字段判冲突；
5. 仅THS netamount：允许“总资金偏流入/流出”，Smart Money不得写主力吸筹/减持；
6. 模型正文错引5日和为当天值：validation mismatch并阻断；
7. 东财超时/Tushare失败→THS fallback attempt chain完整；
8. 所有源失败→显式gap。

## 边界

允许修改`fund_flow_evidence.py`、`smart_money_analyst.py`、必要的provider元数据生成和现有相关测试；不改其他分析师、Prompt、DB schema、用户配置、Token/URL、辩论代码。一个提交一个关注点；若窗口修复与guard修复需两个commit则分开。不得跑/重启真实LLM服务。

## 验证

专项资金流测试、provider链测试、Smart Money测试、全量tests、compileall、diff-check。推送远端分支和精确SHA，提供RED→GREEN与固定探针预期。
