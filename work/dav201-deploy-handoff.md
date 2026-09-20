# DAV-201 宿主部署与三标的 smoke（窄任务）

## 固定输入

- 宿主仓库：`/Users/davidliu/Documents/TradingAgents-AShare`
- 远端交付主干：`target/codex/dav-4-p2a-trunk@c39d975793f907bc21200c7efc5dd2676d2e0833`
- 数据库：`data/tradingagents.db`
- Python：`.venv310/bin/python`，所有命令 `env -u PYTHONPATH`
- 已确认数据库 pending/running reports=0
- 当前宿主本地 checkout 旧 SHA `3c9e650`，tracked `uv.lock` 有用户 WIP，大量 untracked 文件必须保留。

## 任务

1. 对 `uv.lock` tracked WIP 创建可恢复 stash 或 patch 备份；不删除、不 stage untracked 文件。
2. 将宿主当前分支安全 fast-forward 到 `target/codex/dav-4-p2a-trunk@c39d9757`；恢复 WIP，冲突时保留备份、不要强行覆盖。
3. 再查 active reports=0；识别端口8000旧 PID，停止后确认端口释放。
4. 用现有 `.env` 启动，不修改任何 URL/Key/provider/model：
   - `DATABASE_URL=sqlite:///./data/tradingagents.db`
   - 显式 `env -u PYTHONPATH`
   - 保留既有 http/https/all proxy
   - `no_proxy` 必须包含 localhost/私网和 `.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn`
5. 核验新 PID、cwd、实际 DB、HTTP `/healthz` 200、`commit_sha=c39d975...`。
6. 不调用 LLM，运行生产代码三标的 smoke：
   - `000725.SZ` → 消费电子/半导体显示，输出真实 LME铜价、三星电子、缺口、as_of/source/耗时；
   - `300750.SZ` → 新能源车/动力电池，待接入/手动项显式缺失；
   - `600036.SH` → 未映射，必须隔离，不注入产业链段落。
7. 在 DAV-202 评论交付：备份位置、宿主 SHA、PID/cwd/DB、healthz、三标的脱敏结果、明确已上线；真实 LLM 三股回归仍未执行。

## 禁止

- 禁止修改业务代码、测试、`.env`、用户模型/provider/API Key。
- 禁止生成手工报告。
- 禁止删除用户 WIP/untracked 文件。
- 禁止启动 DAV-199/DAV-200。
