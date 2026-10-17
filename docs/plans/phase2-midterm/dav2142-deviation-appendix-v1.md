# DAV-2142｜偏差附表 v1

本附表按 DAV-2193 的总控 2026-10-17 令新增，登记 DAV-2186 的最终裁定及总控 10-16 已裁的 10-12 部署等价说明。**候选文档，待总控对交付的40位提交 SHA 签字。** 本附表不覆盖、不修改 `dav2142-phase1-preregistration-v0.2.md` 和 `dav2142-freeze-register-v0.2.md`，不修改账本，不授权分析运行。

## 1. DAV-2186 裁定：自定义提示词及历史记录

固定账户：`429163f7-50b6-4982-8bdf-96ae99506843`。

**2026-10-11 07:31:06 UTC 的 `user_custom_prompts` 整组重建，判为记录级重建、内容字节等价，版本键不切换。2026-10-08 至 10-16 的记录归入冻结登记的版本键，标注 `custom_basis=report_snapshot`。** 10-11 重建只作为旁证事件，不以当前配置表的更新时间替代逐报告历史证据，不采用候选意见中的 `custom_basis=config_audit`。

DAV-2186 已交付的只读证据为：

- 自定义提示词正文存于 `user_custom_prompts.prompt_text`；固定账户该表1条 global 记录，`created_at` / `updated_at` 均为 `2026-10-11 07:31:06.608197 UTC`。`replace_custom_prompts` 的整组删除后重插语义会重建行 ID 和时间戳，即使正文未变。
- 行内 `prompt_hash` 及正文 SHA256 前12位均为 `5489166b29c2`。五个注入角色的报告 `resolved_hash` 均同值。
- 173份逐报告 `custom_prompt_snapshot` 的规范化 SHA256 全部为 `b0ababd6ba2245ace36191d22b0958a41cab8cff0a572b03e5bab4da2d3e33a2`。规范化方式为 UTF-8 编码的 `json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))`。
- 按 `trade_date` 覆盖 10-08、10-09、10-12、10-13、10-14、10-15、10-16，分别24、29、24、24、24、24、24份。此前登记为149份（至10-15），本裁定的173份新增覆盖10-16的24份；并非改写冻结登记的原采集样本数，也不表示173份全为正式 cohort。

沿用的完整 `analysis_version_id`：

```text
AV1|anchor=082be047|contract=b1.v1-T10|resolved=a9950a9e81f5a18571cd7acb82777922583f147419f97d5499fdd5659d0b781d|custom=b0ababd6ba2245ace36191d22b0958a41cab8cff0a572b03e5bab4da2d3e33a2|served=gemini-3.8-flash-n
```

来源：DAV-2186 只读交付评论 `01a14882-0276-736f-91c3-19496e712d9e`；最终口径以总工登记的总控裁定评论 `01a148b0-4b06-796d-a14f-18938a1b7aa6` 为准。本次仅据交付及裁定落文，未重新读取数据库或重算173份报告哈希。

## 2. 10-12 部署等价说明

**按总控 10-16 裁定，10-08 以来唯一部署为 10-12 的 `65988c799d454fc41c12fbc8e5dbe89114511c69` → `082be0476c1b68dcc9cd33048d3588ff6346ac96`，不切换分析版本，部署前后沿用§1版本键。10-14、10-15为同版本重启，不算部署。**

总控已核的分析路径相关变化：

| 路径或依赖 | 变化及等价依据 |
|---|---|
| `tradingagents/agents/utils/shadow_credit.py` | 改走 `result_data_compat_view` 读取 |
| `tradingagents/knowledge/historical_cases.py` | 改走 `result_data_compat_view` 读取 |
| `tradingagents/llm_clients/thinking_cleaner.py` | 规范行写入时不再物化 horizons 别名 |
| `zstandard` | 新增依赖 |

提示词、`dataflows/`、`graph/`、LLM配置均未改动。B链上线前兼容视图逐行比对 **`view_mismatches=0`**。这些是总控已核的裁定依据，本次未重新运行兼容比对、检查生产进程或执行部署，不将上述局部分析路径说明冒充全部文件清单。

裁定来源：DAV-2142 总工转总控10-16三项裁定评论 `01a143e1-8527-7bb0-a7df-836b58f61186`。本次在独立 agent 检出实跑 `git diff --name-only 65988c79 082be047`，完整输出如下，共59个文件：

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

上述等价裁定仅适用于本次部署；后续分析路径或实际服务模型改变仍依冻结规则处理，不因本条免除版本复核。

## 3. 快照复核口径

**复核一律以「带日期的 zst」的 SHA256 为准；`current.db` 的 SHA256 只在当日有效，不作复核依据。** 不以当前滚动文件的现值否定历史登记，也不将不同日期快照的报告数混作同一次采集。

冻结登记文件§1对应 **10-15重出的 zst**：

| 对象 | 复核指纹或大小 | 证据来源 |
|---|---|---|
| `tradingagents-2026-10-15.db.zst` | SHA256 `979adba9f8609859d68f524f948091fad4214271b0ff769bf4718bf4759928ce`；128,352,744 B | 总控10-17裁定；本次对现存带日期压缩文件只读计算完整SHA256并核对大小 |
| 该 zst 解压出的 `current.db` | 6,337,847,296 B | 总控10-17裁定；本次未解压或开库 |

此处的「§1对应」指冻结登记 `dav2142-freeze-register-v0.2.md` 的§1，不将10-15压缩包称为DAV-2186覆盖至10-16的173份报告核查快照。DAV-2186曾实读滚动 `current.db` 的哈希属于当时采集记录，不用于替代本节的日期压缩包复核口径。裁定来源同 DAV-2186 评论 `01a148b0-4b06-796d-a14f-18938a1b7aa6`。

## 4. 本次核验与待签事项

本次仅新增本Markdown附表，0项目tests、0数据库读取、0账本写入、0收益/IC/归因分析、0模型/provider调用。核验范围为：部署完整清单与Git输出逐字一致；日期zst完整SHA256与大小；两个冻结文件相对本次基线字节不变；提交仅含本文件；`git diff --check`。远端分支及40位提交SHA由DAV-2193交付评论登记。

待总控对交付SHA签字。未合入、未重启、未上线，未解锁下游分析；不得将本候选文档提交或已裁等价结论当作新增分析运行授权。
