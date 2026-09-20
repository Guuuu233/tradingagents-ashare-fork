## 固定合入对象

- 目标分支：`codex/dav-4-p2a-trunk`
- 当前trunk：`153fc9bad03c60744407c85a61a7aa52069c84e9`
- 候选分支：`agent/2/e9e0a4bdc213`
- 精确SHA：`ccc4c53a4985f8db32353586e6cb4317aa34f8cd`
- ccc直接父为153，可fast-forward；相对父恰好2文件。
- DAV-388精确复审PASS；HOLD多场景探针PASS。
- 宿主`.venv310`最终全量：1899 passed, 1 skipped, 0 failed，27:54。
- 无在途报告，3/1不变。

## 唯一动作

1. 读远端确认上述两个SHA未变化。
2. 验祖先关系。
3. 执行：`git push target ccc4c53a4985f8db32353586e6cb4317aa34f8cd:refs/heads/codex/dav-4-p2a-trunk`
4. 读回trunk必须为ccc4c53。

禁止代码/测试修改、merge commit、rebase、force、配置/DB、重启、P1-B。若trunk变化立即BLOCK。报告读回SHA，明确未重启/未上线。不要mention项目调度助手。