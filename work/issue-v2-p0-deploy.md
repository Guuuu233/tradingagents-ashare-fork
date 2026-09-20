## 固定状态

- 项目根：`/Users/davidliu/Documents/TradingAgents-AShare`
- 远端目标主干：`target/codex/dav-4-p2a-trunk@23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 宿主 checkout：`45821dd4f21a5f65578dbf54f5d916970ae835c0`
- 当前服务：PID `11788`，`/healthz.commit_sha=45821dd...`
- DB：`data/tradingagents.db`
- 当前 reports 无 pending/running；真实用户配置仍 3/1
- 宿主存在大量 untracked，特别是 `tests/golden/` 会与新主干新增 tracked 文件冲突，必须备份移走，禁止删除

## 任务

由代码运维测试员执行 Phase 0 安全部署。不得修改代码、数据库数据、用户模型/providers/role bindings/API Key；不得执行业务 Phase 1。

### A. 更新宿主 checkout

1. 再次核验 reports 无 `pending/running/processing/in_progress`；若出现，BLOCK，不重启。
2. 记录旧 PID、旧 health SHA、cwd、打开的 DB。
3. 创建带时间戳的备份目录（如 `/tmp/ta-pre-23e09e5-YYYYMMDD-HHMMSS/`），将会阻塞 checkout 的未跟踪 `tests/golden/` **完整移动**到备份目录；记录备份绝对路径、文件数和大小。不得删除。其他 untracked 不动，除非 checkout 明确报冲突，同样只备份移动。
4. 使用代理可用环境 fetch：`https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897 git fetch target codex/dav-4-p2a-trunk`。
5. 在当前 `codex/dav-4-p2a-trunk` 执行 `git merge --ff-only target/codex/dav-4-p2a-trunk`；禁止 reset --hard、force、merge commit、删除 untracked。
6. 核验本地 HEAD 精确等于 `23e09e5...`，新 tracked `tests/golden/audit_20260823/replay_verifier.py` 存在；`git status --short` 只允许原有 untracked，不允许 tracked 修改。

### B. 安全重启

1. 再查在途报告为 0。
2. 终止旧 PID 11788；先 TERM，短时未退出则 KILL；必须用 `lsof -nP -iTCP:8000 -sTCP:LISTEN` 确认端口为空。
3. 从项目根启动持久服务，环境必须含：
   - `env -u PYTHONPATH`
   - `DATABASE_URL=sqlite:///./data/tradingagents.db`
   - `http_proxy=http://127.0.0.1:7897`
   - `https_proxy=http://127.0.0.1:7897`
   - `all_proxy=socks5://127.0.0.1:7897`
   - 完整 `no_proxy=localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn`
   - `.venv310/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000`
4. 服务必须持久化存活并有明确 PID/日志路径；不要用会随 agent turn 退出而被回收的临时后台任务。若无法保证持久化，BLOCK 并保留端口空闲，交 Hermes 启动。

### C. 启动后验签

- 新 PID != 11788，且拥有 8000；
- cwd 为项目根；解释器为 `.venv310`；打开 DB 为项目 `data/tradingagents.db`；进程环境含 DATABASE_URL 与完整 no_proxy；
- `/healthz` 和 `/api/health` HTTP 200 JSON，`commit_sha=23e09e5...`；
- DB 真实用户仍 3/1，无在途报告；
- 不输出任何凭据。可做未登录 401/脱敏结构检查，但真实登录 smoke 由 Hermes 后续执行。

## 交付

报告备份路径/数量、旧新 PID、local/trunk/service SHA、端口、cwd、DB、环境验签、health JSON 摘要。明确已合入、已重启、运行代码已上线；真实业务 smoke 未完成。评论不要 mention 项目调度助手，直接交付 Hermes。