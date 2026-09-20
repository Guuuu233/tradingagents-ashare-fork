Cursor 独立复审 DAV-503 / P2-T10。

候选 SHA（完整 40 位）：`0cc34278c8024680e0b687bd029295876b6e0c98`
父提交：`46995ac19bb4894dc6cea328f299951eb12698c5`（线性，无 merge）
分支：`agent/dev2/p2-t10-social-toolnode`

隔离 worktree 复跑（宿主 `.venv310`，精确 SHA）：

brief 四套件：**29 passed**；扩跑社交相关：**99 passed**。

契约核对：
- social ToolNode = `ToolNode([])`，无 `get_news`
- news ToolNode 仍含 `get_news` / `get_global_news` / `get_insider_transactions`
- 其它分析师工具集快照未改；无新 social tool；无 `social_data_tools.py`
- 白名单 2 文件；未改 analyst/prompts；未删 legacy

残留（不阻塞）：`api/main.py` 进度文案「读取社交归档」未改（brief 可选）。

**准予合入** `0cc34278c8024680e0b687bd029295876b6e0c98` 到 `codex/dav-4-p2a-trunk`（线性 FF only）。

**不准予部署。** 不要开 Task 11（另卡）。不要删 legacy_proxy。
