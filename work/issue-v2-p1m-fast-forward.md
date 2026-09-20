## 固定合入对象

- 目标分支：`codex/dav-4-p2a-trunk`
- 当前目标SHA：`50e115347b49bcb9e767c593296045a356099006`
- 已审核组合分支：`agent/1/6c00ef564d96`
- 已审核最终SHA：`f89b6009544a60727499a02f2e7c585502802801`
- 线性 ancestry：50e1153→33c6e6b→25e52c0→34b1dcf→f89b600。
- DAV-377、DAV-379、DAV-380精确复审PASS。
- 最终全量：`1890 passed, 1 skipped, 0 failed`，宿主 `.venv310`，耗时18:53。
- replay/compileall/diff-check/生产可达smoke通过。
- 无在途报告，账户配置3/1。

## 唯一允许动作

1. 读远端确认 trunk仍严格等于50e1153，候选分支严格等于f89b600。
2. 验证祖先关系可fast-forward。
3. 仅执行：
   `git push target f89b6009544a60727499a02f2e7c585502802801:refs/heads/codex/dav-4-p2a-trunk`
4. 读回目标SHA必须为f89b600。

禁止代码/测试修改、merge commit、force push、rebase、配置/DB修改、服务重启、P1-B施工。若远端trunk已变化，立即BLOCK。

报告push命令结果与读回SHA，明确未重启/未上线。不要 mention 项目调度助手。