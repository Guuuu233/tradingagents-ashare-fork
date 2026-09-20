# DAV-167 主干合入预检

## 固定对象

- 候选：`agent/2/7eb2100b@935c189476d71f513be324e13c26037e29a38e47`
- 目标：`codex/dav-4-p2a-trunk@cb9c62e6be92f18aa15cf5a513f9430d0409bf0b`

## 任务

只读预检，不修改代码、不合并、不推主干：

1. 核验候选远端存在、目标主干为祖先、提交父链完整。
2. 核验只改资金流 provider/evidence/测试的允许范围，无未列出的祖先或无关文件。
3. 确认审核通过后可采用 fast-forward、cherry-pick 还是 merge commit；给出唯一建议及冲突风险。
4. 核验当前服务 PID、数据库文件和是否有 pending/running reports；但审核 PASS 前禁止重启。
5. 准备审核 PASS 后的合入→主干测试→安全重启→固定标的 smoke 清单。

输出必须包含精确 SHA、ancestry 结果、服务/活动任务证据、未合入/未重启/未上线。真实 mention 项目主管。