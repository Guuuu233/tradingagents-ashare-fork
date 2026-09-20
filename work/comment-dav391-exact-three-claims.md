O2专项当前11项GREEN，但提交前必须纠正以下硬契约：

1. 当前测试名/实现仍写`2 <= len(new_claims) <= 3`；这与单值`battlefield`且每方至少3个不同战场矛盾。B1不设计多battlefield字段，因此v2 Opening必须**恰好3条claim**：`len(new_claims)==3`。
2. 新增明确RED/GREEN：2条claim即使各自覆盖不同合法battlefield，也必须`invalid_protocol`；1/2/4条均拒绝，3条且3个不同合法战场才通过。
3. 更新测试名和错误文案，不保留“2-3条可合法”的误导契约。
4. 上一轮111项legacy矩阵的4失败发生在你继续编辑debate_utils期间；Hermes在当前worktree单独复跑代表性bundle-wash与七报告测试均PASS。O2实现冻结后必须重新完整跑111项，只有稳定终态算证据。
5. 完成上述O2后再进入O3；O3必须覆盖Bear opening最终prompt和Attempt 2 retry：不能出现Bull独特句子/claim ID/summary/current_response，也不能出现“必须respond/target对手”的指令；memory必须n_matches=0或完全不调用。

保持唯一worktree，禁止改legacy fixture、conditional_logic、api/main、主干或服务。暂不提交。