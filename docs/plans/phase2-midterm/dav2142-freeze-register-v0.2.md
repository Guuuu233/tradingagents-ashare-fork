# DAV-2142｜冻结登记附表 v0.3

**登记结论：三项待裁事项已按总控10-16裁定落文，待增量复审及总控签字，尚未冻结；目标10-21前。** 总控对本附表与计划所在40位SHA签字即为冻结。沿用v0.2文件路径以便增量复审；只改模型字段、部署等价、跨年日期补登。

## 1. 采集环境与边界

采集截至：`2026-10-16T08:21:31.583140+00:00`；固定账户 `429163f7-50b6-4982-8bdf-96ae99506843`。
快照URI：`file:///Users/davidliu/ta-snapshot/current.db?mode=ro&immutable=1`；只读、query_only=ON、mmap=0、缓存8 MiB。
快照SHA256：`cc06fb590d9c726f060b4debc91865756481c356fb5963201829469c48614ab0`；大小 6337847296 bytes。采集前后size/mtime/inode一致。
未直开活库，未发网络/模型请求，未计算收益/IC/风格结果。仅元数据、模型分布、哈希和日历索引。

## 2. 交易日历与60日登记

来源文件：`/Users/davidliu/.cache/uv/archive-v0/xTvSj0PZAE1DRfB0/akshare/file_fold/calendar.json`（本机AkShare包内静态日历，离线读取，未请求线上日历）。
SHA256：`f1f1ce33c5cceb5c530c3271fc951405cb5624d2f30becec85dc7a85fd47e656`；覆盖 `1990-12-19` 至 `2026-12-31`。
首个正式信号日：**2026-10-08**；含起点按文件交易日顺序取60日，A/B轮换不删除计划日。第60日=**2026-12-30**。

**按现有日历冻结；CALENDAR_T10_OUT_OF_RANGE为待补日期标记，不再作为本次冻结阻塞。** 最后9个计划日的T+10跨2027，当前保留null，不用普通周一至周五或自然日猜补。官方2027交易日历公布后，按计划§5既定规则机械推算：维持60日序号及信号日，以官方交易日续接现有日历，从每个原信号日偏移10个交易日，填最后9个T+10可用日；确认截点=第60日T+10正常标签可用日的次一上海工作日12:00，工作日按官方安排确定。**仅作为补登附录追加日期，附官方来源和新日历SHA256；不得改动任何口径；补登须早于其中最早一个T+10日。** 冻结正文不覆盖，逾期则记录偏差及不可判定，不因补登重选样本或检验日程。

“T+10可用”指对应交易日收盘后、既有每日标签任务正常成功后；下表为交易日下界，不承诺任务成功。标签晚到不改变既定截点。上海工作日不能默认公历周一至周五；跨年工作日历亦需验证，确认截点遵从计划规定的次一上海工作日12:00。

| 序号 | 计划信号日 | T+10收盘日/正常标签可用下界 |
|---:|---|---|
| 1 | 2026-10-08 | 2026-10-22 收盘后标签任务成功 |
| 2 | 2026-10-09 | 2026-10-23 收盘后标签任务成功 |
| 3 | 2026-10-12 | 2026-10-26 收盘后标签任务成功 |
| 4 | 2026-10-13 | 2026-10-27 收盘后标签任务成功 |
| 5 | 2026-10-14 | 2026-10-28 收盘后标签任务成功 |
| 6 | 2026-10-15 | 2026-10-29 收盘后标签任务成功 |
| 7 | 2026-10-16 | 2026-10-30 收盘后标签任务成功 |
| 8 | 2026-10-19 | 2026-11-02 收盘后标签任务成功 |
| 9 | 2026-10-20 | 2026-11-03 收盘后标签任务成功 |
| 10 | 2026-10-21 | 2026-11-04 收盘后标签任务成功 |
| 11 | 2026-10-22 | 2026-11-05 收盘后标签任务成功 |
| 12 | 2026-10-23 | 2026-11-06 收盘后标签任务成功 |
| 13 | 2026-10-26 | 2026-11-09 收盘后标签任务成功 |
| 14 | 2026-10-27 | 2026-11-10 收盘后标签任务成功 |
| 15 | 2026-10-28 | 2026-11-11 收盘后标签任务成功 |
| 16 | 2026-10-29 | 2026-11-12 收盘后标签任务成功 |
| 17 | 2026-10-30 | 2026-11-13 收盘后标签任务成功 |
| 18 | 2026-11-02 | 2026-11-16 收盘后标签任务成功 |
| 19 | 2026-11-03 | 2026-11-17 收盘后标签任务成功 |
| 20 | 2026-11-04 | 2026-11-18 收盘后标签任务成功 |
| 21 | 2026-11-05 | 2026-11-19 收盘后标签任务成功 |
| 22 | 2026-11-06 | 2026-11-20 收盘后标签任务成功 |
| 23 | 2026-11-09 | 2026-11-23 收盘后标签任务成功 |
| 24 | 2026-11-10 | 2026-11-24 收盘后标签任务成功 |
| 25 | 2026-11-11 | 2026-11-25 收盘后标签任务成功 |
| 26 | 2026-11-12 | 2026-11-26 收盘后标签任务成功 |
| 27 | 2026-11-13 | 2026-11-27 收盘后标签任务成功 |
| 28 | 2026-11-16 | 2026-11-30 收盘后标签任务成功 |
| 29 | 2026-11-17 | 2026-12-01 收盘后标签任务成功 |
| 30 | 2026-11-18 | 2026-12-02 收盘后标签任务成功 |
| 31 | 2026-11-19 | 2026-12-03 收盘后标签任务成功 |
| 32 | 2026-11-20 | 2026-12-04 收盘后标签任务成功 |
| 33 | 2026-11-23 | 2026-12-07 收盘后标签任务成功 |
| 34 | 2026-11-24 | 2026-12-08 收盘后标签任务成功 |
| 35 | 2026-11-25 | 2026-12-09 收盘后标签任务成功 |
| 36 | 2026-11-26 | 2026-12-10 收盘后标签任务成功 |
| 37 | 2026-11-27 | 2026-12-11 收盘后标签任务成功 |
| 38 | 2026-11-30 | 2026-12-14 收盘后标签任务成功 |
| 39 | 2026-12-01 | 2026-12-15 收盘后标签任务成功 |
| 40 | 2026-12-02 | 2026-12-16 收盘后标签任务成功 |
| 41 | 2026-12-03 | 2026-12-17 收盘后标签任务成功 |
| 42 | 2026-12-04 | 2026-12-18 收盘后标签任务成功 |
| 43 | 2026-12-07 | 2026-12-21 收盘后标签任务成功 |
| 44 | 2026-12-08 | 2026-12-22 收盘后标签任务成功 |
| 45 | 2026-12-09 | 2026-12-23 收盘后标签任务成功 |
| 46 | 2026-12-10 | 2026-12-24 收盘后标签任务成功 |
| 47 | 2026-12-11 | 2026-12-25 收盘后标签任务成功 |
| 48 | 2026-12-14 | 2026-12-28 收盘后标签任务成功 |
| 49 | 2026-12-15 | 2026-12-29 收盘后标签任务成功 |
| 50 | 2026-12-16 | 2026-12-30 收盘后标签任务成功 |
| 51 | 2026-12-17 | 2026-12-31 收盘后标签任务成功 |
| 52 | 2026-12-18 | null：CALENDAR_T10_OUT_OF_RANGE |
| 53 | 2026-12-21 | null：CALENDAR_T10_OUT_OF_RANGE |
| 54 | 2026-12-22 | null：CALENDAR_T10_OUT_OF_RANGE |
| 55 | 2026-12-23 | null：CALENDAR_T10_OUT_OF_RANGE |
| 56 | 2026-12-24 | null：CALENDAR_T10_OUT_OF_RANGE |
| 57 | 2026-12-25 | null：CALENDAR_T10_OUT_OF_RANGE |
| 58 | 2026-12-28 | null：CALENDAR_T10_OUT_OF_RANGE |
| 59 | 2026-12-29 | null：CALENDAR_T10_OUT_OF_RANGE |
| 60 | 2026-12-30 | null：CALENDAR_T10_OUT_OF_RANGE |

第20/40计划日仅发描述性快照；其信号日/T+10下界分别为 2026-11-04 / 2026-11-18; 2026-12-02 / 2026-12-16。不改成第20/40个有效日。

## 3. 当前分析版本键与证据

生产发布锚点（总控指定、Git对象核验）：`082be0476c1b68dcc9cd33048d3588ff6346ac96`；本轮不以访问生产进程验证部署。
提示词代码树Git OID：`552db230e735dad8a8646a8cd3fbae8e184fc22e`。
`git ls-tree -r 082be047 -- tradingagents/prompts` 输出去末尾换行后的SHA256：`70219d6adf349097f0b34e230520420f85986679e0664597f0eaf161f3b4574e`。
规范化运行提示词证据采用SHA256(UTF-8 JSON，sort_keys=True, ensure_ascii=False, separators=(逗号,冒号))：

| 证据 | SHA256 | 样本覆盖 |
|---|---|---|
| `.run_identity.prompt_resolved_hashes` | `a9950a9e81f5a18571cd7acb82777922583f147419f97d5499fdd5659d0b781d` | 149 报告，2026-10-08至10-15 |
| `.custom_prompt_snapshot` | `b0ababd6ba2245ace36191d22b0958a41cab8cff0a572b03e5bab4da2d3e33a2` | 149 报告，2026-10-08至10-15 |

运行提示词resolved_hashes覆盖五角色：bull_researcher、bear_researcher、research_manager、trader、risk_manager。报告内custom_prompt_snapshot仅存哈希，本附表不暴露提示词全文/凭据。当前设置表在10-11重建，不能用当前设置代替历史；采用逐报告历史snapshot及run_identity等价证据。

**analysis_version_id（待本SHA签字冻结）**：`AV1|anchor=082be047|contract=b1.v1-T10|resolved=a9950a9e81f5a18571cd7acb82777922583f147419f97d5499fdd5659d0b781d|custom=b0ababd6ba2245ace36191d22b0958a41cab8cff0a572b03e5bab4da2d3e33a2|served=gemini-3.8-flash-n`。

只有分析路径实际改变或served_model改变才切换，其他部署记录release_sha而沿用analysis_version_id。总控已裁10-08以来唯一部署是10-12的65988c79→082be047，属兼容读写改造且view_mismatches=0，不切换版本；部署前后共用本键。10-14、10-15为同版本重启，不算部署。完整清单及裁定说明见§6，不再以缺部署等价裁定阻挡冻结。

## 4. 模型分布及多模型报告

**模型字段已裁定**：版本键的实际服务模型取`llm_call_logs.served_model`；`requested_model`只记录，不参与实际模型判定；`model_name`仅旁证。照既有查询与本条总控裁定登记：10-08以来149份报告、4253次调用served_model全部为`gemini-3.8-flash-n`，多模型报告0。保留下面原分布及采集截点，不为本轮文档修订重新抽样。

查询范围：固定账户报告trade_date>=2026-10-08，JOIN reports.id=llm_call_logs.report_id；本快照实际范围10-08至10-15。包含所有该账户报告，不声称这些都属于正式cohort。149报告（24/29/24/24/24/24），10-09含额外报告需在正式漏斗另排除；此处仅登记全量分布。无日志报告/被删报告不在此分布中，不推断为零。

| 字段 | 值 | 调用数 | 不同报告数 |
|---|---|---:|---:|
| model_name | gemini-3.8-flash-n | 4253 | 149 |
| served_model | gemini-3.8-flash-n | 4253 | 149 |
| requested_model | gemini-3.8-flash-high | 4253 | 149 |

一份报告内多非空served_model：**0**；多model_name：**0**。本次两响应字段全部同值，无空值出现在上述4253行中；请求模型名不同，不当成另一实际响应模型。
后续每报告输出model集合、缺日志/缺响应模型计数和multi_model标记；必做剔除multi_model报告的敏感性，重过横截面与配对门，不另挑模型或加确认检验。本次无多模型，模型剔除不会改变集合；未执行任何收益敏感性运算。

## 5. 补判与P1来源

| 文件 | SHA256 | 行数/大小 |
|---|---|---|
| `/Users/davidliu/ta-p4-2e1e199f/work/phase2-ledger/pit_reattest.jsonl` | `cc7edb21f3976e393606064aa39926a854a5e6ea961cbf2919e26606bc54d641` | 1725行；2085331 bytes |
| `/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/dav-2080-fd1fed5c2507/workdir/out/base_t10/stats.json` | `15f540140dc1f6956af1d595a31efef6360ad199d6b23320de11e9fcba1dd696` | 5473 bytes |

P1选择DAV-2080试跑所建T+10全市场面板的stats.json（不是T+40面板或P4自选样本）。该manifest为来源登记，不意味着本卡重算分区或基率；未来变更必须记偏差及新manifest哈希。补判沿计划账本终态优先、UNVERIFIED查最后adjudicated、no_evidence只诊断；本轮未重生成。

## 6. 部署偏差表与完整文件清单

| 事件 | 前后SHA/清单 | 分析路径结论及证据 | 队列处理 |
|---|---|---|---|
| 10-12：10-08以来唯一部署 | `65988c799d454fc41c12fbc8e5dbe89114511c69` → `082be0476c1b68dcc9cd33048d3588ff6346ac96`；完整清单如下 | 总控已核：shadow_credit.py、historical_cases.py改走result_data_compat_view读取；thinking_cleaner.py规范行写入不再物化horizons别名；新增zstandard依赖；提示词、dataflows/、graph/、LLM配置均未改。B链上线前兼容视图逐行比对view_mismatches=0 | 按总控裁定等价，不切换分析版本 |
| 10-14、10-15 | 同版本重启，不算部署 | 来源为总控10-16裁定；本轮不访问生产进程 | 不切版 |

裁定来源：DAV-2142触发评论 `01a143e1-8527-7bb0-a7df-836b58f61186`（总工转总控10-16三项裁定）。以上运行/比对结论按裁定登记，并非本轮重新执行兼容比对。以下为本轮在本agent检出实跑 `git diff --name-only 65988c79 082be047` 的完整输出（59文件），不以局部目录搜索代替完整清单：

```text
.gitignore
DECISIONS.md
PROJECT_STATE.md
api/main.py
api/services/report_service.py
docs/plans/phase2-midterm/m1-evaluation-criteria.md
docs/research/phase2-midterm/cache-acceptance.md
docs/research/phase2-midterm/chunk-build.md
docs/research/phase2-midterm/p1-report.md
docs/research/phase2-midterm/p1-study-spec.md
docs/ta-snapshot-usage.md
pyproject.toml
requirements.txt
scheduler/main.py
scripts/backfill_tplus5_shadow.py
scripts/dav1507_exit_progress.py
scripts/phase2/README.md
scripts/phase2/build_phase2_chunks.py
scripts/phase2/cache_acceptance.py
scripts/phase2/com.davidliu.ta-p4-ledger.plist
scripts/phase2/daily_snapshot_ledger.py
scripts/phase2/m1_base_rate.py
scripts/phase2/m1_base_rate_bridge.py
scripts/phase2/m1_eval.py
scripts/phase2/m2_periodic_report.py
scripts/phase2/m2_sampler.py
scripts/phase2/p1_stats_layer.py
scripts/phase2/validate_phase2_chunks.py
scripts/ta_snapshot.py
tests/test_dav1480_snapshot_ledger.py
tests/test_dav1506_result_data_compat.py
tests/test_dav1507_exit_progress.py
tests/test_dav1545_single_write.py
tests/test_dav1547_qfq_label.py
tests/test_dav1678_m1_eval.py
tests/test_dav1705_pipeline_drivers.py
tests/test_m1_base_rate.py
tests/test_m1_base_rate_bridge.py
tests/test_m2_periodic_report.py
tests/test_m2_sampler.py
tests/test_provider_resource_cleanup.py
tests/test_report_dual_horizon.py
tests/test_shadow_portfolio.py
tests/test_tplus5_operational_backfill.py
tests/test_trade_execution_rules.py
tradingagents/agents/utils/shadow_credit.py
tradingagents/eval/shadow_portfolio.py
tradingagents/eval/trade_execution.py
tradingagents/knowledge/historical_cases.py
tradingagents/llm_clients/thinking_cleaner.py
tradingagents/storage/__init__.py
tradingagents/storage/result_data_compat.py
uv.lock
work/dav1506-read-points.md
work/dav1507-exit-progress/spec_v4.md
work/dav1705_pipeline/README.md
work/dav1705_pipeline/build_ledger_chunked.py
work/dav1705_pipeline/finalize_trial.py
work/dav1705_pipeline/run_shadow_trial.py
```

该次兼容读写改造的目录命中按总控明确等价裁定不切版；其他未来部署仍按原分析版本规则逐次登记完整改动清单，不把本例外泛化为可任意修改分析路径。

## 7. 验证结果与后续事项

只读采集：collect_freeze_evidence.py退出0，peak footprint=28,410,312 bytes（0.02646 GiB）；逐报告提示词流式哈希inspect_prompt_evidence.py退出0，peak footprint=1,046,250,960 bytes（0.97440 GiB），maximum RSS=1,045,037,056 bytes，均≤4 GiB。0项目tests；无数据库写入、模型/provider调用、生产重启或分析执行。

**三项裁定落文及后续事项**：
1. 跨年9个T+10日期及确认截点按现有日历冻结、官方2027日历公布后机械补登；附新日历SHA256，只填日期不改口径，补登早于其中最早T+10日。
2. 模型字段以served_model为准；149报告/4253调用、gemini-3.8-flash-n、多模型0照登，requested_model仅记录。
3. 10-12唯一部署按总控等价裁定不切版；完整文件清单见§6，10-14/15仅同版重启。

本轮仅上述三类文档修订，不重读数据库、不跑收益分析或项目测试；§1及本节peak数值保留为v0.2采集的历史证据，不冒充本轮实测。封存门、补判顺序、两项Holm、NW60/120取较大p、不可判定线、描述性风格与第20/40日描述快照均不变。后续先做只核改动部分的复审，通过后总控对40位SHA签字即冻结；正式cohort额外报告仍按原漏斗排除，不擅自合入、上线、延长测量或启动M1。
