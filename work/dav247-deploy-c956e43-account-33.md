# DAV-247 部署 c956e43 并正确账户 3/3 复验

## 精确门闩

- 远端主干必须已是：`target/codex/dav-4-p2a-trunk@c956e43db0fea1fea88b85103d10218734b2b1c8`
- 父链：`394e3ef → 825b0cc → be009f3 → c956e43`
- 当前宿主 healthz 仍为 `394e3ef`，active reports=0。本卡目标是把宿主切到 `c956e43` 后做真实账户复验。
- 禁止合入其他 SHA；禁止回退到 `6a8a896b`/`7ef89f63`/`2d7bc5b8`/`419dd337`。

## 保护边界

1. 不得 `reset --hard`、`clean -fd`、覆盖用户 `uv.lock`、改 `.env` 凭据值。
2. 宿主存在用户未提交 WIP 时先安全保护（stash 或移走备份），再 fast-forward 到 `c956e43`；完成后恢复 WIP。
3. 不得修改用户模型、角色绑定、providers、API Key、持久辩论轮次。
4. 用户持久配置必须保持：`max_debate_rounds=3`、`max_risk_discuss_rounds=1`。风险 3 轮只允许单次请求 `config_overrides`，事后核验库内仍为 3/1。
5. 指定账户：`davidliu022305@gmail.com` / `user_id=429163f7-50b6-4982-8bdf-96ae99506843`。禁止 `local-default-user`。

## 部署步骤

1. 再次 `git ls-remote target refs/heads/codex/dav-4-p2a-trunk`，确认仍是 `c956e43`。
2. 查 `SELECT COUNT(*) FROM reports WHERE status IN ('pending','running')`，必须为 0。
3. 保护 WIP 后，将宿主工作树 fast-forward 到该 SHA（不要碰 `.env`/`uv.lock` 用户改动）。
4. 记录旧 PID；`kill -9` 后 `lsof -iTCP:8000` 直到空。
5. 用完整命令重启（必须 `env -u PYTHONPATH`、显式 `DATABASE_URL=sqlite:///./data/tradingagents.db`、完整国内 `no_proxy`）：

```bash
env -u PYTHONPATH DATABASE_URL='sqlite:///./data/tradingagents.db' \
  http_proxy='http://127.0.0.1:7897' https_proxy='http://127.0.0.1:7897' all_proxy='socks5://127.0.0.1:7897' \
  no_proxy='localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn' \
  NO_PROXY='localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn' \
  .venv310/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

6. 验收部署：
   - 新 PID 监听 8000；
   - cwd 为宿主项目根；
   - `lsof` 打开的 DB 是 `data/tradingagents.db`；
   - `/healthz` 的 `commit_sha` = `c956e43db0fea1fea88b85103d10218734b2b1c8`；
   - integrity_check ok。

## 正确账户 3/3 复验

部署通过后，用指定账户登录态对 **招商银行 600036.SH** 发起一次普通单 horizon 分析：

- 请求必须带 `config_overrides`: `max_debate_rounds=3`, `max_risk_discuss_rounds=3`；
- 不得写回用户持久配置；
- 完成后立即核验：
  - `user_id` 正确；
  - `status=completed`（失败则记录精确 error，不得伪造成功）；
  - `investment_debate_state.count == 6`（Bull/Bear 各 3）；
  - `risk_debate_state.count == 9`（三类风险各 3）；
  - history / claims / judge / feedback 完整；
  - 无残留被拒机读块；
  - 资金流 `source/field/window/as_of/fallback_rank` 可审计；
  - 库内用户配置仍为 3/1。

旧报告 `dd2d5cf2`（count=0/0）及更早 2/1 报告一律不能当验收。

## 交付

- 部署前后 PID、healthz SHA、DB 路径；
- 新 report_id 与结构化辩论 count；
- 明确未解锁 DAV-199/DAV-200（必须本卡 3/3 落库证据通过后才解锁）；
- 不得 @项目调度助手。立即执行。
