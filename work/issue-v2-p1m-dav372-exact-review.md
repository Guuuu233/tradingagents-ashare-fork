## 固定审核对象

- 父候选：`33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`
- 远端分支：`agent/1/01a0323a-dav372`
- 精确 SHA：`8ccd6391404c05d96ff524de8ba5bd02bb371561`
- 注意：开发评论曾误写另一长 SHA；只审核上述由 `git ls-remote` 读回的精确 SHA。
- 严格只读，禁止修改代码、测试、主干、服务、DB和配置；禁止跑全量测试。

## 必审项

1. ancestry：直接父提交必须为 `33c6e6b`；changed files恰好4个：debate_metrics、其测试、offline_ab_harness、其测试。
2. 复现 DAV-371 三个阻断：
   - 真实 Golden `debate_round/message_index` 下回收率 denominator > 0；
   - 股票代码/日期/INV/CH 不进入数字事实，区间不拆成两个事实；
   - probability/HOLD合法白名单 note计入 contract completeness，任意 note 不得洗白。
3. 数值提取不能因去污染误删正常财务日期相关数值（例如10年期收益率中的10年）或正负号；手工复算。
4. S1轮次优先级、非法输入 typed no_data；不能把缺失轮次默认成首轮。
5. S3 numerator/denominator/rate数学一致，`present_fields/missing_fields/legitimate_omissions`互斥且覆盖4字段。
6. A/B标签明确 structural compatibility，不能暗示同数据是实际v2质量。
7. 复跑宿主 `.venv310`：两份定向测试、Golden复现摘要、compileall、diff-check、replay；不跑全量。
8. 0 code changes；输出 PASS/BLOCK、文件:行号和真实命令结果。明确未合入/未重启/未上线。

不要 mention 项目调度助手。