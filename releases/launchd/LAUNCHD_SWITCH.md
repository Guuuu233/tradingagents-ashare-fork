# 生产服务 launchd 托管模板（DAV-2180 直启版；总控 10-17 令：只交模板，不安装）

本目录交付 `com.tradingagents.ashare.plist` 模板，用于把现行 `nohup … & disown`
拉起方式切到 launchd 托管。**本令只交模板、不安装**；安装时间在复审通过后由总控另定。

> **DAV-2180 变更**：10-17 实测 launchd 对 `~/Documents` 全树 TCC 拒读，`/bin/bash`
> 唤起时 `getcwd`/`ls ~/Documents` 均 `Operation not permitted`，bash 包装脚本路径不可行。
> 本版改为 **plist `ProgramArguments` 直接 exec 生产 venv 解释器跑 uvicorn**
> （`/Users/davidliu/ta-venv/py310/bin/python`，realpath 为 uv 管理的
> `cpython-3.10.20-macos-aarch64-none/bin/python3.10`，ad-hoc 签名，实测 launchd 下
> 可读 `~/Documents`）；原 wrapper 注入的 16 个环境变量迁入 plist `EnvironmentVariables`。
> **解释器选型（总控 10-17 返修令）**：不用主检出的 `.venv310`（DAV-2087 审计认定的
> 最大单点，10-17 存量任务已迁离），改指独立 venv `~/ta-venv/py310`；10-17 实测两者
> realpath 相同、`pip freeze` 逐行 diff 为空、uvicorn 均 0.41.0。
> **不再使用 `ta-launchd-wrapper.sh`**（本版 plist 不引用它）。

以 `releases/RESTART.md` 为现行拉起基线（当前生产 release=`65988c79`，端口 8000）。

## 文件清单

| 文件                                              | 作用                                                                                                                             |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `releases/launchd/com.tradingagents.ashare.plist` | launchd 作业模板（用户域 LaunchAgents）；DAV-2180 直启版：ProgramArguments 直接调 venv python，环境变量在 `EnvironmentVariables` |
| `releases/launchd/ta-launchd-wrapper.sh`          | **旧版**包装脚本（限流计数 + 环境固化 + exec uvicorn）；DAV-2180 起新 plist 不再引用，仅留档供回退/对照                          |
| `releases/launchd/LAUNCHD_SWITCH.md`              | 本说明：切换 / 回退步骤                                                                                                          |
| `releases/launchd/plist_log_preflight.py`         | **DAV-2223**：安装前日志路径前置检查（fail-closed，防 EX_CONFIG 78）。只读、不装 launchd、不碰生产                               |

## RESTART.md 基线说明（🟡-4 订正）

`releases/RESTART.md` 是**宿主机侧文件**（`/Users/davidliu/Documents/TradingAgents-AShare-releases/RESTART.md`），
**未纳入 git 版本控制**（`git log --all -- releases/RESTART.md` 为空）。模板中
"与 RESTART.md 一致"的基线指该宿主机文件，无法在本仓库 PR 中 diff 校验；
其同步方式为总控运维流程下的宿主机手工维护，切换/回退时以宿主机现行版本为准。
同理 `releases/logs/launch-<sha>.txt` 留档启动令亦在宿主机日志目录。

## 重启节流机制（DAV-2180 直启版：launchd 原生 ThrottleInterval，无滑动窗口熔断）

- plist 里 `KeepAlive={"SuccessfulExit": false}`：**非零退出与信号退出**都自动拉起，
  SIGKILL（`kill -9`）与 macOS OOM killer 覆盖在内；`launchctl bootout` 卸载不算退出，不误拉。
- **DAV-2180 差异**：直启版不经 bash 包装脚本，旧版「同一小时最多 3 次」滑动窗口
  熔断随脚本一并移除。本版改用 `ThrottleInterval=120`（两次 respawn 至少隔 120s）+
  `StartInterval=600` 周期探测。**不再有"每小时 3 次熔断停手"语义**——若 uvicorn
  持续崩溃，launchd 按 120s 间隔一直重拉。**总控 10-17 已确认接受此变化**——限流参数
  只有 `ThrottleInterval=120` + `StartInterval=600`，不再有每小时 3 次熔断；若日后要
  恢复熔断语义，须另引入不经 bash 的轻量 supervisor（如 python -c 计数 wrapper 或编译型
  helper）。
- 崩溃留证：uvicorn 崩溃输出全部落 `logs/uvicorn-<sha>.log`（StandardOut/ErrorPath
  同指），launchd 自身诊断落 `logs/launchd-stderr.log`（见下表）；守望哨兵
  （prod-health-sentinel）与巡检应把反复崩溃列为告警项。
- 人工拉起：`launchctl kickstart -k gui/$UID/com.tradingagents.ashare`（对 running 实例
  先 `-k` 强制重启）。

## 日志与轮转（DAV-2180 直启版）

| 日志                      | 来源                                                                                                                                                                                                 | 轮转                   |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `logs/uvicorn-<sha>.log`  | **plist `StandardOutPath`/`StandardErrorPath` 同指**——uvicorn stdout+stderr 合并直落，与旧 wrapper `>> log 2>&1` 等价；launchd 打开 fd 追加写，不经 bash                                             | 沿用宿主机现行外部轮转 |
| `logs/launchd-stderr.log` | launchd 自身诊断（getcwd/exec 失败等）——plist 未设 Standard*Path 给 launchd 自身，实际落点是 launchd 默认；**本版把 uvicorn 输出直接指到 uvicorn-<sha>.log，launchd-stderr.log 仅留 launchd 层错误** | 见下                   |

> **变更**：旧版 wrapper 的 `logs/wrapper-diagnostic.log`、`logs/.launchd/restart-stamps`、
> 兜底戳目录、`manual-bypass` 哨兵等**计数设施随脚本一并移除**，直启版不再产生。
> uvicorn 崩溃留证全靠 `uvicorn-<sha>.log` + launchd 自身 stderr。

`logs/uvicorn-<sha>.log` 与 `logs/launchd-stderr.log` 均为 append 无内置上限，采用
`newsyslog` 轮转（macOS 原生）。安装时在 `/etc/newsyslog.d/` 加一条（需 sudo，
仅安装窗口执行一次）：

```
# logfilename                                    [owner:group] mode count size(kb) when  flags
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/uvicorn-082be047.log   davidliu:staff 644 5 1024 * Z
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/launchd-stderr.log     davidliu:staff 644 5 1024 * Z
```

（>1MB 轮转、保留 5 代、gzip 压缩；未配置前文件随唤起线性增长，低频可接受，
但**必须在切换文档中登记**，故入册。已用 `newsyslog -vnrs -f` dry-run
校验通过：`davidliu:staff` 属主解析正确、三条配置输出 `does not exist, skipped`
即语法 OK； malformed 行会报 `bad permissions` rc=1，非静默。）

## nohup → launchd 切换步骤（DAV-2180 直启版；待授权后执行）

前置确认（**硬前置**，任一不满足即中止，不得继续）：

1. 现行实例为 `releases/RESTART.md` 方式拉起（nohup & disown），端口 8000；
   `curl -s http://127.0.0.1:8000/healthz` 可达，且确认**无在途分析任务**；
2. plist 的 `WorkingDirectory`/`Standard*Path`（`uvicorn-<sha>.log`）与当前生产
   release 一致；`{{RELEASE_DIR}}`/`{{RELEASE_SHA}}` 占位符已全量替换：
   `grep -cE '\{\{[A-Z_]+\}\}' ~/Library/LaunchAgents/com.tradingagents.ashare.plist` 输出必须为 `0`；
3. **解释器在 launchd 下可读 `~/Documents`**——用临时 launchd 探针验证
   （DAV-2180 已实测：uv-managed ad-hoc `python3.10` realpath 可读；/bin/bash 被拒）。
   方法：写一个临时 plist 直接 exec 生产 venv python `test -r ~/Documents` + `os.getcwd()`，
   bootstrap 后读出文件，跑完 `launchctl bootout` 删 plist。任一拒绝即中止。
4. 已用 `plutil -lint` 校验 plist（见"本地验证"）；
5. 回退所需的 `RESTART.md` 拉起令与 `logs/launch-<sha>.txt` 在宿主机上可读。

```bash
# 0. 停现行 nohup 实例（RESTART.md"停止"节；先确认无在途分析任务）
kill -TERM <uvicorn_pid>; sleep 2
lsof -nP -iTCP:8000 -sTCP:LISTEN   # 应无输出

# 1. 装 LaunchAgent（用户域，无需 sudo）；先替换占位符再校验
mkdir -p ~/Library/LaunchAgents
sed -e "s|{{RELEASE_DIR}}|/Users/davidliu/Documents/TradingAgents-AShare-releases/<sha>|g" \
    -e "s|{{RELEASE_SHA}}|<sha>|g" \
    releases/launchd/com.tradingagents.ashare.plist \
    > ~/Library/LaunchAgents/com.tradingagents.ashare.plist
grep -cE '\{\{[A-Z_]+\}\}' ~/Library/LaunchAgents/com.tradingagents.ashare.plist   # 必须为 0
plutil -lint ~/Library/LaunchAgents/com.tradingagents.ashare.plist   # OK

# 1b. 日志路径前置检查（DAV-2223；不通过即中止，不得 bootstrap）
# LC_ALL=C：探测的「可放行」分支按 xattr(1) 的英文 stderr 文案匹配，非英文 locale
# 会退化成 PROBE_UNAVAILABLE（安全方向但白拦一次本可放行的安装）。
LC_ALL=C python3 releases/launchd/plist_log_preflight.py ~/Library/LaunchAgents/com.tradingagents.ashare.plist

# 2. 装载并启动
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.tradingagents.ashare.plist
# KeepAlive 隐含 RunAtLoad——bootstrap 即首启动。
launchctl print gui/$UID/com.tradingagents.ashare | head -20   # state=running

# 3. 验证（同 RESTART.md"验证"节）
sleep 25
curl -s http://127.0.0.1:8000/healthz   # commit_sha=<sha>、status=ok
launchctl print gui/$UID/com.tradingagents.ashare | head -20   # state=running
tail -20 /Users/davidliu/Documents/TradingAgents-AShare-releases/logs/uvicorn-<sha>.log
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

## 本地验证（安装前；DAV-2180 直启版）

```bash
# plist 语法
plutil -lint releases/launchd/com.tradingagents.ashare.plist   # OK

# 日志路径前置检查（DAV-2223：fail-closed，防 EX_CONFIG 78 复发）
# 必须带 LC_ALL=C：xattr(1) 的两条「可放行」分支靠英文 stderr 文案匹配（见
# plist_log_preflight.py 的 _carries_provenance），非英文 locale 会退化成
# LOGPATH_PROVENANCE_PROBE_UNAVAILABLE（fail-closed，不误放行，但白拦安装）。
LC_ALL=C python3 releases/launchd/plist_log_preflight.py ~/Library/LaunchAgents/com.tradingagents.ashare.plist
# 退出码：0 通过（可能含 WARN）/ 2 前置不通过（原因码见下）/ 3 输入或用法错误。
# 三档判定（总控 10-18 令），**只有 FAIL 阻断安装**：
#   FAIL  带 com.apple.provenance 的既存日志（10-18 事故形态）→ 处置 = **改名留档**
#         后由 launchd 重建同名文件，**不直接覆盖**；其他不满足项（探测不可用、
#         属主/可追加/形态/黑名单/占位符）同样 fail-closed。
#   WARN  既存日志文件**不带** provenance 且当前用户可追加写 → launchd 能正常打开
#         并追加，故 **WARN 不拦**（退出码仍为 0），终端与 --json 均**逐条列出**
#         （原因码 LOGPATH_TARGET_EXISTING_APPENDABLE），供运维知情。
#   PASS  目标不存在（launchd 自建）。
# 检查项：StandardOut/ErrorPath 非空、无未替换占位符、绝对路径、父目录存在且
# 可写、目标不存在（PASS），或为**不带 com.apple.provenance 的**、属主为本用户且
# 可追加的**普通文件**（WARN 不拦，见上），且不落在旧版包装脚本遗留物上
# （wrapper-diagnostic.log / diag-fallback.log / .launchd/ /
# /tmp/ta-launchd-die-stamps-*，文件名大小写不敏感匹配）。
# 10-18 首次 bootstrap 失败（EX_CONFIG 78）的真因：日志文件是 wrapper 时代由
# bash/>> 写出的**既存**文件，带 com.apple.provenance（属主/权限完全正常）；
# launchd 的 xpcproxy 不带 ~/Documents 的 TCC 授权、打不开它。判定用
# `xattr -p com.apple.provenance -- <path>`（只读，不写盘；launchd 新建的文件通常
# **只有** com.apple.macl，但两者可共存——10-18 事故文件就同时带 macl 与
# provenance，**有 macl 不能反推安全**；判据只取 provenance 有无）。命中 → 原因码
# LOGPATH_TARGET_PROVENANCE_XATTR，处置：改名留档后由 launchd 重建同名文件，
# 勿复用该路径；若该文件正被在役作业持有（lsof -nP <path> 有输出），先
# launchctl bootout 再改名（改名后 launchd 仍写旧 inode）。
# xattr(1) 本身不可用/执行失败（非 0 且非「无此属性」，如 EACCES、不支持 xattr 的
# 文件系统）→ 原因码 LOGPATH_PROVENANCE_PROBE_UNAVAILABLE，同样 fail-closed，
# 但**不代表文件带该属性**，文案带异常文本：请人工 `xattr -p com.apple.provenance -- <path>` 复核
# 或换用可用的解释器重跑，不要据此改名。
# 注：符号链接目标一律判 LOGPATH_TARGET_NOT_REGULAR_FILE（比 launchd 严格），
# 故不要用 uvicorn-current.log -> uvicorn-<sha>.log 的软链方案做轮转。
# 注：以 root/sudo 运行会让属主比对与可写判定失真（os.access 对 root 恒真），
# 请以登录用户身份运行本检查。
# 最终验收（总控 10-18 令）：以 launchd bootstrap 后 `launchctl print` 显示
# state=running、且日志有新写入为准；本检查只是安装**前**的确定性预检。

# 解释器 realpath 与 TCC 探针（DAV-2180 已实测，复审可复跑）
realpath /Users/davidliu/ta-venv/py310/bin/python
# → /Users/davidliu/.local/share/uv/python/cpython-3.10.20-macos-aarch64-none/bin/python3.10
# 临时 launchd 探针：写一个 Label=com.tradingagents.tccprobe 的 plist，
# ProgramArguments=[<realpath>, -c, 'import os;print(os.access("/Users/davidliu/Documents",os.R_OK))']，
# bootstrap 后读 StandardOutPath，跑完 bootout 删 plist。实测 python3.10 返回 True，
# /bin/bash 返回 Operation not permitted——故直启可行。

# uvicorn 可解析（独立 venv 内）
env -u PYTHONPATH /Users/davidliu/ta-venv/py310/bin/python \
  -c "import uvicorn; print(uvicorn.__version__)"   # 0.41.0
# 与主检出 .venv310 的依赖一致性（输出为空 = 一致）
diff <(env -u PYTHONPATH /Users/davidliu/ta-venv/py310/bin/python -m pip freeze | sort) \
     <(env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pip freeze | sort)

# newsyslog dry-run（仅两条，直启版无 wrapper-diagnostic/restart-stamps）
newsyslog -vnrs -f /dev/stdin <<'EOF'
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/uvicorn-082be047.log   davidliu:staff 644 5 1024 * Z
/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/launchd-stderr.log     davidliu:staff 644 5 1024 * Z
EOF
# 预期输出：2 × "does not exist, skipped."（语法 OK）
```

## 注意（DAV-2180 直启版）

- 不要 `setsid`（macOS 无此命令）；launchd 本身就是会话外守护，无需 nohup/disown。
- plist `ProgramArguments` **直接 exec venv python 跑 uvicorn**，不经 bash wrapper；
  `exec` 语义由 launchd 自身完成，不要再加 `&` 后台。
- 解释器固定指向 `/Users/davidliu/ta-venv/py310/bin/python`（realpath = uv 管理的
  `cpython-3.10.20`，ad-hoc 签名，实测 launchd 下可读 `~/Documents`）。换解释器必须
  重跑 TCC 探针；主检出 `.venv310` 10-17 起不再是生产托管入口。
- 无滑动窗口熔断：持续崩溃会被 `ThrottleInterval=120` 间隔一直重拉，崩溃输出
  落 `uvicorn-<sha>.log`；守望哨兵 + 巡检须把反复崩溃列为告警项。
- `launchctl kickstart` 无 `-e` 传 env 能力（macOS 27.x 仅 `[-k] [-p] [-s]`）；
  对 running 实例不带 `-k` 的 kickstart 是 no-op，需 `kickstart -k` 强制重启。
- 切换 release 时同步改 plist 的 `WorkingDirectory` 与 `Standard*Path` 内的
  `uvicorn-<sha>.log`（`ProgramArguments` 不变——解释器固定），并
  `launchctl kickstart -k gui/$UID/com.tradingagents.ashare` 重启到新版。
- 安装时间在复审通过后由总控另排窗口；本令只交模板，不碰生产、不动现有实例。

---

# 每日进度表 + 1950 验收报告（DAV-2035，20:30；总控 10-13/10-14 令：只交模板，不安装）

同一目录下另有 `com.davidliu.ta-daily-reports.plist` + `ta-daily-reports-wrapper.sh`，
与上面的生产服务托管**完全独立**：Mon–Fri **20:30**（排在 19:30 tplus5-shadow 与
19:45 phase2 ledger 之后）跑一次，产出当日**进度表**（`dav1507_exit_progress.py --json`）
与**1950 验收报告**（`dav1950_exit_acceptance_report.py --json`）。安装时间同令：
**10-18 部署后、与定时任务检出升级同发布卡**。

| 项           | 口径                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 输入         | `~/ta-snapshot/current.db`，**只读**；读取的 `file:…?mode=ro&immutable=1` URI 口径由 **DAV-2033** 在 `open_ro`/`_connect_ro` 内部统一，本 wrapper 只传 `--db`，不自己拼 URI、不复制快照                                                                                                                                                                                                                                                                                                       |
| 输出         | `~/ta-work/reports/YYYY-MM-DD/`：`exit-progress-<date>.json`、`acceptance-<date>.json`、`run-status-<ts>.txt`（成功标记），失败时额外 `ALERT-<ts>.log`                                                                                                                                                                                                                                                                                                                                        |
| 保留         | **30 天**（`TA_RETENTION_DAYS`）。清理只删名字形如 `YYYY-MM-DD` 的目录，`_logs/` 等不匹配目录一律跳过                                                                                                                                                                                                                                                                                                                                                                                         |
| 失败         | 任一脚本非零退出或产出空文件 → 写 `ALERT-<ts>.log` + `run-status` FAILED + **wrapper exit 1**，不静默                                                                                                                                                                                                                                                                                                                                                                                         |
| PROJECT_ROOT | **总控 10-14 令**：`TA_PROJECT_ROOT` 无默认值——plist 必须显式指向一个**专用检出**（如 `~/ta-reports-<sha>`），**禁止主检出** `~/Documents/TradingAgents-AShare`。wrapper 启动时用 `git -C "$PROJECT_ROOT" merge-base --is-ancestor 8571c80b HEAD` 核对该检出包含 DAV-2033，不满足即 `fail_alert`。锚点默认值为 `8571c80b3578c7ea40e3bdf6eed2ad7646415db9`（DAV-2033 在 `codex/dav-4-p2a-trunk` 上的合入提交），plist 以 `TA_REQUIRED_DAV2033_COMMIT` 显式接线；主检出比对前先 `pwd -P` 规范化 |
| 交易日历     | **总控 10-14 令**：`TA_CALENDAR_JSON` 指向的日历文件不存在时直接 `fail_alert`，不再 WARN 放行                                                                                                                                                                                                                                                                                                                                                                                                 |
| 无 KeepAlive | 失败的报表运行**不重拉**：重拉只会重复撞同一个 bug；告警留证，下一交易日自然再跑                                                                                                                                                                                                                                                                                                                                                                                                              |
| 日志         | `~/ta-work/reports/_logs/{exit-progress,acceptance}-<date>.log` + launchd 自己的 `launchd.{out,err}.log`                                                                                                                                                                                                                                                                                                                                                                                      |

环境（plist `EnvironmentVariables`，均可覆盖）：`TA_PYTHON_BIN`（生产固定
`.venv310`）、`TA_PROJECT_ROOT`（**必填**，专用检出 `~/ta-reports-<sha>`）、
`TA_SNAPSHOT_DB`、`TA_REPORTS_ROOT`、`TA_RETENTION_DAYS`。wrapper 内部对每次子进程
`env -u PYTHONPATH` 并 unset 代理（venv310 门禁铁律 + 脚本零出网）。

## 安装 / 回退（待 10-18 授权后执行）

先准备专用检出（含 DAV-2033 的发布 SHA 在 `codex/dav-4-p2a-trunk` 上）：

```bash
RELEASE_SHA=<主干发布 SHA>
REPORTS_DIR=/Users/davidliu/ta-reports-${RELEASE_SHA:0:8}
git clone /Users/davidliu/Documents/TradingAgents-AShare "${REPORTS_DIR}"
git -C "${REPORTS_DIR}" checkout "${RELEASE_SHA}"
git -C "${REPORTS_DIR}" merge-base --is-ancestor 8571c80b HEAD   # 必须 exit 0

mkdir -p "${REPORTS_DIR}/releases/launchd"
cp releases/launchd/ta-daily-reports-wrapper.sh "${REPORTS_DIR}/releases/launchd/"
chmod +x "${REPORTS_DIR}/releases/launchd/ta-daily-reports-wrapper.sh"

mkdir -p ~/Library/LaunchAgents ~/ta-work/reports/_logs
sed -e "s|{{RELEASE_DIR}}|${REPORTS_DIR}|g" \
    -e "s|{{RELEASE_SHA}}|${RELEASE_SHA:0:8}|g" \
    releases/launchd/com.davidliu.ta-daily-reports.plist \
    > ~/Library/LaunchAgents/com.davidliu.ta-daily-reports.plist
grep -c '{{' ~/Library/LaunchAgents/com.davidliu.ta-daily-reports.plist   # 必须为 0
plutil -lint ~/Library/LaunchAgents/com.davidliu.ta-daily-reports.plist  # OK
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.davidliu.ta-daily-reports.plist

# 回退
launchctl bootout gui/$UID/com.davidliu.ta-daily-reports
rm -f ~/Library/LaunchAgents/com.davidliu.ta-daily-reports.plist
```

## 本地验证（安装前，已实测）

```bash
bash -n releases/launchd/ta-daily-reports-wrapper.sh     # 语法 OK
plutil -lint releases/launchd/com.davidliu.ta-daily-reports.plist   # OK
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
  -m pytest tests/test_ta_daily_reports_launchd.py -q -p no:randomly    # 13 passed
```

该测试用**桩解释器** + 临时目录跑全链路（成功路径双 JSON、30 天清理、上游失败
告警非零、快照缺失 fail-closed），并为 10-14 令新增四个用例：`TA_PROJECT_ROOT`
未设 fail_alert、PROJECT_ROOT=主检出 fail_alert（含 `//` 变体，pwd -P 规范化）、
检出不含 DAV-2033 锚点 fail_alert、
交易日历缺失 fail_alert。不碰生产库、不装 launchd。

## 生产健康哨兵（DAV-2063；总控 10-15 令：只交模板，不安装）

背景：10-14 机器重启后守望随会话中断，生产停服 7.5h 无人发现。哨兵独立于
任何 Claude/agent 会话，纯脚本 + launchd，只观测告警，**不做自动重启**
（重启由上面的 `com.tradingagents.ashare` 负责）。10-17 与 DAV-1852 一起安装。

| 文件                                                            | 作用                                                   |
| --------------------------------------------------------------- | ------------------------------------------------------ |
| `releases/launchd/ta-prod-health-sentinel.sh`                   | 哨兵脚本（探活 + 版本校验 + 告警/恢复记录）            |
| `releases/launchd/com.tradingagents.prod-health-sentinel.plist` | launchd 作业模板（RunAtLoad + StartInterval=300）      |
| `releases/launchd/tests/test_prod_health_sentinel.sh`           | 五情形桩测试（通/不通/版本不符/恢复/同秒多记录不覆盖） |

判定规则：

- `GET ${HEALTH_BASE_URL}/healthz`（curl `--max-time 10`）：连接失败 / 非 2xx /
  2xx 但响应体无 `commit_sha` 均记为不通；
- **不通连续两次**（`FAIL_THRESHOLD=2`）才告警，第一次只写状态、不告警、不通知；
- `commit_sha` 与 `EXPECT_PROD_SHA` 不符**立即**告警（版本漂移不去抖）；
- 告警落 `~/ta-watch/ALERT-prod-<utc>-<unreachable|sha_mismatch>-<pid>.log`，
  并 `osascript` 发系统通知（`TA_WATCH_NOTIFY=0` 可关）；
- 从告警恢复到健康写 `~/ta-watch/ALERT-prod-recovered-<utc>-<pid>.log`（含恢复时间与前序原因），
  持续健康不重复写。
- 文件名末尾的 `<pid>` 是 DAV-2157 加的：`<utc>` 只精确到秒，同秒内两次记录会写同一
  文件名、后一次静默覆盖前一次（丢记录）；哨兵有 `mkdir` 锁（同刻至多一个进程）、
  单次执行最多写一个记录文件（告警/恢复分支互斥），故进程号足以保证同秒不撞名。

安装前必做（占位符必须替换，残留为 0）：

```bash
# {{RELEASE_DIR}} / {{EXPECT_PROD_SHA}} / {{WATCH_DIR}} / {{VENV_PYTHON}}
sed -i '' 's|{{RELEASE_DIR}}|/Users/davidliu/Documents/TradingAgents-AShare-releases/<SHA>|g' \
  ~/Library/LaunchAgents/com.tradingagents.prod-health-sentinel.plist
grep -c '{{' ~/Library/LaunchAgents/com.tradingagents.prod-health-sentinel.plist   # 预期 0
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.tradingagents.prod-health-sentinel.plist
```

安装后自查：`RunAtLoad` 会立刻探一次；此后每 5 分钟一次，
`launchctl print gui/$UID/com.tradingagents.prod-health-sentinel` 看 `last exit code`，
状态与前序告警在 `~/ta-watch/.sentinel/state`。

**10-18 安装窗口待办（DAV-2180 说明，非本版代码改动）**：10-17 已装实例的
`ProgramArguments` 为 `[/bin/bash, ~/ta-watch/ta-prod-health-sentinel.sh,
~/Documents/…/082be047/releases/launchd/ta-prod-health-sentinel.sh]`——**第三个是多余参数**。
脚本只从 `EnvironmentVariables` 取配置、不读任何位置参数（`$1`/`$2` 仅作函数形参），
且该路径与 ta-watch 实际执行副本逐字节相同（10-17 `diff` 为空），删掉不影响行为；
它是安装时替换 `{{RELEASE_DIR}}` 后残留的来源路径（指向 TCC 受限的 `~/Documents`）。
安装窗口从已装 plist 删掉该 `<string>` 后 `bootout` + `bootstrap` 重载即可；
模板本身只有前两个元素，无需改。

本地验证（不碰生产、不装 launchd）：

```bash
plutil -lint releases/launchd/com.tradingagents.prod-health-sentinel.plist  # OK
bash -n releases/launchd/ta-prod-health-sentinel.sh                          # 语法 OK
bash releases/launchd/tests/test_prod_health_sentinel.sh                      # 29 项断言全绿
```

测试只用临时目录 + 随机高位端口的桩服务（`BaseHTTPRequestHandler`），
不连 8000，不发通知（`TA_WATCH_NOTIFY=0`）；测试开头导出 `NO_PROXY=localhost,127.0.0.1`，
本机/CI 设了 `http_proxy` 时也不会把回环地址走代理而假红。
情形 5 用 PATH 首位的假 `date` 把时间戳钉在同一秒，**确定性**复现「同秒多次告警/恢复」：
旧实现下该段必然只剩 1 个告警 + 1 个恢复文件并不通过，新实现 2 + 2 全过。

注意：切换 release 时 `{{EXPECT_PROD_SHA}}` 必须同步改，否则哨兵立刻报
`sha_mismatch`——这是有意为之的漂移检测，不是误报。
