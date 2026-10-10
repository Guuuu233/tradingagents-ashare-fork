# 生产服务 launchd 托管模板（DAV-1790，DAV-1800/DAV-1802 返修；总控 10-10 令：只交模板，不安装）

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

## RESTART.md 基线说明（🟡-4 订正）

`releases/RESTART.md` 是**宿主机侧文件**（`/Users/davidliu/Documents/TradingAgents-AShare-releases/RESTART.md`），
**未纳入 git 版本控制**（`git log --all -- releases/RESTART.md` 为空）。模板中
"与 RESTART.md 一致"的基线指该宿主机文件，无法在本仓库 PR 中 diff 校验；
其同步方式为总控运维流程下的宿主机手工维护，切换/回退时以宿主机现行版本为准。
同理 `releases/logs/launch-<sha>.txt` 留档启动令亦在宿主机日志目录。

## 重启节流机制（同一小时最多 3 次）

- plist 里 `KeepAlive={"SuccessfulExit": false}`：**非零退出与信号退出**都自动拉起，
  SIGKILL（`kill -9`）与 macOS OOM killer 覆盖在内（实测 `KeepAlive.Crashed` 对
  SIGKILL/TERM/INT/`exit 1` 均不重拉，已弃用）；`launchctl bootout` 卸载不算退出，不误拉。
- launchd 原生 `ThrottleInterval`（默认 10s）只能表达"两次 respawn 至少间隔 N 秒"，
  **无法表达"每小时至多 N 次"**，故限流放包装脚本：
  - 时间戳计数文件 `<RELEASES_ROOT>/logs/.launchd/restart-stamps`（绝对路径），
    保留最近 3600s 内拉起时间；**每次被 launchd 唤起先写戳再做任何断言**——
    DAV-1802 🔴-1 修复：戳记录提前到所有前置断言之前，成功与失败路径同等计入，
    确保 logs/ 不可写等早期 die 也占用额度、最多 3 次后 exit 0 停手；
  - **logs/ 不可写兜底**：主戳目录不可建/不可写时退化 `/tmp/ta-launchd-die-stamps`
    （DAV-1802 新增兜底目录），同窗口同上限；连兜底也不可得时 die 改 exit 0
    （不再重拉，仅 StartInterval 探测），杜绝无界循环；
  - **额度语义**：3 次额度计入**所有**唤起——含计划内首启动、KeepAlive 重拉与
    人工 `kickstart`，即首启动占 1 次、真实崩溃预算只剩 2 次；
  - **人工旁路（DAV-1802 🔴-2 修订）**：`launchctl kickstart` 无 `-e` 传 env 能力
    （macOS 27.x `kickstart` 仅支持 `[-k] [-p] [-s]`，`-e` 静默 no-op），
    故旁路改走**受控哨兵文件**——创建 `<LOG_DIR>/.launchd/manual-bypass` 后
    `launchctl kickstart gui/$UID/com.tradingagents.ashare`，wrapper 检测到哨兵即
    跳过计数与上限判定直接拉起（一次性，读取后删除）；注意对 running 实例
    `kickstart` 是 no-op，须先 `bootout`/`kickstart -k` 或等其退出；
  - **超限行为**：达 3 次后 wrapper 打含恢复动作的诊断日志（fd2 + `wrapper-diagnostic.log`
    双写）并 **`exit 0` 快速停手**——exit 0 属"成功退出"，`SuccessfulExit=false` 不 respawn，
    不挂 `sleep`、job 不驻留 `running` 假象；
  - **自动恢复**：`StartInterval=600` 周期探测，窗口过期（最旧戳 >3600s）后
    下一次唤起自然拉起，恢复粒度 ≤600s；人工拉起须配合旁路哨兵（见上）；
  - **fail-closed**：写戳失败、锁 5s 拿不到、release 目录/解释器缺失，一律
    错误日志 + `exit 1` 拒绝拉起——因戳已先写，KeepAlive 重拉 ≤2 次后触顶
    exit 0 停手，"同一小时最多 3 次"对失败路径同样成立；
  - **并发保护**：戳文件读-改-写在 `mkdir` 原子锁（macOS 无 `flock`）内进行，
    防 `kickstart` 与自动重拉并发双写丢戳；>60s 残留锁目录视为死锁自动清除；
  - 机器重启后戳文件可留存，窗口过期计数自然清零；但 3600s 内反复崩溃
    （含挂起/唤醒循环重启）会跨机器重启累计。

## 日志与轮转

| 日志 | 来源 | 轮转 |
|---|---|---|
| `logs/uvicorn-<sha>.log` | wrapper `exec` 内 `>>` 追加（与现行一致） | 沿用宿主机现行外部轮转 |
| `logs/wrapper-diagnostic.log` | wrapper 限流/前置断言/拉起记录（追加式） | 见下 |
| `logs/launchd-stdout.log` / `logs/launchd-stderr.log` | plist Standard{Out,Error}Path（append，**无上限**） | 见下 |
| `logs/.launchd/restart-stamps` | wrapper 唤起计数戳（logs/ 不可写时兜底 `/tmp/ta-launchd-die-stamps`） | 窗口滑动自清，无需轮转 |
| `logs/.launchd/manual-bypass` | 人工旁路哨兵（DAV-1802 🔴-2） | 一次性，wrapper 读取后自删 |

`launchd-*.log` 与 `wrapper-diagnostic.log` 均为 append 无内置上限，采用
`newsyslog` 轮转（macOS 原生）。安装时在 `/etc/newsyslog.d/` 加一条（需 sudo，
仅安装窗口执行一次）：

```
# logfilename                                    [owner:group] mode count size(kb) when  flags
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/launchd-stdout.log   davidliu:staff 644 5 1024 * Z
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/launchd-stderr.log   davidliu:staff 644 5 1024 * Z
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/wrapper-diagnostic.log davidliu:staff 644 5 1024 * Z
```

（>1MB 轮转、保留 5 代、gzip 压缩；未配置前文件随唤起线性增长，低频可接受，
但**必须在切换文档中登记**，故入册。已用 `newsyslog -vnrs -f` dry-run
校验通过：`davidliu:staff` 属主解析正确、三条配置输出 `does not exist, skipped`
即语法 OK； malformed 行会报 `bad permissions` rc=1，非静默。）

## nohup → launchd 切换步骤（待授权后执行）

前置确认（**硬前置**，任一不满足即中止，不得继续）：

1. 现行实例为 `releases/RESTART.md` 方式拉起（nohup & disown），端口 8000；
   `curl -s http://127.0.0.1:8000/healthz` 可达，且确认**无在途分析任务**；
2. `plist`/`wrapper` 中 `RELEASE_SHA`/`RELEASE_DIR`/`VENV_PYTHON`/`LOG_DIR`
   与当前生产一致；`{{RELEASE_DIR}}` 占位符已全量替换：
   `grep -c '{{' ~/Library/LaunchAgents/com.tradingagents.ashare.plist` 输出必须为 `0`；
3. **wrapper 已拷入 `{{RELEASE_DIR}}/releases/launchd/` 且可读可执行**
   （plist 的 ProgramArguments 指向该路径；release worktree 本身不含本目录，
   必须按下文步骤显式拷贝，否则 bootstrap 后 `/bin/bash` 找不到脚本、
   作业秒退且 `SuccessfulExit=false` 会反复重拉刷屏——切换当天停服的主风险点）；
4. 已用 `plutil -lint` 校验 plist、`bash -n` 校验 wrapper（见"本地验证"）；
5. 回退所需的 `RESTART.md` 拉起令与 `logs/launch-<sha>.txt` 在宿主机上可读。

```bash
# 0. 把 wrapper 放进 release 目录（生产 worktree 不含本仓库目录，必须显式拷贝）
mkdir -p "{{RELEASE_DIR}}/releases/launchd"
cp releases/launchd/ta-launchd-wrapper.sh "{{RELEASE_DIR}}/releases/launchd/"
chmod +x "{{RELEASE_DIR}}/releases/launchd/ta-launchd-wrapper.sh"
test -r "{{RELEASE_DIR}}/releases/launchd/ta-launchd-wrapper.sh" \
  && test -x "{{RELEASE_DIR}}/releases/launchd/ta-launchd-wrapper.sh" || exit 1

# 1. 停现行 nohup 实例（RESTART.md"停止"节；先确认无在途分析任务）
kill -TERM <uvicorn_pid>; sleep 2
lsof -nP -iTCP:8000 -sTCP:LISTEN   # 应无输出

# 2. 装 LaunchAgent（用户域，无需 sudo）；先替换占位符再校验
mkdir -p ~/Library/LaunchAgents
sed "s|{{RELEASE_DIR}}|/Users/davidliu/Documents/TradingAgents-AShare-releases/65988c79|g" \
    releases/launchd/com.tradingagents.ashare.plist \
    > ~/Library/LaunchAgents/com.tradingagents.ashare.plist
grep -c '{{' ~/Library/LaunchAgents/com.tradingagents.ashare.plist   # 必须为 0
plutil -lint ~/Library/LaunchAgents/com.tradingagents.ashare.plist   # OK

# 3. 装载并启动
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.tradingagents.ashare.plist
# KeepAlive 隐含 RunAtLoad——bootstrap 即首启动（DAV-1802 🟡-1 订正），
# 无需 kickstart；不带 -k 对 running 实例是 no-op（实测 starts 不变）。
launchctl print gui/$UID/com.tradingagents.ashare | head -20   # state=running
# 人工旁路（如需拉起且不占限流额度）：launchctl kickstart 无 -e 传 env
# 能力（macOS 27.x 仅支持 [-k] [-p] [-s]，-e 静默 no-op），改走哨兵文件——
#   mkdir -p <LOG_DIR>/.launchd && touch <LOG_DIR>/.launchd/manual-bypass
#   launchctl kickstart gui/$UID/com.tradingagents.ashare   # 哨兵一次性消费
# 注：kickstart 不能注入 env；对 running 实例需先 bootout 或 -k。

# 4. 验证（同 RESTART.md"验证"节）
sleep 25
curl -s http://127.0.0.1:8000/healthz   # commit_sha=<sha>、status=ok
launchctl print gui/$UID/com.tradingagents.ashare | head -20   # state=running
tail -5 /Users/davidliu/Documents/TradingAgents-AShare-releases/logs/wrapper-diagnostic.log
```

## 回退（恢复 RESTART.md 手动拉起）

**回退前**：确认无在途分析任务；先 `launchctl print` 看 state——若 uvicorn 正在跑，
`bootout` 会 SIGTERM 它，`ExitTimeOut=20` 内未退出则 SIGKILL，可能丢未提交事务。

```bash
# 0. 确认状态与在途任务
launchctl print gui/$UID/com.tradingagents.ashare | head -20   # 看 state / last exit
curl -s http://127.0.0.1:8000/healthz   # 确认服务与任务情况

# 1. 卸载 LaunchAgent（先禁自动拉起；运行中的 uvicorn 会收到 SIGTERM）
launchctl bootout gui/$UID/com.tradingagents.ashare
rm -f ~/Library/LaunchAgents/com.tradingagents.ashare.plist

# 2. 若 uvicorn 仍在跑，按 RESTART.md"停止"节停掉
kill -TERM <uvicorn_pid>; sleep 2
lsof -nP -iTCP:8000 -sTCP:LISTEN   # 应无输出

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
newsyslog -vnrs -f /dev/stdin <<'EOF'   # dry-run 校验 newsyslog 配置语法
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/launchd-stdout.log   davidliu:staff 644 5 1024 * Z
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/launchd-stderr.log   davidliu:staff 644 5 1024 * Z
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/wrapper-diagnostic.log davidliu:staff 644 5 1024 * Z
EOF
# 预期输出：3 × "does not exist, skipped."（语法 OK；bad permissions 则配置有误）
```

## 注意

- 不要 `setsid`（macOS 无此命令）；launchd 本身就是会话外守护，无需 nohup/disown。
- wrapper 里 `exec` 让 launchd 直管 uvicorn 进程本体（bash 被原地替换，已实测）；
  不要再去 `&` 后台。
- `launchctl kickstart` 无 `-e` 传 env 能力（macOS 27.x 仅 `[-k] [-p] [-s]`，
  `-e` 静默 no-op exit 0）；对 running 实例不带 `-k` 的 kickstart 是 no-op。
  人工旁路改走哨兵文件 `<LOG_DIR>/.launchd/manual-bypass`（一次性，见上文）。
- 守望清单新增：`kill -9`/OOM 虽会被 `SuccessfulExit=false` 覆盖重拉，仍须把
  `wrapper-diagnostic.log` 连刷「已达上限」/`FATAL` 列为巡检告警项；
  `launchctl print` 看到 `state = waiting` 且 8000 无监听时先查该日志。
- 切换 release 时同步改 plist 的 `{{RELEASE_DIR}}` 与 wrapper 的 `RELEASE_SHA`，
  并 `launchctl kickstart -k gui/$UID/com.tradingagents.ashare` 重启到新版。
- 安装时间 10-12 窗口之后由总控另定；本令只交模板，不碰生产、不动现有实例。
