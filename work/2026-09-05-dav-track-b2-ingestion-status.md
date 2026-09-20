# Track B-2：受控采集入口 + 状态分项诚实化

**依赖：** Track B-1 已合入。  
**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` @ `418a310259b5269fe38ce45efdadd6b3c9f360bd`（开工再核 tip）。  
**权威：** `docs/social_data/implementation_plan.md` Task 13；`docs/social_data/runbook.md`；`work/2026-09-05-track-b-remaining.md`。  
**解释器：** `env -u PYTHONPATH .venv310/bin/python`

`TA_SOCIAL_MODE=disabled` **不**表示外部采集必须停止。本卡要能证明：采集成功、导入成功、新鲜度、分析可用性是四件不同的事。

隔离分支，例如 `agent/dev2/track-b2-ingestion-status`。禁止 squash 无关改动进一次提交。禁止 push 主干。

## 允许改

- `scripts/run_social_ingestion.py` 及测试
- `api/services/social_data_service.py`、相关 status API 测试
- `docs/social_data/runbook.md`（只补启动与状态语义）

## 契约

1. 对照**真实** MediaCrawler 在钉住 SHA 上的启动接口（CLI/控制面）做受控执行：loopback、`save_option=sqlite`、工作库表校验、并发锁。禁止「加一个任意 argv 列表」冒充完成。本卡**不**要求真的打外网；实现后用 mock 对真实接口形状测通。真采集另授权。
2. 分别报告：采集进程结果、导入行数、归档新鲜度、分析端是否可用（mode/bundle）。禁止仅凭 archive 文件存在返回 `operational`。
3. `record_social_run_summary` 必须有真实调用方，或改为读导入记录表；禁止只写内存缓存充数。
4. 检查路径：采集任务、源库、导入记录。交付评论写清查了哪些路径、结果（无密钥）。

禁止：部署、active、扩大采集范围、读真实 Cookie 进仓库/测试断言、改账号配置、开加权、改 `news_event_evidence.py`。

完成后：功能分支 + 完整 40 位 SHA + pytest 精确数字；状态 `in_review`。不要自行 FF。
