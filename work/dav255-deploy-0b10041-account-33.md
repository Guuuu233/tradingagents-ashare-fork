# DAV-255 部署 0b10041 并正确账户 3/3 复验

主干已独立核验：`target/codex/dav-4-p2a-trunk@0b10041f9e68b5d0116b76c36cd629acb365f10d`
父链：`c956e43 → 77cd6ac`（DAV-249）`→ 0b10041`（DAV-248）

当前宿主 healthz 仍为 `c956e43`，PID 在听 8000，active reports=0。

## 保护边界

1. 不得 `reset --hard`、`clean -fd`、覆盖用户 `uv.lock`、改 `.env` 凭据。
2. 宿主 WIP 先 stash 或备份，再 fast-forward 到 `0b10041`；完成后恢复 WIP。
3. 不得改用户模型、角色绑定、providers、API Key、持久辩论轮次。
4. 持久配置必须保持：`max_debate_rounds=3`、`max_risk_discuss_rounds=1`。风险 3 轮只允许单次请求 `config_overrides`。
5. 指定账户：`davidliu022305@gmail.com` / `user_id=429163f7-50b6-4982-8bdf-96ae99506843`。禁止 `local-default-user`。

## 部署

1. 再确认远端主干仍是 `0b10041`。
2. `SELECT COUNT(*) FROM reports WHERE status IN ('pending','running')` 必须为 0。
3. 保护 WIP 后宿主 fast-forward 到该 SHA。
4. 记录旧 PID（当前约 36033）；`kill -9` 后 `lsof -iTCP:8000` 直到空。
5. 用完整命令重启：

```bash
env -u PYTHONPATH DATABASE_URL='sqlite:///./data/tradingagents.db' \
  http_proxy='http://127.0.0.1:7897' https_proxy='http://127.0.0.1:7897' all_proxy='socks5://127.0.0.1:7897' \
  no_proxy='localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/16,192.168.0.0/16,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn' \
  NO_PROXY='localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/16,192.168.0.0/16,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn' \
  .venv310/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

6. 验收：新 PID、cwd=宿主根、打开 `data/tradingagents.db`、healthz `commit_sha=0b10041f9e68b5d0116b76c36cd629acb365f10d`。

## 正确账户 3/3

部署通过后，用指定账户登录态对 **招商银行 600036.SH** 发起一次普通单 horizon 分析：

- `config_overrides`: `max_debate_rounds=3`, `max_risk_discuss_rounds=3`
- 不得写回持久配置
- 完成后核验：user_id 正确；失败则 `status=failed` 且 error 精确（DAV-249）；completed 时 `investment_debate_state.count==6` 且 `risk_debate_state.count==9`；资金流 selection 不得因合法 r0_net 被误阻断（DAV-248）；库内仍为 3/1

旧报告 `cc3b55a8` / `dd2d5cf2` 及更早 2/1 一律不能当验收。

不得 @项目调度助手。立即执行。
