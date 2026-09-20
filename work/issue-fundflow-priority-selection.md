# DAV-179 资金流优先级选择：取消多源共识门

## 用户最新产品决策（覆盖旧规则）

取消“至少两个真正可比的新算法来源才允许方向”的多源共识计划。

新规则：

1. 新算法来源按明确优先级选择首个有效、日期和字段语义合格的数据；单一高优先级新算法源即可输出该来源自身的资金流方向，不再因为 `insufficient_sources` 阻断。
2. 建议默认优先级：
   - 东方财富历史结构化数据（`eastmoney_direct` / Tushare `moneyflow_dc`）；
   - 同花顺历史结构化数据（Tushare `moneyflow_ths`；仅在字段语义明确时用于对应方向）；
   - 其他经过验证的新算法来源。
3. 只有全部新算法来源失败、日期不匹配或字段不可用时，才使用新浪 Web `legacy_web_algorithm`；必须醒目标注 legacy/旧算法/仅供参考。
4. 新浪 legacy 不与新算法数据平均，不覆盖任何新算法结果。
5. 不同字段、日期、窗口、单位仍不得混算：
   - DC `net_amount`、THS `net_amount`、THS `net_d5_amount` 和 EM `r0_net` 分别保留真实语义；
   - 不能为了方向一致而伪造可比性；
   - 但“不同比较”不再意味着必须阻断已选高优先级来源自身的方向。
6. 多源结果可以展示为旁证/差异说明，但不再是方向输出的前置硬门。

## 固定基线和依赖

- target trunk：`codex/dav-4-p2a-trunk@21f58832b270917e540afa157ffd2daffa5b6e3f`
- DAV-178 候选：`agent/2/e88dbf41@9bbaad6d29bb23170deb8469c778e3a75877ed87`
- 必须等待 DAV-178 精确 SHA 审核终态后，从审核确认的最新远端 SHA 施工；禁止与 DAV-178 同时修改同一 provider/evidence 文件。

## 实现目标

将资金流决策从“多源 median/MAD consensus 硬门”改为“确定性来源优先级选择”：

- 新增/调整纯函数，输入结构化 records/metadata，输出：
  - `selected_source`
  - `selected_source_family`
  - `selected_algorithm_group`
  - `selected_field`
  - `selected_value` / `selected_direction`
  - `selection_reason`
  - `fallback_rank`
  - `legacy_reference`
  - `direction_allowed`
- 首个满足日期、字段、单位和有效值校验的新算法来源即被选择，`direction_allowed=true`。
- 只有没有可用新算法来源时：若新浪 legacy 可用，返回 legacy reference，但明确 `legacy_web_algorithm=true`、`selection_reason=no_new_algorithm_source_legacy_fallback`；是否允许模型基于 legacy 给方向必须按用户规则实现为“可以展示其来源自身方向，但必须醒目标注旧算法”，不得冒充新算法。
- 缺所有来源时才返回真正的数据缺口/blocked。

## 必须修改的消费链

检查并调整：

- `fund_flow_evidence.py` 中 `insufficient_sources`、`build_consensus_evidence` 或其替代选择逻辑；
- `cn_akshare_provider.py` metadata/guard；
- `smart_money_analyst.py` 的 guard 与正文约束；
- Research Manager / Trader / Risk Manager 不能再因“只有一个新算法来源”阻断整个投资计划；
- 报告结构中保留 source/as-of/field/unit/algorithm_group/legacy 标记。

不要删除历史证据、失败链或字段语义校验；只取消“来源数量”和“多源共识”作为方向前置条件。

## 测试

至少覆盖：

1. 仅 Eastmoney direct 有效 → 选择 Eastmoney，方向允许；
2. Eastmoney 失败、THS/Tushare 有效 → 选择 THS，方向允许；
3. Eastmoney 与 THS 均有效但数值冲突 → 按优先级选择 Eastmoney，THS 作为旁证，不阻断；
4. 新算法全部失败、Sina legacy 有效 → 使用 Sina，醒目标注 legacy；
5. 全部失败 → 数据缺口 blocked；
6. 日期不匹配、未来数据、单位或字段非法 → 跳过该来源继续下一优先级；
7. 单一新算法来源下 Research/Trader/Risk 不再被 guard 阻断；
8. 报告保留 source/field/unit/as-of/legacy/fallback rank。

使用 `.venv310` 跑定向与相关全量测试、compileall、git diff-check；推新远端 branch/SHA，进入独立复审。不得修改用户模型/provider/API Key，不直接合入/重启/上线，评论不要 mention 项目调度助手。
