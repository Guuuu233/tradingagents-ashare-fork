# 高 ABSTAIN 独立诊断与处置

## 已验证环境与边界

- 实查时间：2026-09-18 18:18（UTC+8）；动态批次仍运行，以下数字为冻结截点，不是最终批次总量。
- 生产 SHA：`4b540b0c9b08d77a12ce7d08cfdf0095288cd350`，监听 PID 68658，cwd `/private/tmp/ta-serve-4b540b0`，lsof 确认生产库路径。
- 远端 origin 主线：`10c4c0a3f59448de3bfbb73b1e87d1c470e392f1`；相对生产版本无产品代码变更，增加的是文档与部署脚本/证据。
- 解释器实际输出：`Python 3.10.20`。
- 本轮诊断生产库只读，没有改用户配置、历史报告、guard阈值，没有启动真实分析或重启服务；未强杀当前批次。

## 分母与正式样本

用户贴的731/55/25/2是completed报告分布，不是全库生命周期分布。上海日期“今天”另有126条failed（117条为旧批次no_proxy fail-closed，当前新runner已带该IP，不能当作当前仍未修）。

冻结新批次条件：`created_at >= 2026-09-18 09:49:00 UTC`，已完成20条：19 ABSTAIN + 1 VALID。

真实正式函数 `filter_v2_completed_reports` 输出：

```text
FORMAL_LEDGER raw_count=817 qualifying_v2_count=155 eligible_count=9
non_v2_excluded=662 d009_excluded=146
FORMAL_EXCLUSIONS legacy_null=69 abstain=59 invalid_run=2 data_error=0 no_trade=0 wait=16
COHORT legacy_unversioned=7
COHORT decision_model.v1:evidence_contract.v1:price_basis.unspecified=2
BATCH raw_count=20 qualifying_v2_count=20 eligible_count=0
BATCH_EXCLUSIONS abstain=19 wait=1
```

唯一VALID：`cfd0ffe9116b47d79e684ffe181358b5`，canonical JSON `trade_action=WAIT`，正式排除类型`wait`；DB列为空不改变该判定。因此批次H1b合格净增0，不是5%。当前版本新报告不应计入legacy cohort。

## 新批次根因与旧全库分开

20条均fund-flow selection=`new_algorithm_source_priority`，不是旧字段不可比选择失败。其中18条带manager_consistency_hard_gate；另有1条资金流正文校验阻断。旧32条`incomparable_field_semantics`不能用于解释此批。

正式技术口径：DC net_amount为今日主力净额，THS net_amount为资金净流入，不能跨字段平均；但这不意味着按优先级选定有效DC后也必须弃权。当前selector已有r0_net选中时netamount作旁证的逻辑。

## 可复现缺陷一：去重已处理，却被后置闸当作未裁决

- `research_manager.py:680-696,795-809`：去重guard移除adopted claim，存入结构化excluded_evidence。
- `decision_status.py:426-447,699-732`：decided_cids仅含adopted/partial/rejected，不认合法排除，生成unadjudicated_material_claims_adopt并ABSTAIN。
- 固定输入（2条同event_id，证据全部verified、manager全部采纳）：去重前CONFIRMED；去重后INV-2已经excluded，仍UNRESOLVED。
- 此批7条命中该原因，全部flagged ID都在结构化double_count排除记录中。它不是“经理采纳未核实claim”的同义词。
- 修复不能重新增加重复证据贡献、不能伪造adopt/reject、不能允许任意排除文本绕过PIT/真实缺证。

## 可复现缺陷二：E-04否定/不确定句误报

当前函数把以下三句均作为“已定价肯定断言”拒绝：

- 利好尚未充分定价。
- 目前无法确认利好已定价。
- The benefit is not fully priced in.

真实报告`2d7f66ddf185495f9099ec488916116f`去掉系统告警尾巴后，唯一命中为“但未充分定价低估值…避险虹吸效应”，同样触发该闸。

其他报告确有“已充分定价”肯定断言，不能全放行。修复须句子级区分否定、引述驳回、混合肯定、双重否定，未知证据下肯定断言继续拦截。修掉误拦不保证整份报告变VALID；其他真实闸可能仍在。

## 执行器可见风险

静态展开`/tmp/gen_h1b_detached.py`：队列130项实际只覆盖7只股票，26项日期为周末；归一化前去重+并发非原子检查，已有重复规范股票/日期。DONE只表示调用返回，不代表eligible。不得用这种分布宣称跨60标的分散，也不能按事后赢家或收益挑样本提高通过率。

## 已实际派发

- DAV-1068 资深开发1：两缺陷单一实现者串行修复，分关注点commit。
  run `01a0b407-8acb-7675-88c5-b96123ff8fba` 已回读running。
- DAV-1066 代码审核员：并行只读复核根因与红队覆盖，非候选发布PASS。
  run `01a0b407-8500-7143-9ccc-ce8fcb8344dd` 已回读running。
- DAV-1067 代码运维测试员：批次计数、日期去重、抽样偏差及落库字段一致性只读审计。
  run `01a0b407-85b9-7079-b5fe-074becb1e18f` 已回读running。

当前未产出已验收修复SHA，未合入、未部署。恢复放量条件：修复候选同SHA审查+全量基线对照→独立部署门→一次受控真实报告通过D009且正确cohort计数增加；不能只看completed或VALID标签。

## 复现与资料

执行过：

```sh
env -u PYTHONPATH DATABASE_URL=sqlite:////tmp/ta-abstain-no-write-20260918.db /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python /tmp/ta_abstain_repro_20260918.py
```

退出0，断言确认生命周期冲突。该脚本只作诊断，不是候选修复测试PASS。

冻结脱敏数据：`work/abstain-diagnostic-20260918-1817.json`。

官方字段文档：https://tushare.pro/wctapi/documents/349.md ，https://tushare.pro/document/2?doc_id=348 。

算术纠正：按5%粗略估计，130×5%=6.5，并非20多条；且20条小批次的5%不能当稳定产率，更不能将其当H1b实际合格率。
