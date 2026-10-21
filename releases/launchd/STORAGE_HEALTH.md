# 存储健康巡检 I（storage_health.sh）— 脚本 + launchd 模板（只入库，未安装）

本目录收纳宿主每日存储巡检脚本与其 launchd 模板。**本卡只推分支 + 开复审：脚本与模板
都已入仓，但未安装、未启用**——`~/Library/LaunchAgents/` 下的对应 plist 已于
DAV-2237 安装后按总工 10-21 11:05 令**卸载**（plist 保留待重装），安装另走发布卡，
排在 **10-22 读数之后**。

## 文件清单

| 文件                                                      | 作用                                                                 |
| --------------------------------------------------------- | -------------------------------------------------------------------- |
| `releases/launchd/storage_health.sh`                      | 巡检脚本本体（zsh，`daily` / `weekly` / `auto` 三种模式）            |
| `releases/launchd/storage_health_launcher.py`             | **DAV-2310** launchd 启动器（python → exec 巡检脚本；TCC responsible process） |
| `releases/launchd/com.tradingagents.storage-health.plist` | launchd 模板（含 `{{PYTHON_BIN}}` / `{{REPO_DIR}}` / `{{LOG_DIR}}` 占位符，**未安装**） |
| `releases/launchd/tests/test_storage_health.sh`           | 行为门禁：`bash releases/launchd/tests/test_storage_health.sh`       |
| `releases/launchd/tests/test_storage_health_launcher.py` | **DAV-2310** 启动器 + plist 模板门禁（pytest，见下方「运行」）      |
| `releases/launchd/STORAGE_HEALTH.md`                      | 本说明                                                               |

DAV-2310 只动上表里的**启动器**与**plist 模板**两处；**巡检脚本 `storage_health.sh` 一字未改**
（sha256 仍为 DAV-2237 总控 pin 的 `fc694aeac4e85ac600053dd8a9522f3ef8776a2e3c0fec752f6f7e321fd60089`，
由 `tests/test_storage_health_launcher.py` 逐次断言）。

DAV-2215 入库时脚本与 DAV-1508 交付件**逐字节一致**（旧 `sha256 d1936831b94d677bf79eefc826b9fb5186a84bba8ea8dc59dee9749ef5923037`）；
**DAV-2219 按 DAV-2216 复审的 2🟡/2🟢 修正、DAV-2238 按 DAV-2237 §10 签字对齐热备口径、
DAV-2239/DAV-2250/DAV-2252 按 🟡 打回收口「枚举不到 ≠ 占用为 0」（含 ① 根链接、
悬空根链接两条通道）后
已偏离该哈希**（新哈希见交付评论，或本机
`shasum -a 256 releases/launchd/storage_health.sh`），改动仅限热备预算口径与「不可测」处理，
水位阈值、每周基线等逻辑行未动。

## 水位口径（总控 2026-10-17 令）

| 可用空间（`df -g /System/Volumes/Data` 第 4 列，GiB） | 灯色 | 动作                           |
| ----------------------------------------------------- | ---- | ------------------------------ |
| ≥ 100                                                 | 绿   | 无                             |
| 60 – 99                                               | 黄   | **只记录**，不唤醒、不停副本   |
| < 60                                                  | 红   | 立即唤醒；**停止创建整库副本** |

旧口径（≥150 绿 / 120–149 黄 / <120 红）已按 10-17 令彻底移除，脚本内只剩 100/60 两个阈值。
门禁用假 `df` 覆盖 150/120/100/99/80/60/59 七个点，并显式断言「120 不再判黄、80 不再判红」，
防止回退到旧阈值。

热备预算口径（**DAV-2238 按 DAV-2237 §10 签字对齐**，取代 DAV-2219 的「同级 releases 目录 +
快照目录」口径）：合计 ≤ 15 GiB 为绿，超出报「红（超预算）」。**计入三项（单列）**：

| #   | 预算项                  | 路径                                                                  | 说明                                      |
| --- | ----------------------- | --------------------------------------------------------------------- | ----------------------------------------- |
| ①   | 发布/回滚备份           | `$REL/backups`（`~/Documents/TradingAgents-AShare-releases/backups`） | 部署前 `*.db.zst` 备份、唯一回滚点        |
| ②   | 共享快照基线            | `~/ta-snapshot/*.db.zst`（按文件累加）                                | DAV-1528 周基线（本地留最近 3 份）        |
| ③   | 发布期 `.pre-slim` 副本 | `PROJ` / `REL` / `SNAP_DIR` 下 `**/*.pre-slim*`                       | 活库瘦身发布期的整库副本（当前本机 0 个） |

②③ 是「文件集合」，先枚举再测量，符号链接按 **目录级不进入 / 文件级解引用** 双层口径处理
（DAV-2250，详见下文「枚举与测量失败」）：链接后方的普通文件副本计入预算，链接后方
整棵目录树不进入预算；名字匹配但按普通文件测不了的条目一律记「不可测」判红。

① 的测量对象 `$REL/backups` **本身就是预算项**（不是「枚举到的条目」），因此它若被整成
目录级符号链接（迁到外置盘/另一卷、历史整理留下的链接）时，按**链接的真实目标实测**
（`du -sk -H`，DAV-2252 🟡-1）；根为**悬空**链接（目标不在，如外置卷未挂载）则记
「不可测」判**红**，不得写成「目录不存在」静默按 0（DAV-2252 🟡-2）。

**不计入**（单列展示、不进合计）：活库 `data/tradingagents.db` 与整个工作树 `PROJ`；
`~/ta-snapshot/current.db`（未压缩工作副本，归 D-073 整库副本上限管）；
`~/ta-work/dav-2013/` 下的实验副本（归整库副本上限管，脚本**不扫** `~/ta-work`，
也不扫 `REL` 下的发布目录与日志）。

判定用**精确 KiB**（15 GiB = `15728640 KiB`），不做整数截断——`(15,16) GiB` 窗口
（如 15.5 GiB）必须判红，报告里同时给出精确 KiB 与一位小数 GiB。
口径沿革：改前为 `PROJ + releases + 快照 ≤ 15 GiB`（含 6 GiB 活库，从第一天就红，
DAV-2216 🟢-2）→ DAV-2219 改为 `releases + 快照` → DAV-2238（本版）收窄为上表三项。

### 枚举与测量失败：三态与判红（DAV-2238 + DAV-2239 🟡-1 返修）

预算项①②③是「文件集合/目录」，必须先**枚举或解析**、再测量。状态写死如下
（① ② ③ 与两个非预算项的调用口径一致）：

| 根路径状态                                                   | 行为                                                                                                                                                       |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **不存在**（上级目录可读，确实没有）                         | 跳过，不算不可测；②报「0 个文件」/①报「目录不存在」                                                                                                        |
| **存在且可读**                                               | 枚举/测量（可为 0 个文件，按 0 计入）                                                                                                                      |
| **存在但不可读/不可进入（枚举失败）**                        | 记「⚠️ 不可测」并带 `find` 的失败原因（如 `Permission denied`），**不出「0 个文件」行**，热备行判**红**                                                    |
| **存在但路径存在性无法判断**（上级目录不可读致 stat EACCES） | 记「⚠️ 不可测」判**红**（不得写成「不存在」）                                                                                                              |
| **本身是悬空符号链接**（`-L` 且 `! -e`，DAV-2252 🟡-2）      | 记「⚠️ 不可测」判**红**（`path_state` = unknown）：该名字断言「备份/快照在这里」，目标不在（外置卷未挂载、目标被移走、只留链接）——不得走「不存在」静默跳过 |

**枚举用 `find` 并检查退出码**（`releases/launchd/storage_health.sh` 的 `enumerate_files`）：
zsh 的 `(N)` 通配在目录不可读时**静默返回 0 个匹配**（无 stderr、无失败态），会把该预算项
按 0 计入并把热备行判绿（DAV-2239 🟡-1 实测：`chmod 000` 快照目录时候选绿 `rc=0`、
主干同夹具为「不可测 + `rc=2`」）；`find` 此时返回非零，据此 fail-closed。`find` 还会枚举
`.` 开头的路径段，隐藏目录/隐藏文件里的副本不会被漏掉（通配默认不匹配）。

`du` **失败、无输出或输出非数值时不得判绿**：该测量项同样记「⚠️ 不可测」并把 `du` 的 stderr
原文写进报告，热备行判「**红（不可测，fail-closed）**」；整个脚本以**退出码 2**
结束（报告本身照常产出，不是静默失败）。

#### 符号链接双层口径：目录级不进入 / 文件级解引用（DAV-2250 🟡-1 返修）

上一版（候选 `72e434b0`）两次 `find` 用了 BSD 默认的 `-P` 且未把符号链接纳入匹配集，
于是链接后方的副本被**静默按 0 计**（实测：`snap/link-snap.db.zst → 快照根之外的 20 MiB 实体`
使②报「0 个文件」、`proj/link-rel → 含 .pre-slim 实体的目录`使③报「0 个文件」，两者均 `rc=0` 报绿），
与 DAV-2239 🟡-1「枚举不到 ≠ 占用为 0」是同一类逃逸通道。现写死两层口径：

| 层级       | 口径                                                               | 实现                                                                                      |
| ---------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| **目录级** | **不进入**符号链接目录（链接后方的整棵目录树不进预算）             | `find -P`（BSD 默认即 `-P`，显式写出）                                                    |
| **文件级** | **解引用**：名字匹配的符号链接指向普通文件时，按目标文件实测并计入 | 匹配集 `-type f -o -type l`，循环内用 `[ -f ]`（会解引用）确认可测，再交 `du -sk -H` 测量 |

三个配套要点（均为实测得到的必然选择，不是风格偏好）：

1. **`du` 必须显式 `-H`**：macOS/BSD `du` **默认不解引用命令行上的符号链接**——
   `du -sk link-snap.db.zst`（link→20 MiB 文件）实测输出 `0` 且 `rc=0`；只有 `du -sk -H`
   才解析到目标（实测 `20480 KiB`）。「逐文件 `du`」若漏了 `-H` 仍会假绿，所以门禁里
   既有桩侧断言（桩按真 `du` 语义实现：无 `-H` 时符号链接计 0），也有**不经桩的真 `du` 断言**
   （夹具：8 MiB 实体 + 文件级链接 → ② 必须 ≥ 4096 KiB）；
2. **根目录本身是符号链接时先 `cd -P` 解析**：实测 `find -P <链接根> …` **静默返回 0 个匹配、`rc=0`**
   （又一条假绿通道）。故枚举前先用 `$( { cd -P "$root" && pwd; } 2>&1 )` 取物理路径，
   解析失败则记「不可测」判红；
3. **名字匹配、但按普通文件测不了 ⇒ 不可测判红**（悬空链接、指向目录的链接、
   枚举后已不可访问的条目）——宁可误红：该名字断言「这里有一份副本」，既不能确认、
   也不得按 0 计入（与 🟢-2 的「窄权限下宁可误红」同一方向）。
4. **① 的根链接同样解引用**（DAV-2252 🟡-1）：`du_kb` 统一带 `-H`。① 的测量对象
   `$REL/backups` 本身就是预算项而非遍历条目，因此它被整成目录级链接时按目标目录实测，
   而不是「不进入」。实测（真 `du`）：`du -sk <备份目录链接>` = **0 KiB** / `rc=0`（修复前
   候选在报告里写「计入预算①…：0.0 GiB（0 KiB）」并判绿），`du -sk -H <同一链接>` = **51800 KiB**。
   同一函数也用于 `PROJ` 与 `current.db`，`-H` 对真实目录测量值无影响（实测 `51200 KiB → 51200 KiB`）。
5. **根为悬空符号链接 ⇒ 不可测判红**（DAV-2252 🟡-2）：`[ -e ]` 会解引用，悬空链接返回假，
   原实现因此滑到 `absent` 分支——①写「目录不存在」、②③写「0 个文件」，整行绿、`rc=0`。
   现 `path_state` 先判 `[ -L ] && ! [ -e ]` ⇒ `unknown`（判红不可测）。该通道自 DAV-2239
   引入 `path_state` 时即存在，**非 DAV-2250 引入的回归**；本机生产路径实测均为真实目录
   （`ls -ld`），故属潜在通道，但方向是假绿，一并收口。

实测对照（同一夹具、同一机，2026-10-18）：候选 `72e434b0` →「② 0 个文件 / ③ 0 个文件 / 合计 0 KiB / `rc=0` 绿」；
本版 →「② 1 个文件 = 20480 KiB / ③ 0 个文件（目录级链接不进入）/ 合计 20480 KiB / `rc=0` 绿」，
并把 20 MiB 列入预算——不再有静默按 0 的通道。

DAV-2252 轮补充实测（真 `du`、真目录）：

| 夹具                                   | 修复前（候选 `f9667fc4`）             | 本版                                                              |
| -------------------------------------- | ------------------------------------- | ----------------------------------------------------------------- |
| ① `$REL/backups` → 目录（50 MiB 实体） | 「① 0.0 GiB（0 KiB）」+ 热备绿 `rc=0` | 「① 51800 KiB」+ 绿 `rc=0`（真 `du`：默认 0 / `-H` 51800）        |
| ① `$REL/backups` = 悬空链接            | 「① 目录不存在」+ 热备绿 `rc=0`       | **红（不可测，fail-closed）**，点名路径 +「悬空符号链接」，`rc=2` |
| ② `$SNAP_DIR` = 悬空链接               | 「② 0 个文件」+ 热备绿 `rc=0`         | **红（不可测，fail-closed）**，②③各一条不可测项，`rc=2`           |

- 任一测量项不可测即判红：上表三项，以及非预算项 `PROJ` / `current.db` 同样计入；
- 报告逐条点名不可测对象与原因，并在热备行给出「已测得 N KiB（不完整）」；
- **红优先序（DAV-2239 🟢-1）**：已测得部分和已超预算时，热备行出「**红（超预算）**」并把
  「另有 N 个测量项不可测，合计不完整（只多不少）」附在同一条——部分和只会**低估**真实合计，
  超预算判定可靠；只有「未超预算但测得不全」才出「红（不可测，fail-closed）」；
- **大文件清单不静默（DAV-2239 🟢-3）**：`find` 未能遍历全部目录时，清单小节追加一行
  「⚠️ 大文件清单不完整」；该清单不驱动灯色与退出码；
- **大文件清单的链接口径（DAV-2252 🟢-1）**：`find -size` 在 `-P` 下只看**链接自身**大小
  （实测 8~113 字节），所以清单单靠 `-type f -size +N` 会漏掉「≥阈值 的副本只以符号链接
  存在」的形态。现补一遍：`find -P … -type l` ⊕ `stat -L -f '%z %N'`（macOS `stat` **默认
  lstat**，不加 `-L` 拿到的是链接自身字节数）按目标字节数筛，命中者再用 `du -h -H` 生成与
  上一遍一致的展示行；只处理指向普通文件的链接（目录链接不展开）。阈值默认 `204800 KiB`
  （= 200 MiB），可用 `STORAGE_HEALTH_BIGFILE_MIN_KB` 注入小值——**仅供门禁**用 MiB 级夹具
  覆盖同一路径，生产不设该变量；清单抬头随阈值动态显示（默认仍逐字为「≥200 MiB」；
  注入值 < 1024 KiB 时抬头会显示「≥0 MiB」，仅门禁会出现，生产默认下不可能）；
  第二遍对**悬空链接**静默不列（`stat -L` 报错被丢弃，目标字节数本不可测），与该清单
  「不驱动灯色与退出码」的定位一致（DAV-2264 🟢-c 注记）；
- **拼接换行必须走变量（DAV-2264 🟡-1）**：zsh 5.9 对 **`${var:+word}` 的替换词不解析 C 引号**
  （实测 `${x:+$'\n'}` 原样输出 5 个字面字符 `$'\n'`；而 `%%`/`##` 模式位会解析）。
  故「实体命中一遍 + 链接命中一遍」拼接时必须 `local nl; nl=$'\n'` 后用 `${bf_out:+${nl}}`；
  直拼会让两行粘连成一行、`case` 归因失致（实测量型：实体命中 + 链接命中并存时
  清单只剩 0 条完整条目行，并被 `$'` 残渣污染）。门禁 `[4h]` 已用「两类命中并存」夹具钉住，
  M-M/M-N 两个变异（恢复直拼 / 撤分隔符）均 FAIL；
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
  ≥200 MiB 无主/大文件清单（含只以符号链接存在、目标 ≥阈值 的副本）
  （按路径归类给出「活库/发布备份/共享快照/`.pre-slim`/日志/依赖产物/待人工判断」）；
- 退出码：全量测得且热备预算未超 = 0；有测量项不可测 = 2（热备行「红（不可测，
  fail-closed）」，不得据此判绿）。**超预算本身不改退出码**（仍为 0）：退出码表达的是
  「测量完整性」，而「超预算」的报警形态 = **报告文本「红（超预算）」+ 人工/巡检读取**
  （脚本无推送通道；是否接通知/独立退出码属发布卡范围，DAV-2239 🟢-2 已记录）。
- 周三另加「每周基线」小节：从 `~/ta-snapshot/` 取当周最后一份
  `tradingagents-*.db.zst`，记录大小 + SHA256，有 `quark` CLI 时上传到
  `/TradingAgents-AShare/baselines`，否则在报告里要求人工上传。
  快照项本身可能是符号链接（历史整理/迁移常见）：大小用 `du -h -H`、字节数用 `stat -L -f %z`
  （DAV-2252 🟢-1）——不加 `-H` 会显示 `大小：0B`，不加 `-L`（macOS `stat` 默认 lstat）
  会拿链接自身字节数当目标大小；`shasum` 本就读穿链接，无需特殊处理。

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

DAV-2250 符号链接轮（该版树 `f9667fc4` 控制组 `pass=113 fail=0`，均在临时副本上做，未改仓库文件）：
撤文件级解引用（`du_kb_sum` 去掉 `-H`）→ **2 条**失败（含真 `du` 那条）；撤文件级链接枚举
（`find` 回 `-type f`）→ **10 条**；撤根目录链接解析（`cd -P` 短路）→ **3 条**；
目录级改为完全跟随（`find -P`→`find -L`，即方案 2 语义）→ **8 条**；
撤「名字匹配但测不了→不可测」判定 → **6 条**；符号链接不判红（不可测退化为静默跳过）→ **7 条**；
重做 M10（撤枚举退出码闸门）→ **2 条**（`[4b]`/`[4c]` 新增断言：find 报错不得被当成「枚举到的条目」）。

DAV-2252 轮（该版树 `61a96ed4` 控制组 `pass=125 fail=0`，均在临时副本上做，未改仓库文件）：
撤①的 `du -H`（`du_kb` 不解引用目录级链接）→ **2 条**失败（桩侧 ① 计量 + 真 `du` ① ≥4096 那条）；
撤「悬空符号链接 → unknown」（滑回 `absent` 静默跳过）→ **8 条**（①「目录不存在」、②「0 个文件」、
两个 `rc=2` 断言与点名断言同时命中）；其余六类（M-A..M-G）复跑失败条数同上；控制组/复原 →
`125 pass / 0 fail`（证明不是随机红）。

DAV-2252 🟢 轮（该版树 `e99e812d` 控制组 `pass=135 fail=0`，均在临时副本上做，未改仓库文件）：
撤大文件清单的「链接一遍」（`bf_lnk=""` 短路）→ **1 条**失败（「只以符号链接存在的大副本
也进大文件清单」）；周基线 `du -h -H` → `du -h` → **2 条**（`大小：0B` 不得出现 + `大小：4.0M`）；
周基线 `stat -L` → `stat` → **1 条**（目标字节数断言）；M-A..M-I 复跑失败条数不变；
控制组/复原 → `135 pass / 0 fail`。

DAV-2264 轮（本版树控制组 `pass=138 fail=0`，均在临时副本上做，未改仓库文件）：
**恢复 `$'\n'` 直拼**（`${bf_out:+${nl}}` → `${bf_out:+$'\n'}`）→ **4 条**失败（实体行/链接行/
「恰 2 条独立条目行」/「不得出现字面 `$'` 残渣」）；**撤分隔符**（`nl=''`）→ **3 条**；
其余变异复跑：M-A 2、M-B 10、M-C 3、M-D 8、M-E 2、M-F 6、M-G 7、M-H 2、M-I 10、
M-J 2（双命中夹具下）、M-K 2、M-L 1；控制组/复原 → `138 pass / 0 fail`。

## TCC responsible process：DAV-2310 探针实测（launchd 上下文）

### 为什么

DAV-2237 首跑 `exit=2`（fail-closed 判红）的根因：**launchd 下 `/bin/zsh` 没有
`~/Documents` 的 TCC 授权**。巡检对象（活库、发布备份 `backups/`、发布期 `.pre-slim`
副本）全在 `~/Documents` 之下，launchd 唤起的 zsh 跑 `du` / `find` 一律
`Operation not permitted` → 4 个测量项不可测 → 热备行判红（DAV-2238 后不再静默报绿）。

macOS TCC 按 **responsible process** 判定授权：**由已获授权的进程启动的子进程继承
该授权**。同机 `com.tradingagents.ashare` 以 `~/ta-venv/py310/bin/python` 作
`ProgramArguments[0]` 就能持有 `~/Documents/…/tradingagents.db` —— 但这是旁证，
**不是**对本问题的直接测量。DAV-2310 用临时探针在 launchd 上下文直接测量。

### 探针设计（4 组对照，覆盖三层）

探针 plist（临时）以 `~/ta-venv/py310/bin/python`（uv 管理的 cpython-3.10.20，
ad-hoc 签名）作 responsible process，**只读**地测：`ls -d`（目录枚举）、`pwd`
（getcwd）、`du -sk`（目录占用）、`find -P`（递归枚举）、`stat`（已知路径大小）、
`head -c 1`（读内容，**只读 1 字节，不落盘、不复制**）。

**必须覆盖三层**（只测第一层会得出假阳性）：① python 自己能读 `~/Documents`；
② python 起的 zsh 能读；③ 该 zsh 跑的 `du` / `find` 能读。

| 组别 | 形态                                                       | 结果                       |
| ----- | ---------------------------------------------------------- | -------------------------- |
| A     | `/bin/zsh` 直接当 responsible process（**对照组**）          | **FAIL**（复现 D2 原象）   |
| B     | python **spawn** `/bin/zsh`（python 仍是父进程）             | PASS                       |
| C     | python **exec** `/bin/zsh`（映像被替换，同一 PID）           | PASS ← **本卡采用**        |
| D     | 同 C，但 `WorkingDirectory` 在 `~/Documents` 下              | PASS                       |

对照组 A 的实测输出（逐字，与 DAV-2237 首跑的 D2 一致）：

```
L2 du -sk PROJ rc=1 out_first=du: …/TradingAgents-AShare: Operation not permitted
L2 du -sk -H REL/backups rc=1 out_first=du: …/-releases/backups: Operation not permitted
L3 find rc=1 out_first=find: …/TradingAgents-AShare: Operation not permitted
L5 head -c 1 db rc=1 err=head: …/tradingagents.db: Operation not permitted
```

对照组 A 与 C 的**唯一差别**是 `ProgramArguments[0]` 与进程形态；A 失败、C 通过 →
「授权随 responsible process 传递」这一前提**成立**。

**为什么选 C（exec）而不是 B（spawn）**：exec 后进程映像被 zsh 取代，**脚本退出码天然
就是本进程退出码**（无需中转、不需额外捕获），且与已实测通过的探针形态逐字一致。
D 组另外确认了 `WorkingDirectory` 设在 `~/Documents` 下不会重现 DAV-2180 记录的
getcwd 拒读。

### 探针纪律与清理

- 探针 plist 放在 `~/ta-work/`（**不在** `~/Library/LaunchAgents/`），测完**立即**
  `bootout` + **删除 plist**；回读确认 `Could not find service …`；
- 探针输出落 `~/ta-work/`（授权区之外），**不写 `~/Documents`**；
- 探针**不碰生产库**：只读目录与 1 字节内容判定，不读、不写、不复制 `tradingagents.db`；
- 4 组作业全部卸载（`bootout` rc=0），`launchctl list` / `print-disabled` 无残留，
  `ps` 无残留进程。

### 换解释器必须重跑探针

探针结论**只对被测的那条解释器路径成立**。C 组测的是
`~/ta-venv/py310/bin/python`（realpath =
`~/.local/share/uv/python/cpython-3.10.20-macos-aarch64-none/bin/python3.10`）。
**换一个解释器就必须重跑探针**，不得凭推测替换 plist 里的 `{{PYTHON_BIN}}`。

## 启动器 `storage_health_launcher.py`（DAV-2310）

职责刻意最小（卡面三条）：

1. **路径解析**：巡检脚本取 `STORAGE_HEALTH_SCRIPT`（plist 注入）；**未设时取与启动器
   同目录的 `storage_health.sh`**（由 `__file__` 推导，**不写死绝对路径**——DAV-2292
   「路径写死 → 改 env/脚本目录推导」的同款教训）。报告目录取 `STORAGE_HEALTH_LOG_DIR`；
   **不自动创建**（目录缺失即拒绝启动，避免 launchd 下留无主副作用）。
2. **参数透传**：`argv[1:]` 逐字转交脚本（不解析、不重排、不补默认值）。
3. **退出码透传**：脚本退出码即本进程退出码（exec 形态天然如此）。

输出落盘替代旧 plist 的 shell 重定向，**语义逐字一致**：
当日报告 `<log-dir>/storage-health-<YYYY-MM-DD>.md`（**覆盖**，旧行为是 `>`）；
脚本 stderr `<log-dir>/storage-health-stderr.log`（**追加**，旧行为是 `2>>`）。

启动器**自身**的失败（未设日志目录 / 目录不存在 / 脚本不存在 / 日志打不开 / `exec` 失败）
一律 **exit 64**（fail-closed，拒绝启动）——**64 是「拒绝启动」，不是巡检结论**，
与脚本自身的 0 / 2 不撞码。

只读纪律：启动器只**打开两个输出文件**并 exec 巡检脚本，**不删、不改、不复制任何数据
文件**，**不读数据库内容**。

### 门禁与变异验证

`releases/launchd/tests/test_storage_health_launcher.py`（26 条）：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest \
    releases/launchd/tests/test_storage_health_launcher.py -q -p no:randomly
```

覆盖：路径解析（env 优先 / 同目录兜底 / 脚本缺失 → 64）、参数透传（逐字、不补默认）、
退出码透传（0 / 2 / 3 / 42）、报告覆盖与 stderr 分离、plist 契约（占位符集合、
`ProgramArguments` 指向启动器、占位符替换后 `plutil -lint` + `plistlib` 可解析、
无 KeepAlive、`{{REPO_DIR}}` 三处同源、无硬编码宿主机路径）。

**变异验证（真闸证明）**：

| 变异                                              | 失败条数 |
| ------------------------------------------------- | -------- |
| 启动器丢掉参数（`args = []`）                    | 1        |
| 退出码不透传（总是返回 0）                        | 3        |
| 路径解析退回写死绝对路径（忽略 env 与目录推导）   | 11       |
| fail-closed 失效（缺日志目录也照跑）              | 1        |
| plist 退回 `/bin/zsh`（DAV-2237 旧形态）          | 1        |
| plist 加 `KeepAlive`                             | 1        |
| 复原                                               | 0（26 全绿） |

## 安装（**本卡不执行**，另走发布卡，排在 **10-22 读数之后**）

硬前置（任一不满足即中止）：

1. **TCC 实测（DAV-2310 已完成）**：DAV-2180 实测 launchd 对 `~/Documents` 全树拒读。
   DAV-2310 用 4 组对照探针在 launchd 上下文实测：**由持权解释器当 responsible process
   时，其 zsh 子进程可读 `~/Documents`**（C/D 组 PASS，对照组 A 复现 FAIL）——
   详见上方「TCC responsible process：DAV-2310 探针实测」。
   **硬要求：解释器必须是探针实测过的那条路径**（`~/ta-venv/py310/bin/python`）；
   换解释器必须**重跑探针**。
2. 占位符全量替换并校验残留为 0（**DAV-2310 起共 3 个**）：
   `grep -cE '\{\{[A-Z_]+\}\}' ~/Library/LaunchAgents/com.tradingagents.storage-health.plist`
   必须输出 `0`；`plutil -lint` 必须 OK。
3. 报告目录存在且可写；首次手动跑一次 `storage_health.sh daily`，确认报告落盘且耗时远小于一天。
4. （DAV-2223）`bootstrap` 之前先跑 `releases/launchd/plist_log_preflight.py`
   （**用 python 运行，不是 bash**）：FAIL 即停，不得继续。

```bash
# 安装（发布卡执行，勿未经授权照抄）
# 注：DAV-2310 起 plist 有 3 个占位符（PYTHON_BIN / REPO_DIR / LOG_DIR）。
#     PYTHON_BIN 必须是探针实测持权的那条解释器路径。
#     LOG_DIR 用 DAV-2237 首跑实际装过的位置（不在 ~/Documents 下）。
mkdir -p ~/Library/LaunchAgents
mkdir -p /Users/davidliu/ta-watch/storage-health
sed -e "s|{{PYTHON_BIN}}|/Users/davidliu/ta-venv/py310/bin/python|g" \
    -e "s|{{REPO_DIR}}|/Users/davidliu/Documents/TradingAgents-AShare|g" \
    -e "s|{{LOG_DIR}}|/Users/davidliu/ta-watch/storage-health|g" \
    releases/launchd/com.tradingagents.storage-health.plist \
    > ~/Library/LaunchAgents/com.tradingagents.storage-health.plist
grep -cE '\{\{[A-Z_]+\}\}' ~/Library/LaunchAgents/com.tradingagents.storage-health.plist   # 必须 0
plutil -lint ~/Library/LaunchAgents/com.tradingagents.storage-health.plist                 # OK
# DAV-2223 前置检查（FAIL 即停）
python3 releases/launchd/plist_log_preflight.py \
    ~/Library/LaunchAgents/com.tradingagents.storage-health.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.tradingagents.storage-health.plist
launchctl print gui/$UID/com.tradingagents.storage-health | head -20                       # 应注册成功
launchctl kickstart -k gui/$UID/com.tradingagents.storage-health                           # 手动首跑
ls -l /Users/davidliu/ta-watch/storage-health/storage-health-$(date +%F).md
```

回退：`launchctl bootout gui/$UID/com.tradingagents.storage-health`，
再 `rm ~/Library/LaunchAgents/com.tradingagents.storage-health.plist`（仅安装后回退时才需要）。

> **DAV-2237 首跑的坑位**（重装时注意）：
> 1. `bootout` 只卸载当前会话，`plist` 留在 `~/Library/LaunchAgents/` 会在**下次登录时
>    被 launchd 自动装载、作业复活** → 需 `launchctl disable gui/$UID/com.tradingagents.storage-health`
>    写持久停用位（回退时 `enable` 即可）；
> 2. 首跑当日是周三，`auto` 模式会**同时**跑「每周基线」小节（本机无 quark CLI →
>    报告里提示人工上传，无云端副作用）——这是预期行为，不是故障。

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
5. **DAV-2310 遗留缺口（发布卡需处理）**：
   - **D6 持久停用未做**：DAV-2237 的 plist 仍在 `~/Library/LaunchAgents/`，
     `bootout` 只卸载当前会话，`launchctl print-disabled` 里**无**该 label →
     **下次登录/重启作业会自行复活**、继续每日 `exit=2` 判红。
     重装前需 `launchctl disable gui/$UID/com.tradingagents.storage-health`
     （或改名留档），否则「先卸载等修复」的意图失效。
   - **`{{PYTHON_BIN}}` 是新占位符**：旧安装步骤只有 2 个占位符（REPO_DIR / LOG_DIR），
     按旧步骤 `sed` 会漏替换它 → 装上去的 plist 会以 `{{PYTHON_BIN}}` 字面量当解释器，
     **launchd 装不上**。安装步骤必须改成 3 个占位符，并核
     `grep -cE '\{\{[A-Z_]+\}\}' …` 为 0。
   - **首次重装的「真跑」验证未做**：DAV-2310 只做了 launchd 上下文**探针**
     （证明 python → zsh 能读 `~/Documents`），**没有**跑真正的
     `storage_health.sh auto` 在 launchd 下（该卡明确「不安装」）。重装卡必须
     `kickstart` 一次并回读退出码应为 **0**（不再是 2）。
