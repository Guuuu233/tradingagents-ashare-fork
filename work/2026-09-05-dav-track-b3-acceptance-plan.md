# Track B-3：真实采集 / shadow / canary 验收方案（不执行启用）

**基线：** `origin/codex/dav-4-p2a-trunk` @ `4fdcf8efa841c2a881d82429febd48578d544c94`（B-1+B-2 已合入；开工再 `git fetch` 核 tip）。  
**允许改：** `docs/social_data/` 下验收清单（优先改 `implementation_plan.md` 完成态表，或新增同目录单一 checklist，禁止平行第二套设计）。测试仅当锁住「方案文档存在且门槛数字未被改低」时才加。  
**解释器：** `env -u PYTHONPATH .venv310/bin/python`  
禁止 push 主干、真采集、切 mode、部署、改账号。完成后 40 位 SHA，`in_review`。  
**本卡只出方案与检查清单，不启动爬虫、不切 active、不部署。**

承接原计划门槛，不降低：

- Gate0：钉 SHA 的 MediaCrawler；sqlite 工作库；xhs/dy 各至少一轮导入且 archive 行增加、旧 snapshot 不被 UPDATE  
- Gate2：shadow；30 份 / 10 只股票人工覆盖；`social_data_context` 可追溯；`direction_allowed=false` 不得当方向证据  
- Gate3：2–5 只 canary active（**执行需另授权**）  
- 历史无当时快照 → 标缺失，禁止用当天新采回填  

交付：`docs/social_data/` 或本卡评论中的验收清单，列 Gate 各项 **代码已交付 / 真实未做**。需要登录、真采集或切 mode 时，单独列出待授权项。
