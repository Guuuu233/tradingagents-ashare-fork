# 存储健康巡检 I（storage_health.sh）— 脚本 + launchd 模板（只入库，未安装）

本目录收纳宿主每日存储巡检脚本与其 launchd 模板。**本卡只推分支 + 开复审：脚本与模板
都已入仓，但未安装、未启用**——`~/Library/LaunchAgents/` 下没有对应 plist，
`launchctl list` 无该项，`crontab` 为空。安装另走发布卡，排在 **10-19 之后**。

## 文件清单

| 文件 | 作用 |
|---|---|
| `releases/launchd/storage_health.sh` | 巡检脚本本体（zsh，`daily` / `weekly` / `auto` 三种模式） |
| `releases/launchd/com.tradingagents.storage-health.plist` | launchd 模板（含 `{{REPO_DIR}}` / `{{LOG_DIR}}` 占位符，**未安装**） |
| `releases/launchd/tests/test_storage_health.sh` | 行为门禁：`bash releases/launchd/tests/test_storage_health.sh` |
| `releases/launchd/STORAGE_HEALTH.md` | 本说明 |

DAV-2215 入库时脚本与 DAV-1508 交付件**逐字节一致**（旧 `sha256 d1936831b94d677bf79eefc826b9fb5186a84bba8ea8dc59dee9749ef5923037`）；
**DAV-2219 按 DAV-2216 复审的 2🟡/2🟢 修正后已偏离该哈希**（新哈希见交付评论，或本机
`shasum -a 256 releases/launchd/storage_health.sh`），改动仅限热备口径与「不可测」处理，
水位阈值、每周基线等逻辑行未动。

## 水位口径（总控 2026-10-17 令）

| 可用空间（`df -g /System/Volumes/Data` 第 4 列，GiB） | 灯色 | 动作 |
|---|---|---|
| ≥ 100 | 绿 | 无 |
| 60 – 99 | 黄 | **只记录**，不唤醒、不停副本 |
| < 60 | 红 | 立即唤醒；**停止创建整库副本** |

旧口径（≥150 绿 / 120–149 黄 / <120 红）已按 10-17 令彻底移除，脚本内只剩 100/60 两个阈值。
门禁用假 `df` 覆盖 150/120/100/99/80/60/59 七个点，并显式断言「120 不再判黄、80 不再判红」，
防止回退到旧阈值。

热备预算另有一条（**DAV-2219 订正口径**）：**`同级 releases 目录 + 快照目录`**（即副本集）
合计 ≤ 15 GiB 为绿，超出报「红（超预算）」。判定用**精确 KiB**（15 GiB = `15728640 KiB`），
不做整数截断——`(15,16) GiB` 窗口（如 15.5 GiB）必须判红，报告里同时给出精确 KiB 与一位小数 GiB。
`PROJ` 是工作树/活库所在（`data/tradingagents.db` 是活库，不是副本），单列展示、**不计入预算**。
改前口径是 `PROJ + releases + 快照 ≤ 15 GiB`，含 6 GiB 活库时从第一天就红（DAV-2216 🟢-2）。

### 目录不可测（DAV-2219 🟡-1）

`du` **失败、无输出或输出非数值时不得判绿**：该目录记为「⚠️ 不可测」并把 `du` 的 stderr 原文
写进报告，热备行降级为「**未知 ⚠️** — 有目录不可测，预算判定不成立」；整个脚本以**退出码 2**
结束（报告本身照常产出，不是静默失败）。

- 部分不可测也算：`releases` 或快照目录只要有一个不可测，预算判定就不成立；
- `PROJ` 单独不可测时不影响副本预算，但同样计入不可测数与退出码 2，并在它自己的行上标 ⚠️；
- 目录**不存在**不算不可测（沿用旧行为：跳过）；只有「存在但读不了」才记不可测；
- 旧行为（`du … 2>/dev/null` 后再算术累加，空输出在 zsh 算术中按 0）会把 TCC 拒读的三个目录
  报成「合计约 0 GiB → 绿、`rc=0`」；本文旧版又把该场景写成「作业会静默失败（绿灯都产不出来）」——
  两句都不成立（实测恰好能产出假绿灯），已一并订正。

## 报告内容与落盘（安装后）

- 每日 09:00 由 launchd 唤起 `storage_health.sh auto`；
- 报告：`{{LOG_DIR}}/storage-health-<YYYY-MM-DD>.md`（当日复跑会覆盖当日文件，与 DAV-1508 交付行为一致）；
- 脚本 stderr：`{{LOG_DIR}}/storage-health-stderr.log`；launchd 自身 exec/getcwd 错误：
  `{{LOG_DIR}}/launchd-storage-health-stderr.log`（分开留证，不让错误串污染当日 markdown）；
- 内容：日期、水位三色 + `df -h` 人读行、热备副本合计（精确 KiB）+ 副本逐目录占用
  （工作树/活库单列、不计预算）、不可测目录及其原因、≥200 MiB 无主/大文件清单
  （按路径归类给出「活库/发布备份/共享快照/日志/依赖产物/待人工判断」）；
- 退出码：全量测得且未超预算 = 0；有目录不可测 = 2（热备行「未知」，不得据此判绿）。
- 周三另加「每周基线」小节：从 `~/ta-snapshot/` 取当周最后一份
  `tradingagents-*.db.zst`，记录大小 + SHA256，有 `quark` CLI 时上传到
  `/TradingAgents-AShare/baselines`，否则在报告里要求人工上传。

## 只读纪律（本卡硬约束）

脚本只调用 `df` / `du` / `find` / `ls` / `shasum` / `stat` / `date` 等统计类命令，
**不含任何 `rm`、删除、覆盖清理逻辑**；云端同样不动——夸克超过 2 份时只在报告里列出清单，
由人手工删最旧份。

门禁对此做四道静态断言（剔除注释、`[[ ]]` 比较与双引号字面量后检查真实语法）：

1. 无 `rm/rmdir/unlink/truncate/shred/mv/dd/install/cp`；
2. 无 `find -delete`；
3. 除 `2>/dev/null`、`>/dev/null`、`2>&1` 外没有任何重定向（脚本自身不落盘）；
4. 无 `sqlite3/curl/wget/tee/osascript/python`（不给「只读」开后门）。

门禁另加一条 plist 契约断言：脚本必须带**可执行位**（DAV-2219 🟢-1）——plist 的 `-c` 串是
`"<绝对路径>/storage_health.sh auto"`，zsh 把它当**命令**执行，644 时先报 `permission denied`
（rc=126）且 stdout 已被重定向到当日报告 → 报告被截成 0 字节；而门禁旧的调用形式是
`"${ZSH_BIN}" "${SCRIPT}"`（zsh 读源码执行），644 照样 pass，拦不住这类回归。

已用变异验证门禁不是空跑（DAV-2219 逐项复跑）：水位阈值改回 150/120 → **8 条**失败
（7 条水位断言 + 1 条连带命中 section 4 的「水位绿」前置）；追加一行 `rm -rf "$SNAP_DIR/old"` →
静态门 **1 条**失败；预算判定还原成 `total_kb / 1048576 -le 15`（整数截断）→ **2 条**失败；
`du` 还原成 `2>/dev/null` 并按 0 计入 → **8 条**失败；`chmod 644` 脚本 → **1 条**失败。

## 安装（**本卡不执行**，另走发布卡，排在 10-19 之后）

硬前置（任一不满足即中止）：

1. **TCC 实测**：DAV-2180 已实测 launchd 对 `~/Documents` 全树拒读（`/bin/bash` 唤起时
   `getcwd`、`ls ~/Documents` 均 `Operation not permitted`）。本模板沿用 `/bin/zsh -c`，
   而脚本与报告目录默认都在 `~/Documents` 下——必须先用临时探针确认 **zsh 在 launchd 下
   可读 `{{REPO_DIR}}`、可写 `{{LOG_DIR}}`**；否则热备行会长期停在「未知 ⚠️」、脚本每次以
   退出码 2 结束（DAV-2219 后不再静默报绿）；若 `{{LOG_DIR}}` 也不可写，markdown 报告根本
   落不了盘（launchd 侧 stderr 日志仍留证）。
2. 占位符全量替换并校验残留为 0：
   `grep -cE '\{\{[A-Z_]+\}\}' ~/Library/LaunchAgents/com.tradingagents.storage-health.plist`
   必须输出 `0`；`plutil -lint` 必须 OK。
3. 报告目录存在且可写；首次手动跑一次 `storage_health.sh daily`，确认报告落盘且耗时远小于一天。

```bash
# 安装（发布卡执行，勿未经授权照抄）
mkdir -p ~/Library/LaunchAgents
sed -e "s|{{REPO_DIR}}|/Users/davidliu/Documents/TradingAgents-AShare|g" \
    -e "s|{{LOG_DIR}}|/Users/davidliu/Documents/TradingAgents-AShare-releases/logs|g" \
    releases/launchd/com.tradingagents.storage-health.plist \
    > ~/Library/LaunchAgents/com.tradingagents.storage-health.plist
grep -cE '\{\{[A-Z_]+\}\}' ~/Library/LaunchAgents/com.tradingagents.storage-health.plist   # 必须 0
plutil -lint ~/Library/LaunchAgents/com.tradingagents.storage-health.plist                 # OK
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.tradingagents.storage-health.plist
launchctl print gui/$UID/com.tradingagents.storage-health | head -20                       # 应注册成功
launchctl kickstart -k gui/$UID/com.tradingagents.storage-health                           # 手动首跑
ls -l /Users/davidliu/Documents/TradingAgents-AShare-releases/logs/storage-health-$(date +%F).md
```

回退：`launchctl bootout gui/$UID/com.tradingagents.storage-health`，
再 `rm ~/Library/LaunchAgents/com.tradingagents.storage-health.plist`（仅安装后回退时才需要）。

## 已知缺口 / 未验证项（务必在发布卡里处理）

1. **红灯没有推送通道**：脚本只写 markdown，不做通知、不重启、不清理。若无人每天看
   `{{LOG_DIR}}`，红灯（<60 GiB）不会叫醒任何人。是否接 `osascript` 通知或并入
   `prod-health-sentinel`，属发布卡设计范围，本卡未改。
2. **quark 上传在 launchd 下未验证**：launchd 无登录环境，PATH 已显式注入
   `/opt/homebrew/bin`，但没有代理变量（本机 Clash 7897）。上传失败时脚本只在报告里
   提示人工上传，不中断巡检；上传成功分支本机无法验证（本机无 `quark` CLI）。
3. **周三自动追加未做时钟注入测试**：门禁只单测 `weekly` 分支，`auto` 的
   `date +%u == 3` 门未用 faketime 覆盖。
4. 安装位置未定：本仓库路径（`releases/launchd/`）与 DAV-1508 原交付说明里的
   `~/Documents/TradingAgents-AShare/scripts/` 不同；若发布卡坚持拷到 `scripts/`，
   需自行改 plist 的 `{{REPO_DIR}}` 前缀并重跑本目录门禁。
