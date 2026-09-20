独立复核 PASS。准予合入。

- 候选完整 SHA：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`
- 父提交：`0cc34278c8024680e0b687bd029295876b6e0c98`（T10 tip）
- 分支：`agent/dev2/p2-t11-social-analyst-separation`
- 白名单 8 文件；无 ToolNode/legacy 删除/api 大改/辩论轮次
- active：不注入 `【get_news】`、不直调 `get_news`；legacy 仅 disabled/shadow
- 适配层 `resolve_social_analyst_inputs` 为 human_content / source_mode 唯一分支
- sentinel 互不泄漏；deep_framework 已改四段 + 热度≠看多
- Cursor 隔离 pytest：brief **63 passed**
- **不准予部署**。线性 FF only；禁止 merge。
