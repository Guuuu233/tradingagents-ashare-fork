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
**DAV-2219 按 DAV-2216 复审的 2🟡/2🟢 修正、DAV-2238 按 DAV-2237 §10 签字对齐热备口径后
已偏离该哈希**（新哈希见交付评论，或本机
`shasum -a 256 releases/launchd/storage_health.sh`），改动仅限热备预算口径与「不可测」处理，
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

热备预算口径（**DAV-2238 按 DAV-2237 §10 签字对齐**，取代 DAV-2219 的「同级 releases 目录 +
快照目录」口径）：合计 ≤ 15 GiB 为绿，超出报「红（超预算）」。**计入三项（单列）**：

| # | 预算项 | 路径 | 说明 |
|---|---|---|---|
| ① | 发布/回滚备份 | `$REL/backups`（`~/Documents/TradingAgents-AShare-releases/backups`） | 部署前 `*.db.zst` 备份、唯一回滚点 |
| ② | 共享快照基线 | `~/ta-snapshot/*.db.zst`（按文件累加） | DAV-1528 周基线（本地留最近 3 份） |
| ③ | 发布期 `.pre-slim` 副本 | `PROJ` / `REL` / `SNAP_DIR` 下 `**/*.pre-slim*` | 活库瘦身发布期的整库副本（当前本机 0 个） |

**不计入**（单列展示、不进合计）：活库 `data/tradingagents.db` 与整个工作树 `PROJ`；
`~/ta-snapshot/current.db`（未压缩工作副本，归 D-073 整库副本上限管）；
`~/ta-work/dav-2013/` 下的实验副本（归整库副本上限管，脚本**不扫** `~/ta-work`，
也不扫 `REL` 下的发布目录与日志）。

判定用**精确 KiB**（15 GiB = `15728640 KiB`），不做整数截断——`(15,16) GiB` 窗口
（如 15.5 GiB）必须判红，报告里同时给出精确 KiB 与一位小数 GiB。
口径沿革：改前为 `PROJ + releases + 快照 ≤ 15 GiB`（含 6 GiB 活库，从第一天就红，
DAV-2216 🟢-2）→ DAV-2219 改为 `releases + 快照` → DAV-2238（本版）收窄为上表三项。

### 枚举与测量失败：三态与判红（DAV-2238 + DAV-2239 🟡-1 返修）

预算项②③是「文件集合」，必须先**枚举**、再测量。三类状态写死如下（三处口径一致）：

| 根目录状态 | 行为 |
|---|---|
| **不存在** | 跳过，不算不可测；②报「0 个文件」/①报「目录不存在」 |
| **存在且可读** | 枚举（可为 0 个文件，按 0 计入） |
| **存在但不可读/不可进入（枚举失败）** | 记「⚠️ 不可测」并带 `find` 的失败原因（如 `Permission denied`），**不出「0 个文件」行**，热备行判**红** |

**枚举用 `find` 并检查退出码**（`releases/launchd/storage_health.sh` 的 `enumerate_files`）：
zsh 的 `(N)` 通配在目录不可读时**静默返回 0 个匹配**（无 stderr、无失败态），会把该预算项
按 0 计入并把热备行判绿（DAV-2239 🟡-1 实测：`chmod 000` 快照目录时候选绿 `rc=0`、
主干同夹具为「不可测 + `rc=2`」）；`find` 此时返回非零，据此 fail-closed。`find` 还会枚举
`.` 开头的路径段，隐藏目录/隐藏文件里的副本不会被漏掉（通配默认不匹配）。

`du` **失败、无输出或输出非数值时不得判绿**：该测量项同样记「⚠️ 不可测」并把 `du` 的 stderr
原文写进报告，热备行判「**红（不可测，fail-closed）**」；整个脚本以**退出码 2**
结束（报告本身照常产出，不是静默失败）。

- 任一测量项不可测即判红：上表三项，以及非预算项 `PROJ` / `current.db` 同样计入；
- 报告逐条点名不可测对象与原因，并在热备行给出「已测得 N KiB（不完整）」；
- **红优先序（DAV-2239 🟢-1）**：已测得部分和已超预算时，热备行出「**红（超预算）**」并把
  「另有 N 个测量项不可测，合计不完整（只多不少）」附在同一条——部分和只会**低估**真实合计，
  超预算判定可靠；只有「未超预算但测得不全」才出「红（不可测，fail-closed）」；
- **大文件清单不静默（DAV-2239 🟢-3）**：`find` 未能遍历全部目录时，清单小节追加一行
  「⚠️ 大文件清单不完整」；该清单不驱动灯色与退出码；
- 旧行为（`du … 2>/dev/null` 后再算术累加，空输出在 zsh 算术中按 0）会把 TCC 拒读的对象
  报成「合计约 0 GiB → 绿、`rc=0`」（假绿）。DAV-2219 改为「未知 ⚠️ + `rc=2`」，
  DAV-2238 再按安装前必修要求改为**判红**；DAV-2239 补上「枚举阶段」的同款收集。

## 报告内容与落盘（安装后）

- 每日 09:00 由 launchd 唤起 `storage_health.sh auto`；
- 报告：`{{LOG_DIR}}/storage-health-<YYYY-MM-DD>.md`（当日复跑会覆盖当日文件，与 DAV-1508 交付行为一致）；
- 脚本 stderr：`{{LOG_DIR}}/storage-health-stderr.log`；launchd 自身 exec/getcwd 错误：
  `{{LOG_DIR}}/launchd-storage-health-stderr.log`（分开留证，不让错误串污染当日 markdown）；
- 内容：日期、水位三色 + `df -h` 人读行、热备预算合计（精确 KiB）+ 预算三项逐项占用
  （`backups/`、`*.db.zst` 文件数、`.pre-slim` 文件数）+ 非预算项单列
  （工作树/活库 `PROJ`、快照目录未压缩 `current.db`）+ 不可测对象及其原因清单 +
  ≥200 MiB 无主/大文件清单
  （按路径归类给出「活库/发布备份/共享快照/`.pre-slim`/日志/依赖产物/待人工判断」）；
- 退出码：全量测得且热备预算未超 = 0；有测量项不可测 = 2（热备行「红（不可测，
  fail-closed）」，不得据此判绿）。**超预算本身不改退出码**（仍为 0）：退出码表达的是
  「测量完整性」，而「超预算」的报警形态 = **报告文本「红（超预算）」+ 人工/巡检读取**
  （脚本无推送通道；是否接通知/独立退出码属发布卡范围，DAV-2239 🟢-2 已记录）。
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

DAV-2238/2239 两轮的变异（均在临时副本上做，未改仓库文件）：判红退回「未知」→ **7 条**失败；
判定退回整数截断 → **5 条**；合计漏计 `.pre-slim`/`backups` → **4 / 13 条**；快照项改为模式放宽
（含 `current.db`）→ **2 条**；`.pre-slim` 枚举模式打错 → **6 条**；去掉「空输出」判定 → **1 条**；
**撤掉枚举闸门（枚举失败静默返回 0，M10）→ 10 条失败**；隐藏路径被跳过（模拟旧 glob）→
**3 条**；超预算前置被撤 → **2 条**；大文件清单不留证 → **1 条**；追加无关行/复原后 → **0 条**
（证明不是随机红）。

## 安装（**本卡不执行**，另走发布卡，排在 10-19 之后）

硬前置（任一不满足即中止）：

1. **TCC 实测**：DAV-2180 已实测 launchd 对 `~/Documents` 全树拒读（`/bin/bash` 唤起时
   `getcwd`、`ls ~/Documents` 均 `Operation not permitted`）。本模板沿用 `/bin/zsh -c`，
   而脚本与报告目录默认都在 `~/Documents` 下——必须先用临时探针确认 **zsh 在 launchd 下
   可读 `{{REPO_DIR}}`、可写 `{{LOG_DIR}}`**；否则热备行会长期判「红（不可测）」、脚本每次以
   退出码 2 结束（DAV-2238 后不再静默报绿），而 `{{LOG_DIR}}` 不可写时报告根本落不了盘。
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
