# 生产服务 launchd 托管模板（DAV-1790，总控 10-10 令：只交模板，不安装）

本目录交付 `com.tradingagents.ashare.plist` 模板 + `ta-launchd-wrapper.sh` 包装脚本，
用于把现行 `nohup … & disown` 拉起方式切到 launchd 托管。**本令只交模板、不安装**；
安装时间 10-12 窗口之后由总控另定。

以 `releases/RESTART.md` 为现行拉起基线（当前生产 release=`65988c79`，端口 8000）。

## 文件清单

| 文件 | 作用 |
|---|---|
| `releases/launchd/com.tradingagents.ashare.plist` | launchd 作业模板（用户域 LaunchAgents） |
| `releases/launchd/ta-launchd-wrapper.sh` | 包装脚本：限流计数 + 环境固化 + exec uvicorn |
| `releases/launchd/LAUNCHD_SWITCH.md` | 本说明：切换 / 回退步骤 |

## 重启节流机制（同一小时最多 3 次）

- plist 里 `KeepAlive.Crashed=true`：进程**意外退出**（非正常终止）才自动拉起；
  `launchctl bootout` / `kill -TERM` 的干净退出不算 Crashed，不触发拉起。
- launchd 原生 `ThrottleInterval` 只能表达"两次 respawn 至少间隔 N 秒"，
  **无法表达"每小时至多 N 次"**，故限流放包装脚本：
  - 时间戳计数文件 `releases/logs/.launchd/restart-stamps`，保留最近 3600s 内拉起时间；
  - 每次被 launchd 唤起先清窗口外戳、数剩余戳，≥3 次则 `sleep` 到窗口外再 `exit 1`；
    launchd 收到非零退出（非 crash 语义的正常失败）不再 respawn，等待人工
    `launchctl kickstart` 或下一窗口；
  - 机器重启后戳文件可留存，但 launchd 重新 `RunAtLoad`/`kickstart` 时窗口已过期，
    计数自然清零。

## nohup → launchd 切换步骤（待授权后执行）

前置确认：
1. 现行实例为 `releases/RESTART.md` 方式拉起（nohup & disown），端口 8000；
2. `plist`/`wrapper` 中 `RELEASE_SHA`/`RELEASE_DIR`/`VENV_PYTHON`/`LOG_DIR` 与当前生产一致；
3. 已用 `plutil -lint` 校验 plist，`bash -n` 校验 wrapper（见"本地验证"）。

```bash
# 1. 停现行 nohup 实例（RESTART.md"停止"节）
kill -TERM <uvicorn_pid>; sleep 2
lsof -nP -iTCP:8000 -sTCP:LISTEN   # 应无输出

# 2. 装 LaunchAgent（用户域，无需 sudo）
mkdir -p ~/Library/LaunchAgents
cp releases/launchd/com.tradingagents.ashare.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/com.tradingagents.ashare.plist   # OK

# 3. 装载并启动
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.tradingagents.ashare.plist
launchctl kickstart gui/$UID/com.tradingagents.ashare   # 首次运行（RunAtLoad=false）

# 4. 验证（同 RESTART.md"验证"节）
sleep 25
curl -s http://127.0.0.1:8000/healthz   # commit_sha=<sha>、status=ok
launchctl print gui/$UID/com.tradingagents.ashare | head -20   # state=running
```

## 回退（恢复 RESTART.md 手动拉起）

```bash
# 1. 卸载 LaunchAgent（不杀进程本体也可，先禁自动拉起）
launchctl bootout gui/$UID/com.tradingagents.ashare
rm -f ~/Library/LaunchAgents/com.tradingagents.ashare.plist

# 2. 若 uvicorn 仍在跑，按 RESTART.md"停止"节停掉
kill -TERM <uvicorn_pid>; sleep 2

# 3. 恢复手动拉起（逐字执行 RESTART.md"拉起"节）
cd /Users/davidliu/Documents/TradingAgents-AShare-releases/<sha>
nohup bash -c "$(cat /Users/davidliu/Documents/TradingAgents-AShare-releases/logs/launch-<sha>.txt)" & disown
sleep 25; curl -s http://127.0.0.1:8000/healthz
```

回退点、数据库前像、守望 EXPECT_PROD_SHA 的更新口径同 `RESTART.md`，不变。

## 本地验证（安装前）

```bash
plutil -lint releases/launchd/com.tradingagents.ashare.plist   # OK
bash -n releases/launchd/ta-launchd-wrapper.sh                  # 语法 OK
```

## 注意

- 不要 `setsid`（macOS 无此命令）；launchd 本身就是会话外守护，无需 nohup/disown。
- wrapper 里 `exec` 让 launchd 直管 uvicorn 进程本体；不要再去 `&` 后台。
- 切换 release 时同步改 plist 的 `{{RELEASE_DIR}}` 与 wrapper 的 `RELEASE_SHA`，
  并 `launchctl kickstart -k gui/$UID/com.tradingagents.ashare` 重启到新版。
- 安装时间 10-12 窗口之后由总控另定；本令只交模板，不碰生产、不动现有实例。
