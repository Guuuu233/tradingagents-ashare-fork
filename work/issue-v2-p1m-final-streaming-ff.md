## 固定合入对象

- 目标分支：`codex/dav-4-p2a-trunk`
- 当前远端trunk：`f89b6009544a60727499a02f2e7c585502802801`
- 已审核候选分支：`agent/2/dav384-semantic-repair`
- 最终精确SHA：`153fc9bad03c60744407c85a61a7aa52069c84e9`
- 线性链：f89b600→cfaa4b2→3a4cab0→153fc9b；f89为候选祖先，可fast-forward。
- 相对trunk变更恰好2文件：api/main.py、tests/test_debate_state_persistence.py。
- DAV-385精确复审PASS；Hermes两条语义探针PASS。
- 宿主`.venv310`最终全量：1894 passed, 1 skipped, 0 failed，19:37。
- 无在途报告，账户3/1。

## 唯一允许动作

1. 读远端确认trunk仍为f89b600、候选仍为153fc9b。
2. 再验祖先关系。
3. 仅执行：
   `git push target 153fc9bad03c60744407c85a61a7aa52069c84e9:refs/heads/codex/dav-4-p2a-trunk`
4. 读回trunk必须为153fc9b。

禁止代码/测试修改、merge commit、rebase、force push、配置/DB、服务重启、P1-B。若trunk变化，立即BLOCK。

报告命令结果与读回SHA；明确未重启/未上线。不要mention项目调度助手。