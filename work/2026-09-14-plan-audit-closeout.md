# 2026-09-14 计划与施工审计收口

## 审计结论

旧计划没有遗漏一整条 H/D/E 主线，但其中多份快照已把“已合入”“已部署”“真实业务已产生数据”混写。当前应以远端主干、运行态和独立证据为准。

已完成且不应重复派工的代码线：

- E-01/E-02 关系图生产、reducer、消费接线和 DAV-808 custom_prompt 三层守卫；
- E-03 多空研究、证据核验、总监裁决及普通反驳 `WAIT` 修复；
- E-04 预期修正分栏、`cluster_id`、严格日期/时间戳和防伪造 hash；
- P0-B 博弈论接线硬化；P0-C/D 的 ST/新股元数据、动态 OOS 上界和 completeness 口径；
- DAV-887、DAV-889、DAV-895、DAV-898 的前端/Fuyao 窄修。

## 仍然存在的边界

1. 生产图代码已经部署，但本轮没有触发分析；因此仍缺“生产 graph 可达 → trace → 报告字段 → 持久化回读”的业务证据。
2. V-03a 目前只有隔离副本的历史进度基线，`FORWARD_OOS=0`，不能作为正式收益实验或盈利判断。
3. 真实社交 Gate 0–4 仍依赖账号、Cookie、采集和逐级授权，social 保持 disabled。
4. trader/risk_manager 的一手 analyst evidence 仍是设计项；先冻结 bounded summary 和来源规则，再派实现卡。
5. 财务披露日 PIT、`/limit-up-ladder` 和前端 live build/UI smoke 仍未闭环。
6. H1b/信用加权继续 `KEEP_FALSE`；不补写历史样本、不缩短 T+5、不改门槛。

## 本次纠正的口径

- `model_config_snapshot` 应区分“有键 244”和“真正非空 59”，另有 185 个空对象 `{}`。
- `DECISIONS.md` 在当前主干中存在，但旧内容曾落后；`PROJECT_STATE.md` 本次正式纳入主干。文件存在与内容更新是两件事。
- 看板此前逐页完整读取的快照为 `880 total / 801 done / 79 cancelled / 0 non-terminal`；接口出现分页失败时不得把缺页当作空页，重新派工前要重新分页核验。

## 推荐后续顺序

1. 先保持当前 `63d5648` 运行态稳定，持续做只读 health/DB/daemon 观察。
2. 若取得明确的数据写入授权，再做一次受控生产业务路径，专门补 trace/字段/readback 证据；没有授权就不补样本。
3. 对正式 V-03 先补齐到期窗口、provider 元数据、动态完整度和 provenance，再按冻结协议执行。
4. 社交线只在 Gate 0–4 的外部条件到位后继续；不以离线 fixture 冒充真实采集。
5. worktree 和历史工件清理另开只读盘点，之后逐项授权；不得广泛 prune、reset 或删除证据。

## 后续状态回写（2026-09-14）

本文是审计时点快照。其“仍然存在的边界”第 4、5 项随后已完成代码侧收口：P1-D、P1-E 和
P1-F 均已分别通过**代码审核员**同 SHA 复审、同口径全量回归、线性合入；P1-D/P1-E 已随
`026349614a3f1b92a95dc06c0515f10ebec193bc` 发布，P1-F 已随
`6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1` 发布。现在仍缺的是生产业务路径的 trace/report/readback
证据和 P1-F 的真实上游调用证据，不是这些代码线尚未施工。当前快照与后续事实以
`PROJECT_STATE.md` 为准；没有明确的数据写入授权时不补生产样本。
