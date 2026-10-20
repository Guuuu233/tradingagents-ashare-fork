# DAV-2284 — 标签定时任务凭据路径 TCC 兼容（TA_ENV_FILE）

本文件是 `scripts/phase2/run_expiry_label_daily.sh` +
`scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist` +
`scripts/phase2/ta_secrets_install.sh` 的安装/口径说明，并登记卡面第 5、6 项
（「更换密钥两处都要改」的宿主机文案、其余 `/bin/bash` 定时任务的排查结论）。

> **本卡只推分支，不安装。** 安装动作由发布卡执行；`TA_ENV_FILE` 的目标副本
> （`~/ta-secrets/ta.env`）本次未创建，宿主机侧任何 plist / `.env` / `RESTART.md`
> 均未改动。

## 1. 根因

`com.davidliu.ta-p4-expiry-labels` 于 2026-10-19 20:10 以 **exit 13** 失败。
真实原因不是「缺 token」，而是 **macOS TCC 拒绝 launchd 唤起的 `/bin/bash`
读取 `~/Documents` 下任何文件内容**：

```
$ cat ~/ta-p4-2e1e199f/work/phase2-labels/launchd.err.log
grep: /Users/davidliu/Documents/TradingAgents-AShare/.env: Operation not permitted
/Users/davidliu/ta-p4-labels-cb82987b/scripts/phase2/run_expiry_label_daily.sh: line 133:
    /Users/davidliu/Documents/TradingAgents-AShare/.env: Operation not permitted

$ cat ~/ta-p4-2e1e199f/work/phase2-labels/ALERT.launchd
exit_code=13
reason=TUSHARE_TOKEN missing after sourcing /Users/davidliu/Documents/TradingAgents-AShare/.env …
```

旧代码把 `grep` 的返回码当成布尔量用（`if grep -vEq …`），**rc=2（读不到）被当成
rc=1（文件干净）**，于是继续 `source`，`source` 同样 EPERM，最后落到
「TUSHARE_TOKEN missing → exit 13」。**授权故障被误报成数据缺失**，这本身是比
路径问题更严重的一处缺陷，故本卡把「读被拒」独立成 fail-closed 告警（exit 18）。

**不是** `/bin/bash` 整体不可用：同一台机器上 `/bin/bash` 启动的
`com.davidliu.ta-logsize-watch` 能正常采到 `~/Documents` 下日志的大小 —— 因为它
只用 `stat(2)`。TCC 的数据保护拦的是 `open()`（读内容）与目录枚举（`getdirentries`，
`ls`/`du`），不拦已知路径的 `stat()`。实测旁证：

- `logsize-daily.csv` 2026-10-19 15:50 采样：`svc|launchd-stderr` 等
  `~/Documents/…` 路径的 size 为真实值（5622 / 32450 / 4518 …），非 `-1`；
- 同一脚本 2026-10-18 04:15 的 `dir_per_run_total`（当时用 `du -sk
~/Documents/TradingAgents-AShare/work/daily-batch`）为 `-1`，其注释已写明
  「launchd agent is denied recursive enumeration there (TCC: du returns
  Operation not permitted)」—— 说明该不对称现象在本机已被独立复现并绕开。

因此修法不是给 `/bin/bash` 开完全磁盘访问（授权面过大，卡面明确排除），而是
**把凭据副本搬到 `~/Documents` 之外**。

## 2. 改动清单（对照卡面 1–6）

| #   | 卡面要求                                     | 落在哪                                                                                                                                                                                                                                |
| --- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `ENV_FILE="${TA_ENV_FILE:-原路径}"`          | `run_expiry_label_daily.sh` 路径段；默认值仅为「终端手工跑」保留                                                                                                                                                                      |
| 2   | plist 增加 `TA_ENV_FILE=~/ta-secrets/ta.env` | `com.davidliu.ta-p4-expiry-labels.plist` → `EnvironmentVariables.TA_ENV_FILE`，写成**绝对路径** `/Users/davidliu/ta-secrets/ta.env`（launchd 不展开 `~`/`$VAR`，写成 `~` 会被逐字使用；非绝对路径由脚本 exit 16 拒绝）                |
| 3   | 副本 600、目录 700                           | `ta_secrets_install.sh`：`umask 077` + `mkdir -p` 后 `chmod 700` + 临时文件 `chmod 600` + `rename(2)` 原子落位；`--check` 为**严格门禁**（非 600/700 直接非零退出）。运行时 wrapper 只 **WARN**（见 §6 权衡）                         |
| 4   | `grep` rc ≥ 2 → 单独告警 fail-closed         | wrapper 改为先取值 `GREP_RC` 再 `case`：`0`→exit 16（非法行），`1`→继续，`≥2`→**exit 18**「凭据文件读取被拒（疑似系统授权）」，不 `source`、不放行                                                                                    |
| 4b  | （DAV-2286 复审补充）                        | KEY=VALUE 闸的**唯一定义**提到 wrapper 的 `CRED_KEY_VALUE_GATE='…'`，安装脚本运行时从 wrapper 提取同一行（`eval` 同一份 shell 字面量），两侧逐字节一致（有对应用例）；安装件因此能拒绝「定时器会拒收」的副本，而不是 20:10 才 exit 16 |
| 5   | `RESTART.md` 登记「更换密钥时两处都要改」    | 见 §4 的现成段落。`RESTART.md` 是**宿主机侧未入库文件**（`/Users/davidliu/Documents/TradingAgents-AShare-releases/RESTART.md`，见 `LAUNCHD_SWITCH.md` §「RESTART.md 基线说明」），本卡只推分支，故**未改动**，由发布卡粘贴            |
| 6   | 排查 `reports` / `logsize-watch`             | 见 §5                                                                                                                                                                                                                                 |

顺带（为使第 1 项真正生效，非卡面字面要求）：

- **解释器**：`PYTHON` 模板默认从 `~/Documents/TradingAgents-AShare/.venv310/bin/python`
  改为独立 venv `~/ta-venv/py310/bin/python`（可用 `TA_PYTHON` 覆盖）。
  理由两条：① 总控 10-17 返修令已明令迁离 `.venv310`（DAV-2087 认定的最大单点）；
  ② 该路径同样位于 `~/Documents` 之下，凭据路径修好之后，下一处 EPERM 就会落在
  `exec` 解释器上（exec 是否受 TCC 拦截本卡**未取得实测证据**，标注为待验证；
  迁出后此风险不再存在）。已安装作业的本地工作树 10-17 已手工改成 ta-venv，
  模板此前与之漂移。
- **`sys.path` 去硬编码**：交易日闸的 heredoc 原先把 `/Users/davidliu/ta-p4-ef8aa9d9`
  写死在 `'PY'`（引号化 heredoc 无法展开变量）里，与 `CODE_DIR` 形成第二份必须
  手工同步的路径（10-19 安装时就手工改过两处）。现改为把 `CODE_DIR` 作为
  `argv[1]` 传入，只声明一次。

## 3. 安装步骤（发布卡执行）

```bash
# 0. 准备检出：wrapper 与管道脚本必须在**同一个**检出里（DAV-2303）
LABELS_DIR=/Users/davidliu/ta-p4-labels-<sha>
git clone <repo> "${LABELS_DIR}"
git -C "${LABELS_DIR}" checkout <sha>
# 硬门禁：管道脚本不在这里 → 装上去必然 exit 11（15:56 就是这么失败的）
test -f "${LABELS_DIR}/scripts/phase2/expiry_label_pipeline.py" || exit 1
# 工作树必须干净（发布卡纪律：不得在检出里直接改代码）
git -C "${LABELS_DIR}" status --porcelain | wc -l     # 期望 0

# 1. 生成/刷新凭据副本：目录 700、文件 600、原子落位、不打印任何值
scripts/phase2/ta_secrets_install.sh
scripts/phase2/ta_secrets_install.sh --check     # 严格门禁，非 0 即停

# 2. 装 plist：把**两个**占位符都替换掉（三处 {{LABELS_DIR}} 指向同一检出；
#    三处 {{WORK_ROOT}} 指向同一数据面）
WORK_ROOT=/Users/davidliu/ta-p4-2e1e199f/work
sed -e "s|{{LABELS_DIR}}|${LABELS_DIR}|g" \
    -e "s|{{WORK_ROOT}}|${WORK_ROOT}|g" \
    scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist \
    > ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
# 只查「值」是否还残留**占位符本身**（`{{`）——不再查某个具体名字，
# 以后再加占位符也不用改这段检查。
# 只查「值」：注释里提到占位符不算（raw 只输出数组首元素，
# 必须用 json 才能拿到全部元素）。
# 注意：`grep -c` 在计数为 0（= 检查通过）时返回 1 —— 直接拿它进 `set -e`/`&&`
# 链会把「通过」判成「失败」（DAV-2292 🟢-3）。用 `grep -q` 并把判断反过来：
! plutil -extract ProgramArguments json -o - \
   ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist | grep -q '{{' \
   || { echo "unresolved placeholder in ProgramArguments"; exit 1; }
! plutil -extract EnvironmentVariables json -o - \
   ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist | grep -q '{{' \
   || { echo "unresolved placeholder in EnvironmentVariables"; exit 1; }
! plutil -extract StandardOutPath raw -o - \
   ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist | grep -q '{{' \
   || { echo "unresolved placeholder in StandardOutPath"; exit 1; }
! plutil -extract StandardErrorPath raw -o - \
   ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist | grep -q '{{' \
   || { echo "unresolved placeholder in StandardErrorPath"; exit 1; }
plutil -lint ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist

# 3. 一次手工触发（非交易日会 exit 0 + 一行 skip 日志，也算通过）
launchctl kickstart -k gui/$UID/com.davidliu.ta-p4-expiry-labels
```

> 装完再核一次：三处路径（`ProgramArguments` / `WorkingDirectory` / `TA_CODE_DIR`）
> 必须解析到**同一个**检出：
> ```bash
> /usr/libexec/PlistBuddy -c "Print :ProgramArguments" ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
> /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:TA_CODE_DIR" ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
> ```

## 4. RESTART.md 待粘贴段落（卡面第 5 项）

把下面整段追加到宿主机 `/Users/davidliu/Documents/TradingAgents-AShare-releases/RESTART.md`
（建议追加在文末「回退方法」之前；该文件不入 git，无法在本分支 diff 校验）：

`````markdown
## 凭据两处（DAV-2284，2026-10-2x）

定时任务 `com.davidliu.ta-p4-expiry-labels` 的凭据来自 `~/ta-secrets/ta.env`
（目录 700 / 文件 600，plist 的 `TA_ENV_FILE` 指向它），**不是**主检出的 `.env` ——
macOS TCC 拒绝 launchd 唤起的 `/bin/bash` 读取 `~/Documents` 下任何文件内容。

**更换密钥时，两处都要改**：

1. `~/Documents/TradingAgents-AShare/.env`（主检出，服务/其他任务在用）
2. `~/ta-secrets/ta.env`（标签定时任务在用）

改完第 2 处后必须：

```bash
scripts/phase2/ta_secrets_install.sh --check   # 期望末行 OK
launchctl kickstart -k gui/$UID/com.davidliu.ta-p4-expiry-labels
```
`````

只改一处会出现「服务正常但标签任务 nightly exit 13/18」或「标签任务用旧 key」，
两种都不会自动报警到人。


（发布后 `ta_secrets_install.sh` 的末行也会打印同一句提醒。）

## 5. 卡面第 6 项：其余 `/bin/bash` 定时任务排查

本机 `~/Library/LaunchAgents` 里以 `/bin/bash` 为 `ProgramArguments[0]` 的作业共 4 个：

| 作业 | 运行时是否读 `~/Documents`（`open()` 内容） | 结论 |
|---|---|---|
| `com.davidliu.ta-p4-expiry-labels` | **是**（`…/TradingAgents-AShare/.env`） | 本卡修复对象 |
| `com.davidliu.ta-logsize-watch` | **否** —— 只用 `stat -f '%z'`/`%m'` 按**显式路径**取元数据；`du -sk` 只跑非 `~/Documents` 目录（`~/ta-work/reports`），`~/Documents` 下的 daily-batch 已改用 `sum_stat` 逐文件 stat 求和 | 无需改动。脚本内已写明「Required for ~/Documents targets: a launchd agent is denied recursive enumeration there (TCC: du returns Operation not permitted), while stat() on a known path succeeds」，2026-10-18 起生效；10-19 15:50 采样 **26 行，其中 `-1` 为 0 行**（全部为真实 size），`-1` 仅出现在 10-18 修好之前的 `du` 行 |
| `com.davidliu.ta-daily-reports` | **否（按现装 plist）** —— `TA_PYTHON_BIN=/Users/davidliu/ta-venv/py310/bin/python`、`TA_PROJECT_ROOT=/Users/davidliu/ta-reports-cb82987b`、`TA_SNAPSHOT_DB=/Users/davidliu/ta-snapshot/current.db` 全部在 `~/Documents` 之外；`MAIN_CHECKOUT` 仅用于字符串比对（拒收主检出），不读内容；模板里 `CALENDAR_JSON` 由 `PYTHON_BIN` 推导，随 venv 一起在 `Documents` 之外 | 现装配置无需改动。**但模板本身会复现 TCC 撞墙**（DAV-2286 🟡-2 更正，原文写成「漏配」是错的）：① `releases/launchd/com.davidliu.ta-daily-reports.plist:60-61` **显式**写入 `TA_PYTHON_BIN = /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`；② `releases/launchd/ta-daily-reports-wrapper.sh:33` 的缺省同样是它。即**按模板原样重装就会复现**，不是「某次安装忘了配」；且只改 wrapper 缺省修不好真正生效的那一处（plist 的 `TA_PYTHON_BIN` 优先），`CALENDAR_JSON`（`ta-daily-reports-wrapper.sh:47`）又由它推导。**修复面 = 模板 plist 的 `TA_PYTHON_BIN` + wrapper 缺省两处**（各 1 行，均在 `releases/launchd/`）。**承接**：需总控签白名单放宽后由本卡一并改，或另开跟踪卡；**未落地前禁止从模板重装日报**（现装作业用的是 `~/ta-venv`，本身没问题） |
| `com.tradingagents.prod-health-sentinel` | **否** —— 脚本内无 `~/Documents` 引用（`grep -c Documents` = 0） | 无需改动 |

其余定时作业（`com.davidliu.ta-p4-ledger`、`com.davidliu.ta-daily-batch`、
`com.tradingagents.ta-snapshot` 等）`ProgramArguments[0]` 直接 exec venv python，
不经 `/bin/bash`，不在本卡范围；`ta-p4-ledger` 的 `--db` 位于 `~/Documents`，但它由
ad-hoc 签名的 venv python 直启（DAV-2180 实测该组合可读 `~/Documents`），且 10-19 19:45
正常产出（`forward_ledger.jsonl` 14 MB，mtime 当日），无告警。

## 6. 权衡记录（复审可挑战点）

1. **运行时为什么只 WARN 不 fail 权限**：`cp` 默认 umask 产出 644、目录 755；
   若把「非 600/700」做成 20:10 的硬失败，安装时一次漏 `chmod` 就让标签任务整夜不跑，
   而权限问题并不会因此变轻。故严格门禁放在安装/复核侧（`ta_secrets_install.sh --check`，
   非零退出），运行时只记 `WARN credential file mode 644 (want 600)`（只打模式，不打内容）。
2. **exit 18 而不是复用 13/16**：13 是「token 缺失」（数据面），16 是「文件存在但内容
   非法/不可用」（内容面），18 是「OS 拒绝读」（授权面）。三者排障动作完全不同
   （换 key / 修 .env 语法 / 改路径或授权），混码会让下一次告警继续误导。
3. **`TA_ENV_FILE` 非绝对路径直接 exit 16**：launchd 不展开 `~`，卡面写法
   `TA_ENV_FILE=~/ta-secrets/ta.env` 若原样进 plist 会被当成字面量，静默指向
   `WorkingDirectory/~/ta-secrets/ta.env`。宁可安装当晚告警，也不要静默跑错文件。
4. **（DAV-2286 返修）安装脚本的 KEY=VALUE 闸从 wrapper 提取，而不是各自写一份**：
   两份正则只要有一处漂移，就会出现「装成功、20:10 exit 16」或「装不上」的不一致。
   提取方式是把 wrapper 里的 `CRED_KEY_VALUE_GATE='…'` 那一行原样交给 `eval`
   （shell 自己完成单引号还原），安装脚本与 wrapper 因此拿到**逐字节相同**的
   143 字符正则；提取不到（wrapper 不在 / 读不到 / 格式被改）→ **rc 7 拒绝安装**，
   绝不「少查一点照装」。

## 7. 回退

```bash
launchctl bootout gui/$UID/com.davidliu.ta-p4-expiry-labels
# 旧行为 = 仍读 ~/Documents/…/.env（在 launchd 下必然 exit 18），
# 若需临时恢复可用终端手工跑：
TA_ENV_FILE=/Users/davidliu/Documents/TradingAgents-AShare/.env \
  bash scripts/phase2/run_expiry_label_daily.sh
````

`~/ta-secrets` 可整体删除；删除后作业会以 exit 16「credential file missing or not
readable」告警（不会静默），符合 fail-closed。

## 8. 已知残留

1. 宿主机 `RESTART.md` 未改（§4 待粘贴），`~/ta-secrets/ta.env` 未创建 —— 均由发布卡执行。
2. 已安装作业当前指向 `/Users/davidliu/ta-p4-labels-cb82987b`（安装时手工改写），
   与模板里的 `ta-p4-ef8aa9d9` 不一致；该工作树还有 10-17 的未提交本地改动
   （`PYTHON`/`CODE_DIR`/heredoc）。本卡合入后安装时以模板 + 发布卡口径为准，
   勿直接沿用该工作树。
3. `exec` 一个位于 `~/Documents` 的二进制在 launchd 下是否被 TCC 拦截：**未实测**
   （本卡不安装，无法在不落地 launchd 作业的前提下取得该证据）。迁到 `~/ta-venv/py310`
   后此路径不再涉及。
4. **（DAV-2286 🟡-2 承接）日报模板的 `TA_PYTHON_BIN` 仍指向 `~/Documents`**：
   本卡**未改**（白名单未放宽，且属另一作业），需总控签字后由本卡一并改，或另开
   跟踪卡；在那之前**禁止从模板重装日报**。

## 9. DAV-2286 复审返修记录（2026-10-20）

复审结论：代码主体（`ENV_FILE` 间接化、`grep rc≥2 → exit 18` fail-closed、700/600
安装件、plist 绝对路径）**无 🔴、实现正确**；打回原因是 2 个 🟡。本轮改动：

| 编号 | 处理 | 落点 |
|---|---|---|
| 🟡-1 | `--src` / `--dest` 缺值时 `shift 2` 失败且不移动位置参数 → 参数循环永不前进（实测 >4s 仍在转，~70% CPU、零输出、只能 kill）。**本脚本是发布卡安装第 1 步**，运维漏一个值就表现为「命令卡住不说话」 | 改为先判 `$#` 再取 `$2`（两个独立 `if`；**任何会展开 `$2` 的命令前面必须先有独立的计数检查** —— 这是 DAV-2292 🟢-1 更正后的真规则），缺值 → **rc 2** 立即报错（实测 0.004s）；同时把 `say`/`fail`/`mode_file` **上移到参数循环之前**（原先 `fail` 在循环后才定义，循环里调用会 `command not found` 然后继续往下跑） |
| 🟡-2 | 卡面第 6 项「日报模板」排查结论**成因写错**：原文说「若将来某次安装漏配 `TA_PYTHON_BIN`」，但**模板 plist 自己就显式写入了 Documents 路径**（`releases/launchd/com.davidliu.ta-daily-reports.plist:60-61`），wrapper 缺省同样是它（`ta-daily-reports-wrapper.sh:33`）——**按模板原样重装就会复现，不是漏配**；且只改 wrapper 缺省修不好真正生效的那一处（plist 优先），`CALENDAR_JSON`（`:47`）又由它推导 | §5 表格该行改写为：修复面 = 模板 plist 的 `TA_PYTHON_BIN` + wrapper 缺省**两处**，按模板重装即复现；承接 = 需总控签白名单放宽后由本卡一并改，或另开跟踪卡；**未落地前禁止从模板重装日报**（现装作业用 `~/ta-venv`，本身无问题）。本卡未改该模板（白名单未放宽 + 属另一作业），已在 §8 列为承接项 |
| 🟢-1 | 退出码表补齐新增的两种 16 | wrapper 头部：`16a` 路径非绝对 / `16b` 文件缺失或不可读 / `16c` 非法行 / `16d` source 失败 |
| 🟢-2 | 注释称 source 守卫能接住「unbound var under `set -u`」——实测 bash 在 source 内遇未定义变量会**直接终止整个脚本**（rc 127、无 alert 文件），守卫只接得住 `.` 返回非 0 | source 两侧改为 `set +u` … `set -u` 窗口（失败变成正常 exit 16 告警，而不是静默 rc=127）；注释改为与实测一致，并说明「KEY=VALUE 白名单已先拦下裸 `$VAR`，`set +u` 是第二层保险」 |
| 🟢-3 | 同一文件内两处注释仍写「sources ~/Documents/…/.env」，与新口径矛盾 | wrapper 头部与凭据段注释改为「TA_ENV_FILE 指向的副本；旧 .env 仅作终端手跑缺省」 |
| 🟢-4 | 安装件未复用 wrapper 的 KEY=VALUE 严格闸（副本可装成功、到 20:10 才 exit 16） | 闸的**唯一定义**提到 wrapper 的 `CRED_KEY_VALUE_GATE='…'`；安装脚本运行时从 wrapper 提取同一行（`eval` 同一份 shell 字面量）→ 两侧逐字节一致（143 字符，有对应用例）；安装时对源与副本都过闸；提取不到 → **rc 7 拒绝安装**，不「少查一点照装」 |
| 🟢-5 | 「10-19 15:50 采样 24 行」数字偏差 | 改为实测 **26 行，其中 `-1` 为 0 行** |
| 🟢-6 | 覆盖缺口：目录模式漂移、`--dest` 符号链接拒绝、`SOURCE_RC` 分支、「rc≥2 时确实没有 source」只间接断言 | 新增用例：目录 755 → `--check` rc=6；目标目录是 symlink → rc=4；rc≥2 用例改用会执行命令的 canary 行并断言 marker 不存在；unbound 变量两形态（裸 `$VAR` 被闸拦 / 单引号字面量走到 token 探针）都断言「有 alert、不是静默 rc=127」；安装器与 wrapper 的闸逐字节一致 |
| 🟢-7 | plist 注释里写裸 `check`、别处写 `--check`，不一致 | 保留 `check`（**XML 注释里出现双连字符是非法的**，会让 plistlib/libxml2 直接解析失败；`plutil` 容忍而已），并在 plist 注释里写明原因；安装脚本两种拼法都接受 |

### 本轮新增/变更的退出码

安装脚本：`2` 参数缺值（新增，原为死循环）、`7` 闸读取失败（新增）、`3` 源过不了闸（新增）。
wrapper：退出码表补齐 16 的四个子情形（16a/16b/16c/16d），行为不变。


## 10. DAV-2292 事故记录与新规矩（2026-10-20 15:56）

### 10.1 事故：谁、做了什么、影响面

**执行人是本卡的开发（资深开发1）**。返修过程中，为了对比 wrapper 与安装脚本的
KEY=VALUE 正则是否一致，执行了：

```
bash -c 'source scripts/phase2/run_expiry_label_daily.sh 2>/dev/null; echo "$CRED_KEY_VALUE_GATE"'
```

`source` **不是**「读一个文件」——它会**执行**整个脚本。于是该脚本按自己的固定常量
跑了下去：`OUT_DIR` 指向**正式**标签目录、默认 `ENV_FILE` 指向**真实凭据**。

生产目录留下的痕迹（已逐项核对）：

```
label_pipeline.log  追加三行（第 41-43 行，全部是 07:56:00）：
  2026-10-20T07:56:00+00:00 timer start label=com.davidliu.ta-p4-expiry-labels
  2026-10-20T07:56:00+00:00 WARN credential dir mode 755 (want 700) — chmod 700 '/Users/davidliu/Documents/TradingAgents-AShare'
  2026-10-20T07:56:00+00:00 ALERT pipeline script missing: …/ta-p4-ef8aa9d9/…/expiry_label_pipeline.py (exit 11)
ALERT.launchd      被覆盖（exit 11 的内容）—— 已由总工改名留证为
                   ALERT.launchd.stray-20261020T1556（380 字节，内容完整）
```

**影响面（已逐项核对）**：

| 项 | 结论 |
|---|---|
| 标签数据 | **未写**。`labels_t10.jsonl` mtime 仍为 10-20 14:25:01、`labels_t40.jsonl` 仍为 14:29:42（945 / 307 行未变）；exit 11 在 `[ -f PIPELINE ]` 早退，尚未到 run 阶段 |
| `label_run_state.json` | 未变（14:29:42） |
| 真实凭据 | **被读取**（`source` 执行了 `.env` 的赋值，进程内注入，未落盘）。已核：凭据值**未出现在**任何产物（alert / log / plist / 文档 / diff）中 |
| 告警与日志 | **被污染**：生产 `ALERT.launchd` 被覆盖（15:56 那次的 exit 11 内容，原文已由总工留证为 `ALERT.launchd.stray-20261020T1556`）、`label_pipeline.log` 追加 3 行（log 第 41-43 行） |

**复现验证**（隔离沙箱内，`OUT_DIR`/`ENV_FILE` 指向 `/tmp`）：`source` 那条命令
产出的日志三行与生产 15:56 的三行**逐字相同**，退出码同为 11 —— 归因确认，不是推测。

### 10.2 根因

脚本本身**没有「可被 source 吗」的判据**：它既是一个可执行脚本，又被当成常量来源
去 `source`。任何人（包括开发自己）想取一个常量，就可能触发一次指向生产的误执行。
**这是脚本的缺陷，不是单纯的「谁手滑了」。**

### 10.3 修复：脚本拒绝被 source（已在本分支）

两个脚本（wrapper 与安装脚本）开头都加了护栏：检测到 `BASH_SOURCE[0] != $0`
（即被 `source`/`.` 调用）就 **exit 64** 并打印 REFUSED 提示，**不写任何目录、
不读任何凭据**：

```
$ bash -c 'source scripts/phase2/run_expiry_label_daily.sh; echo "$CRED_KEY_VALUE_GATE"'
REFUSED: …/run_expiry_label_daily.sh is an executable script, not a sourceable library.
REFUSED: sourcing it would write to the PRODUCTION label dir and read the REAL credentials.
HINT: read a constant with: grep "^NAME=" …   (do not source the script)
$ echo $?
64
```

要取脚本里的常量，正确姿势是 `grep "^NAME=" 文件` 或
`eval "$(grep '^NAME=' 文件)"`（本卡安装脚本自己就是这么取 wrapper 的闸的）。

对应用例：`test_wrapper_refuses_to_be_sourced`（rc=64 + REFUSED 文案 + 闸常量
没被打印）、`test_wrapper_refuses_to_be_sourced_writes_nothing_to_the_output_dir`
（输出目录零文件）、`test_installer_also_refuses_to_be_sourced`。

### 10.4 新规矩（总控 10-20 16:05 令第 2 节第 3 条）

#### 手工执行硬规矩（DAV-2292 第四轮补入）

手工跑 `run_expiry_label_daily.sh` **必须同时**给：

```bash
TA_WORK_ROOT=<临时目录>      # 不给 → 写生产标签目录 ~/ta-p4-2e1e199f/work/phase2-labels
TA_ENV_FILE=<临时凭据文件>   # 不给 → 读真实凭据 ~/Documents/TradingAgents-AShare/.env
```

**只给 `TA_ENV_FILE` 不给 `TA_WORK_ROOT` 仍会写生产标签目录**
（2026-10-20 17:58:53 事故正是这个形态：只覆写了 `TA_ENV_FILE`，
`TA_WORK_ROOT` 仍取缺省 → 覆盖了生产 `ALERT.launchd`、追加了 2 行日志）。
脚本在 `TA_WORK_ROOT` 缺失时会打一条 `WARN manual run with the PRODUCTION
work root …`（第四轮起判据只用 `TA_WORK_ROOT` 是否显式给出，
**不用** `XPC_SERVICE_NAME` —— agent 运行时也导出那个变量，
用它判断会把「agent shell 里的手工执行」误判成 launchd）。

复审（2292）核对项：**从模板重装后，plist 里的 `TA_WORK_ROOT` 必须存在**
（第四轮已加进模板）—— 否则 launchd 正常路径每夜都会打这条假 WARN。

> 开发与复审在测试标签脚本时，必须指定「临时输出目录」和「临时凭据路径」；
> 不得写入正式标签目录 `~/ta-p4-2e1e199f/work/phase2-labels/`；
> 不得读取真实凭据。

**本卡返修测试的合规自查（逐条）**：

| 用例 | 临时输出目录 | 临时凭据路径 | 碰正式目录/真实凭据？ |
|---|---|---|---|
| `test_ta_env_file_override_wins_over_the_documents_default` | `tmp_path/wrapper_sandbox/work` | `tmp_path/outside/ta.env` | 否 |
| `test_relative_ta_env_file_is_rejected_before_any_read` | 同上 | `tmp_path/ta.env` | 否 |
| `test_read_denied_grep_rc2_alerts_exit_18_not_exit_13` | 同上 | sandbox 内 `ta.env` | 否 |
| `test_read_denied_never_sources_the_file` | 同上 | sandbox 内 canary 行 | 否 |
| `test_grep_rc2_short_circuits_before_the_ledger_or_pipeline` | 同上 | sandbox 内 | 否 |
| `test_malformed_env_is_still_exit_16_after_the_rc_rewrite` | 同上 | sandbox 内 | 否 |
| `test_unbound_variable_in_the_credential_file_never_aborts_silently` | 同上 | sandbox 内 | 否 |
| `test_wrapper_warns_but_does_not_stop_on_a_loose_mode` | 同上 | sandbox 内 | 否 |
| `test_installer_*`（8 个） | `tmp_path/ta-secrets` | `tmp_path/src.env`（假值） | 否 |
| `test_wrapper_refuses_to_be_sourced` | `tmp_path/sandbox/work` | `tmp_path/sandbox/ta.env` | 否 |
| `test_wrapper_refuses_to_be_sourced_writes_nothing_to_the_output_dir` | 同上 | 同上 | 否 |
| `test_installer_also_refuses_to_be_sourced` | `tmp_path/sandbox/ta-secrets` | `tmp_path/sandbox/ta.env` | 否 |
| `test_wrapper_does_not_leak_its_own_variables_into_the_subprocess_env` | `tmp_path/work` | `tmp_path/ta.env` | 否 |
| `test_plist_*` / `test_installer_gate_is_byte_identical_*` / `test_no_hard_coded_checkout_*` / `test_plist_carries_the_checkout_*` / `test_pipeline_is_found_*` / `test_ta_code_dir_*` / `test_missing_pipeline_*` | 无写入 | 无 | 否 |

实现机制：wrapper 的沙箱化是**改写被拷贝副本里的固定常量**（`PYTHON=`、
`ENV_FILE=`、`CODE_DIR=`、`WORK_ROOT=` 四个），`OUT_DIR` 由 `WORK_ROOT` 推导，
因此**测试永远落在 `tmp_path` 下**；测试文件里**不出现** `~/ta-p4-2e1e199f`
与 `~/Documents/TradingAgents-AShare`（仅一处断言 plist 里的 `TA_ENV_FILE` 字面量，
不读文件）。

**DAV-2292 🟡-2 的加固**：三条护栏用例（`test_wrapper_refuses_to_be_sourced`、
`…_writes_nothing_to_the_output_dir`、`test_installer_also_refuses_to_be_sourced`）
原先**直接 source 仓库里的真文件**且不覆写任何常量 —— 一旦护栏被删/被绕过，
它们就会**重放 15:56 事故**（wrapper 那条会写正式标签目录并读真实 `.env`；
installer 那条会把真实凭据复制成 `~/ta-secrets/ta.env` 并 `mkdir 700`，比 15:56
更重）。现改为：先 `read_text()` → 把 `PYTHON`/`ENV_FILE`/`CODE_DIR`/`WORK_ROOT`
（安装脚本另加 `SRC`/`DEST`）**全部覆写到 `tmp_path`** → 写 `tmp_path` 副本 →
source **那个副本**。

**变异测试已做**：把 wrapper 的护栏整段删掉再跑这三条用例 → 2 条**失败**
（`test_wrapper_refuses_to_be_sourced`、`…_writes_nothing_to_the_output_dir`），
证明用例不是空转的；同时因为已沙箱化，变异期间**没有碰生产目录与真实凭据**。

**既有 `tests/test_dav1875_expiry_label_pipeline.py` 的 wrapper 用例**同构
（`_wrapper_harness` 改写同样四个常量），也全部落在 `tmp_path` 下。

### 10.5 残留

- `ALERT.launchd` **现状（截至 2026-10-20 20:10:05）**：**378 字节、exit 13**，
  内容是**现装作业的定时运行**留下的（`reason=TUSHARE_TOKEN missing after
  sourcing /Users/davidliu/Documents/TradingAgents-AShare/.env`）—— 20:10 的
  launchd 定时运行又失败了一次（**DAV-2284 要修的 TCC 问题此刻仍在生产上每夜
  复现**；该检出跑的是 10-17 那份 wrapper，没有 `TA_ENV_FILE` 支持）。
  此前它先后被 15:56（exit 11）与 17:58:53（复审误执，exit 16）覆盖过。
  **唯一取证锚点**是 `ALERT.launchd.stray-20261020T1556`（380 字节，exit 11，
  15:56 的原文）—— **留证件未动，全程只读未删未改**。
  若发布卡要求目录干净，可删 `ALERT.launchd`（**不删留证件**）——本卡未动生产目录。
- `label_pipeline.log` 现为 **47 行**（2026-10-20 20:10:05）：15:56 的三行在
  第 41-43 行，17:58:53 的两行与 20:10:05 的两行在末尾。均未删（属取证材料）。
- 15:56 误执行**读到了真实凭据**（仅在进程内，未落盘、未外泄）；本卡未轮换密钥，
  是否轮换由总控定。

## 11. DAV-2303 路径写死 → 环境变量 / 脚本目录推导（总控 10-20 17:20 令）

### 11.1 问题（总控实查，已复核）

候选是从主干版本改出来的，因此继承了主干的**写死路径**：

| 写死项 | 旧值 | 后果 |
|---|---|---|
| `CODE_DIR` | `/Users/davidliu/ta-p4-ef8aa9d9` | 该检出停在 10-14 的 `f41b9cf5`，**里面没有 `expiry_label_pipeline.py`** → 照候选装上去，10-22 20:10 的标签任务**以 exit 11 失败**（15:56 那次手工执行正是这么 exit 11 的） |
| `PYTHON` | `~/Documents/TradingAgents-AShare/.venv310/bin/python` | 位于 `~/Documents` 之下（TCC 墙后），且是 DAV-2087 认定的最大单点（总控 10-17 令已要求迁离） |
| 交易日闸的 `sys.path` | 同一个旧检出 | 同上 |

**已复核属实**：`git -C ~/ta-p4-ef8aa9d9 log --oneline -1` = `f41b9cf5`；
`ls ~/ta-p4-ef8aa9d9/scripts/phase2/expiry_label_pipeline.py` → **No such file**。

### 11.2 改法：env 优先 + 脚本自身目录兜底

```
SCRIPT_DIR     = 脚本自身所在目录（BASH_SOURCE 的绝对路径，不用 $PWD）
SCRIPT_CHECKOUT = ${SCRIPT_DIR}/../..          （脚本在 <检出>/scripts/phase2/ 下）
PYTHON      = ${TA_PYTHON:-/Users/davidliu/ta-venv/py310/bin/python}
ENV_FILE    = ${TA_ENV_FILE:-…}                 （DAV-2284，不变）
CODE_DIR    = ${TA_CODE_DIR:-${SCRIPT_CHECKOUT}}
WORK_ROOT   = ${TA_WORK_ROOT:-/Users/davidliu/ta-p4-2e1e199f/work}
```

- **env 优先**（plist 显式给出 → 决定性的），**脚本自身目录兜底**（装到哪个检出
  就在哪个检出里找管道脚本）。两条都不写死。
- 交易日闸的 `sys.path` 早就取 `argv[1]`（DAV-2286 已改），而 `argv[1]` 就是
  `${CODE_DIR}` —— 因此**自动跟随**，无第二处写死。
- `WORK_ROOT` 也纳入 env（数据面与检出无关，但同样不该写死在代码里）。

### 11.3 plist 模板（`{{LABELS_DIR}}` 占位符，沿用仓库既有约定）

`scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist`：

| 位置 | 值 |
|---|---|
| `ProgramArguments[1]` | `{{LABELS_DIR}}/scripts/phase2/run_expiry_label_daily.sh` |
| `WorkingDirectory` | `{{LABELS_DIR}}` |
| `EnvironmentVariables.TA_CODE_DIR` | `{{LABELS_DIR}}` |
| `EnvironmentVariables.TA_PYTHON` | `/Users/davidliu/ta-venv/py310/bin/python` |
| `EnvironmentVariables.TA_ENV_FILE` | `/Users/davidliu/ta-secrets/ta.env` |
| `EnvironmentVariables.TA_WORK_ROOT` | `{{WORK_ROOT}}` |
| `StandardOutPath` | `{{WORK_ROOT}}/phase2-labels/launchd.out.log` |
| `StandardErrorPath` | `{{WORK_ROOT}}/phase2-labels/launchd.err.log` |

**三处 `{{LABELS_DIR}}` 指向同一个检出** —— 这正是 15:56 违反的那条不变量；
**三处 `{{WORK_ROOT}}` 指向同一个数据面**（DAV-2292 第六轮补登记：原先表里漏了
`TA_WORK_ROOT`，而安装段的 `sed` 也只替换了 `{{LABELS_DIR}}` —— 照那版装出来的
plist 残留 5 处 `{{WORK_ROOT}}`，其中 3 处是活值）。

安装时 `sed` **两条都要替换**（写法与 `releases/launchd/com.davidliu.ta-daily-reports.plist`
一致）：

```bash
sed -e "s|{{LABELS_DIR}}|${LABELS_DIR}|g" \
    -e "s|{{WORK_ROOT}}|${WORK_ROOT}|g" \
    scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist \
    > ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
```

替换后的检查**查残留占位符本身 `{{`**（不是查某个具体名字 —— 以后再加占位符
也不用改检查段）：见 §3 的四条 `grep -q '{{'`。

### 11.4 回归用例（新增 6 个）

| 用例 | 断言 |
|---|---|
| `test_pipeline_is_found_in_the_scripts_own_checkout` | 无 `TA_CODE_DIR` 时，管道脚本在**脚本自身所在检出**里被找到（rc 0，不是 exit 11） |
| `test_ta_code_dir_from_the_environment_wins_over_the_script_location` | `TA_CODE_DIR` 指向另一个检出时，**env 优先**，管道脚本在那里被找到 |
| `test_missing_pipeline_still_exits_11_naming_the_resolved_checkout` | 真的缺管道时仍 exit 11，且告警里写的是**实际解析到的检出**（运维能一眼看出是哪个检出不对） |
| `test_no_hard_coded_checkout_survives_in_the_shipped_script_and_plist` | 脚本与 plist 的**活路径**里不得再出现旧检出字面量（注释里叙述历史不算） |
| `test_plist_carries_the_checkout_as_a_placeholder_and_env_var` | plist 用 `{{LABELS_DIR}}` 占位符；替换后 `ProgramArguments` / `WorkingDirectory` / `TA_CODE_DIR` **三者解析到同一目录**；所有值里不得残留未替换的 `{{` |
| `test_wrapper_does_not_leak_its_own_variables_into_the_subprocess_env` | DAV-2292 🟡-1：`set +a` 补回后，桩解释器落盘的子进程 env 里**不得出现** `TODAY`/`IS_TRADING`/`CHECK_RC`/`RUN_RC`/`GAP_CHECK`/`GAP_LEVEL`/`GAP_RC`/`SOURCE_RC`/`GREP_RC`/`GREP_ERR`；**但 `TUSHARE_TOKEN` 必须仍在**（凭据值要导出） |

### 11.5 顺带修掉的 DAV-2292 复审项

| 编号 | 处理 |
|---|---|
| 🟡-1 | `set +a` 补回（凭据值仍导出，脚本自己的记账变量不再泄漏）。**注**：当时写的「`SOURCE_RC=$?` 的捕获放在 `set +a` 之后」在第三轮被复审指出是错的 —— `set +a` 是返回 0 的内建命令，在它之后取 `$?` 永远读到 0，16d 成了死分支。**正确做法**（第三轮已改）：在 `set +a` **之前**用临时变量 `_src_rc=$?` 捕获，紧随其后 `set +a` → `SOURCE_RC=${_src_rc}` → `unset _src_rc` |
| 🟡-2 | 三条护栏用例改为 source **沙箱副本**（四个常量全部覆写到 `tmp_path`；安装脚本另加 `SRC`/`DEST`），护栏一旦失效也不会重放 15:56；并做了变异测试（删掉护栏 → 2 条用例失败） |
| 🟢-1 | 两个脚本的退出码表补 **64**（refused to be sourced） |
| 🟢-2 | 安装脚本里那条错误的 bash 语义论断（"`a || b` 救不了"）改正为「**会展开 `$2` 的命令前面必须先有一条独立的计数检查**」，并在注释里明确撤回旧说法（`\|\|`/`&&` 确实会短路，旧说法是错的） |
| 🟢-3 | 事故取证文字「追加两行」→ **三行**（log 第 41-43 行，全为 07:56:00） |
| 🟢-4 | `TA_ENV_TCC.md` 的孤立 4 反引号围栏（吞掉 §5/§6/§7 三个标题）已修：外层围栏改 5 反引号以正确嵌套内层 ```bash，孤立行删除；现全文围栏配对检查通过 |
| 🟢-5 | 测试里的 canary 从全局 `/tmp/PWNED_gate` 改为 `tmp_path` 内 |

### 11.6 发布卡纪律（总工 10-20 17:20 令，先记在卡上）

1. **发布卡不得在检出里直接改代码**：需要改路径，**必须先在分支上改好、经复审再部署**。
2. 2284 的发布卡要核对两件事：**新检出的工作树是干净的**；**装好之后旧检出里的本地改动已不再需要**
   （`~/ta-p4-labels-cb82987b` 现有 10-17 的未提交本地改动；`~/ta-p4-ef8aa9d9` 停在
   `f41b9cf5` 且缺管道脚本，两者都**不可**作为新装机的检出）。

## 12. 第三轮返修（DAV-2292 对 `0cba793e` 的 3🟡 + 5🟢）

### 12.1 🟡-1 `.` 的返回码被 `set +a` 吃掉 → 16d 是死分支

**复审指出并已复现**：`set +a` 是返回 **0** 的内建命令，所以「在 `set +a` 之后取 `$?`」
永远读到 0 —— 上一轮补 `set +a` 时把 `SOURCE_RC=$?` 放到了它**后面**，
`failed to source`（16d）因此成了**死代码**：过闸但解析不了的内容被误报成
exit 13「内容缺失」，最坏路径下静默通过。

**但只改捕获位置还不够**：`.` 返回的是**最后一条命令**的状态，
所以「第一行语法错 + 后面一行合法」时 rc 仍是 0（实测）：

```
$ printf 'A=((\nTUSHARE_TOKEN=plainvalue\n' > t.env
$ . t.env; echo $?      →  0      # 语法错被后面成功的行掩盖
$ bash -n t.env; echo $? →  0      # bash -n 也不报错（不适用）
```

唯一可靠信号是**源码的 stderr**（bash 打印语法错后**继续**解析后面的行）。
改法：

```bash
_src_errfile="${TMPDIR:-/tmp}/.cred_src_err.$$"
: >"${_src_errfile}" 2>/dev/null || _src_errfile=""
if [ -n "${_src_errfile}" ]; then
    . "${ENV_FILE}" 2>"${_src_errfile}"
    _src_rc=$?
    _src_err="$(cat "${_src_errfile}" 2>/dev/null)"; rm -f "${_src_errfile}"
else
    . "${ENV_FILE}"; _src_rc=$?; _src_err=""
fi
set +a
SOURCE_RC=${_src_rc}; unset _src_rc
set -u
```

三点说明：
1. **stderr 落临时文件而不是命令替换** —— 命令替换会把 shell 自己的 `set -x`
   trace（也走 stderr）一起吞进来；临时文件只装被 source 文件的 stderr。
2. **告警里只写行号，绝不写 stderr 原文** —— bash 的语法错消息会**引用出错的那一行**，
   对凭据文件来说那一行**可能就是密钥值**。实测：告警与日志里 `plainvalue` 出现 0 次。
3. 临时文件用完即删（实测 `/tmp/.cred_src_err.*` 无残留）。

**回归用例**：`test_source_failure_is_reported_as_16d_not_13_or_silence`、
`test_source_failure_with_a_valid_token_line_is_not_silent`（后者正是复审指出的
最坏路径：`A=((` + 合法 token 行 → 必须 exit 16 且有告警，绝不 exit 0）。

### 12.2 🟡-2 CRLF 行尾 → 值带尾部 0x0d → 永久 gap 行

闸的尾锚 `[[:space:]]*$` 把 `\r` 当尾随空白接受，CRLF 文件**过闸**（实测 rc=1），
而 bash 赋值**不剥离** `\r` → 值尾带 0x0d（实测 `TUSHARE_TOKEN` 长度 7、末字节 0d）。
token 预检只判「非空」，于是带 CR 的无效 token 被放行 → SW 行业腿**逐行**落
`membership_status="gap"`，而标签行是 append-only，**已写入的 gap 无法重标**。

**修法（不动 143 字符闸本体，避免文档数字全部失效）**：wrapper 与安装脚本各在
GREP 闸**之前**加一条 CRLF 前置检查：
- wrapper → **exit 16**，告警点名「CRLF 行尾」；
- 安装脚本（install 走**源**、verify 走**副本**）→ **rc 3**（源）/ **rc 6**（副本）。

**回归用例**：`test_crlf_credential_file_is_rejected_before_the_gate`、
`test_installer_rejects_a_crlf_source`。

### 12.3 🟡-3 安装脚本会把「已存在的任意目录」chmod 700

旧代码无条件 `chmod 700 "${DEST_DIR}"`：`--dest $HOME/ta.env` 会把**用户主目录**
从 755 改成 700（`--dest /private/tmp/x.env` 则把 /private/tmp 改成 700），且仍报 OK。

**修法**：已存在的目录只接受 **700/500**，否则 **rc 4 拒绝且不动它**；
只有**本脚本新建**的目录才 `chmod 700`。另对 `$HOME`、`/`、`/private/tmp`、`/tmp`、
`/var`、`/usr`、`/Applications`、`/Users`、`/Library`、`/System` 直接 rc=4 拒绝。

**回归用例**：`test_installer_refuses_to_chmod_a_preexisting_directory`（断言既有目录
权限**未被改动**）、`test_installer_refuses_the_home_directory_as_destination`。

### 12.4 5 条 🟢

| 编号 | 处理 |
|---|---|
| 🟢-1 | exit 10 的告警文案点名 plist 键（`TA_PYTHON`），便于搬 venv 时定位；解释器缺省仍是 `~/ta-venv/py310`（本机实测存在且依赖齐全，**今天装上去不会出事**） |
| 🟢-2 | plist 里两段**重复且已漂移**的注释（旧的 Install 块、旧的 TA_ENV_FILE 段）已删，只留新版；`plutil -lint` 仍 OK |
| 🟢-3 | 占位符检查的**退出码反了**：`grep -c` 在计数为 0（= 检查通过）时返回 1，直接进 `set -e`/`&&` 链会把「通过」判成「失败」。改为 `! … \| grep -q 'LABELS_DIR' \|\| { echo "unresolved placeholder …"; exit 1; }`（plist 注释与 §3 同步） |
| 🟢-4 | `--help` 不再多打一行孤立的 REMINDER（`sed` 范围是闭区间 → 加 `head -n -1`） |
| 🟢-5 | `TA_WORK_ROOT` **未显式给出**时打 `WARN …`（复审员 17:58:53 手工执行覆盖了生产 `ALERT.launchd` 并追加了 2 行日志 —— 护栏只挡 `source`，挡不住 `bash <脚本>`）。**手工执行必须同时给 `TA_WORK_ROOT`（临时目录）与 `TA_ENV_FILE`（临时文件）** —— 这条规矩在**第四轮**才真正写进 §10.4（此前只是口头承诺「已写进 §10.4」，实际 §10.4 里没有，属自述与实际不符） |

### 12.5 本轮新增用例

`test_source_failure_is_reported_as_16d_not_13_or_silence`、
`test_source_failure_with_a_valid_token_line_is_not_silent`、
`test_crlf_credential_file_is_rejected_before_the_gate`、
`test_installer_rejects_a_crlf_source`、
`test_installer_refuses_to_chmod_a_preexisting_directory`、
`test_installer_refuses_the_home_directory_as_destination`。

## 13. 第四轮返修（DAV-2292 对 `66482fc7` 的 3🟡 + 5🟢）

### 13.1 🟡-1 `--help` 一个字都不打（BSD `head` 不支持 `-n -1`）

上一轮为「不再多打一行孤立 REMINDER」加的 `head -n -1` 是 **GNU 扩展**：
macOS 的 BSD `head` 直接报 `head: illegal line count -- -1` → 管道后段失败、
`sed` 被 SIGPIPE → **usage 块一行都不输出**，而脚本仍 `exit 0`。
即「修复本身变成空检查」——与本卡反复核对的同一型问题。

**修法（纯 POSIX，两步）**：

```bash
sed -n '/^# Usage:/,/^# REMINDER/p' "$0" | sed '/^# REMINDER/d' | sed 's/^# \{0,1\}//'
```

两个坑都踩过并记录在注释里：
- `head -n -1` → BSD 不支持（本次回归的根因）；
- 在地址块里写 `p` → BSD sed 报 `extra characters at the end of p command`，
  **且** `d` 会跳过隐式打印（`sed -n` 下地址块里只有 `d` 会输出 0 行）——
  所以「删除」与「打印」必须是**两次独立的 sed**；
- 再往前一步的坑：先 `sed '/^# REMINDER/d'` 全局删再取范围 → 范围的**结束
  标记被删掉了**，范围永不结束 → **整份文件**被打印（287 行）。

实测：`--help` 输出 **23 行**、stderr 空、rc 0、不含 REMINDER。

**用例**：`test_help_prints_the_usage_block_without_the_reminder_line`
（断言 rc 0 / stderr 空 / **行数 ≥ 10（防空检查）** / 三个 flag 与三个 env 覆盖都在 /
不含 REMINDER）。此前**没有任何用例覆盖 `usage()`**，所以 143 passed 与它无关。

### 13.2 🟡-2 source 的 stderr 以 0644 + 可预测文件名落地

上一轮把 stderr 落到 `${TMPDIR:-/tmp}/.cred_src_err.$$`：
wrapper 不设 `umask`（shell 默认 0022）→ 文件 **0644**；名字**可预测**，
同机进程可预建同名符号链接让 `: >` 截断目标；`TMPDIR` 未设时兜底 `/tmp`（1777）；
写入与 `rm` 之间若被 SIGKILL 打断则**永久残留**。
（告警层确实只写行号，但**中间层把含原文的副本写进了盘**，保护等级比原文件低一档。）

**修法**：

```bash
_src_errfile="$(umask 077; mktemp "${TMPDIR:-/tmp}/.cred_src_err.XXXXXX" 2>/dev/null)" || _src_errfile=""
if [ -z "${_src_errfile}" ]; then
    alert "cannot create a private temp file for ${ENV_FILE}'s stderr — refusing to source it blindly" 16
fi
. "${ENV_FILE}" 2>"${_src_errfile}"
```

- `mktemp` → **0600**（实测）+ 名字不可猜；
- `umask 077` 写在**子 shell**里（`$(umask 077; mktemp …)`），不改变本脚本的 umask；
- **拿不到私有临时文件 → fail-closed（exit 16）**，不再退回「只看 `$?`」——
  那条退路会**重放本机制要防的 bug**（过闸但解析不了的文件被静默接受）。

### 13.3 🟡-3 🟢-5 的两处落地偏差

1. **生产日志每夜误报**：plist 原本**没有** `TA_WORK_ROOT` → launchd 正常路径也满足
   「未显式给出」→ 每夜都写一条「outside launchd」WARN，而文案里的断言在 launchd
   语境下恰好是**正常预期行为**。修法：**plist 显式给出 `TA_WORK_ROOT`**
   （`/Users/davidliu/ta-p4-2e1e199f/work`），WARN 就只在真正的手工执行里出现。
2. **文档自述与实际不符**：§12.4 声称「手工执行必须同时给 `TA_WORK_ROOT` 与
   `TA_ENV_FILE`，已写进 §10.4」，而 §10.4 里**并没有**这条。修法：§10.4 补
   「**手工执行硬规矩**」小节（含 17:58:53 事故的形态说明），§12.4 的空头承诺改掉。

**判据的一处修正（自查发现）**：复审给的路线 B 建议把
`[ -z "${XPC_SERVICE_NAME:-}" ]` 也纳入判据，但**agent 运行时同样导出
`XPC_SERVICE_NAME`**（本机实测 `com.davidliu.multica-steer`）→ 「agent shell 里的
手工执行」会被误判成 launchd 而**保持沉默** —— 恰恰是要警告的那种情况。
故最终判据**只用 `TA_WORK_ROOT` 是否显式给出**（plist 一定给，手工跑不给）。

### 13.4 5 条 🟢

| 编号 | 处理 |
|---|---|
| 🟢-1 | `_src_errfile` 会漏进管道子进程 env（指向已删除文件）→ `unset _src_errfile`（已含在 🟡-2 的改动里） |
| 🟢-2 | 临时文件建不出来时的 fallback 会**重放被修的 bug**（实测 `TMPDIR` 不可写 + 坏行 → rc 0、零告警）→ 改为 **fail-closed**（exit 16），见 §13.2 |
| 🟢-3 | 告警行号重复（`line(s) 1,1`）→ `sort -u` 去重 |
| 🟢-4 | 「stderr 原文一律丢弃」**没有回归用例** → 新增 `test_the_quoted_credential_line_never_reaches_alert_log_or_child_env`（用 `TUSHARE_TOKEN={{SECRET_…}}(` 这种**带密钥形态**的坏行，断言值在告警/日志/临时目录里都不出现） |
| 🟢-5 | 文档 §10.1 / §10.5 仍写「`ALERT.launchd` 现为 0 字节空文件」→ 更正为「**自 2026-10-20 17:58:53 起是 455 字节的 exit 16 假告警**（复审员误执留下的痕迹），15:56 的原文仍在 `ALERT.launchd.stray-20261020T1556`（380 字节，exit 11），留证件未动」 |

### 13.5 本轮新增用例（7 条）

`test_help_prints_the_usage_block_without_the_reminder_line`、
`test_stderr_temp_file_is_private_and_leaves_nothing_behind`、
`test_unusable_tmpdir_fails_closed_instead_of_silently_accepting`、
`test_the_quoted_credential_line_never_reaches_alert_log_or_child_env`、
`test_launchd_run_does_not_log_the_manual_run_warning`、
`test_manual_run_without_ta_work_root_does_warn`、
`test_plist_declares_the_production_work_root`。

## 14. 发布卡前置条款（总控 10-20 19:40 令，源自 DAV-2297）

> **安装前任何人不得往 `~/ta-p4-labels-cb82987b` 提交或修改。**
> **安装时核对它的工作树状态与今天一致 —— 只有那 3 行未提交改动**
> （解释器 → `~/ta-venv/py310`、`CODE_DIR` → 该检出、`sys.path.insert` → 该检出）。

**本条款必须原样带入 2284 的发布卡**（复审通过后建卡时写入卡面前置项）。

### 14.1 今日基线（2026-10-20 12:20 实测，作为发布卡核对基准）

`~/ta-p4-labels-cb82987b` 的 HEAD = `cb82987b737be2cc3c40c939082b70c4135db7b8`（detached），
工作树**只有 1 个文件被改、共 3 行**（`scripts/phase2/run_expiry_label_daily.sh`）：

```
PYTHON="/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"
   → PYTHON="/Users/davidliu/ta-venv/py310/bin/python"
CODE_DIR="/Users/davidliu/ta-p4-ef8aa9d9"
   → CODE_DIR="/Users/davidliu/ta-p4-labels-cb82987b"
sys.path.insert(0, "/Users/davidliu/ta-p4-ef8aa9d9")
   → sys.path.insert(0, "/Users/davidliu/ta-p4-labels-cb82987b")
```

发布卡可直接用下列指纹核对（**任一不符即停**）：

| 核对项 | 今日基线值 |
|---|---|
| `git -C ~/ta-p4-labels-cb82987b status --porcelain` | ` M scripts/phase2/run_expiry_label_daily.sh`（**仅此 1 行**） |
| `git -C ~/ta-p4-labels-cb82987b diff --numstat` | `3	3	scripts/phase2/run_expiry_label_daily.sh` |
| 未跟踪文件数 | **0** |
| `sha256`（该文件全文） | `5eddb170a8efd82ef08c269b4d6e191c2465114fda9f03ca62734f7c33df9945` |
| `sha256`（`git diff` 输出） | `3675ba0dea4d4686b7cce1c5f90eb6f95e8222c6f4b04ed92d6012503c00738c` |
| `expiry_label_pipeline.py` 是否存在 | **是**（旧 `CODE_DIR` 指向的 `~/ta-p4-ef8aa9d9` 里**没有**） |

### 14.2 为什么必须冻结（DAV-2297 的因果链）

那 3 行**只存在于工作树**，不在任何提交里；该检出是 detached HEAD。
一旦有人执行 `git checkout -- .` / `git restore` / `git clean` / `git reset --hard`
/ `git stash`，这 3 行就没了 → `CODE_DIR` 退回 `~/ta-p4-ef8aa9d9` → 那个检出里
**没有 `expiry_label_pipeline.py`** → 20:10 的定时任务**立刻 exit 11**。
2026-10-20 15:56 那次失败正是这个形态。

### 14.3 与本卡候选的关系（本卡未动这个检出）

- 本卡候选 `5239b320` **没有**改 `~/ta-p4-labels-cb82987b` 的任何文件
  （实测 `git status` 仍只有那 1 个 `M`、3 行）。
- 本卡候选**没有**动 `scripts/phase2/expiry_label_pipeline.py`
  （`git diff e534a6f9 5239b320 -- scripts/phase2/expiry_label_pipeline.py` 为空）。
- 本卡候选把 `CODE_DIR` 从写死改成 **`TA_CODE_DIR` env 优先 + 脚本自身目录兜底**，
  因此**新装**的作业不再依赖「检出 + 本地改动」——这正是 DAV-2297 冻结解除条件之一。
- **但已装作业此刻仍跑旧逻辑**：旧 wrapper 里 `grep -vEq` 的 rc=2（EPERM）被当成
  「文件干净」→ 继续 `source` 同样 EPERM → 报成 exit 13「TUSHARE_TOKEN missing」
  （与 10-19 同形）。旧 wrapper 也**完全没有** `TA_ENV_FILE` 支持（实测计数 = 0）。
  → **合入+安装前，今晚 20:10 的作业仍会以 exit 13 失败**（这是已知风险，
  DAV-2297 已登记；不是本卡引入）。

### 14.4 发布卡建议核对顺序（不改代码）

1. 装前：按 §14.1 的指纹核对 `~/ta-p4-labels-cb82987b`（**冻结期内不得有 git 写操作**）；
2. 装：**新建**一个含本卡候选的固定 release 检出（`{{LABELS_DIR}}` 三处指向它），
   `test -f "${LABELS_DIR}/scripts/phase2/expiry_label_pipeline.py"` 必须为真；
3. 装后：核 `ProgramArguments` / `WorkingDirectory` / `TA_CODE_DIR` **三者解析到同一目录**，
   且该目录**不是** `~/ta-p4-ef8aa9d9`；
4. 跑一次 `launchctl kickstart -k`，看 `label_pipeline.log` 是否出现
   `skip:`（非交易日）或 `timer done`（交易日）——**不是** `ALERT`；
5. 旧检出 `~/ta-p4-labels-cb82987b` 的 3 行改动在装好之后**不再需要**
   （总工 10-20 17:20 令的第二项核对），但**本卡不代为清理**（需总工确认后再动）。

## 15. 第五轮返修（DAV-2292 对 `5239b320` 的 1🟡 + 5🟢）

### 15.1 🟡-1 「原文一律丢弃」在代码里不成立：`_src_err` 被 `set -a` 导出

**复审诊断正确且已复现**：`set -a` 把窗口内**每一次赋值**标记为 exported，
所以 `_src_err="$(cat …)"`（装的是 `.` 的原始 stderr，而 bash 的语法错消息
**逐字引用出错行** → 对凭据文件那行**就是值**）会被导出，
`:337` 的 `rm` 与 `:348` 的 `sed`/`sort`/`tr` 三个 helper 的 environ 里都带着原文。
这与代码里「the raw stderr is NEVER written … anywhere else」的断言**直接矛盾**，
也与第四轮把「0644 可预测名临时文件装同一段原文」判为 🟡 的口径不自洽
（同一段原文，换了个容器）。

**更深的根因（复审未点出、本轮自查发现）**：**`_src_errfile`（临时文件路径）
也在窗口内赋值**，同样被导出 → helper 的 env 里还带着那个路径。

**修法（顺序即修法）**：临时文件**在窗口外**创建，窗口只包住 `.` 本身：

```bash
_src_errfile="$(umask 077; mktemp …)" || _src_errfile=""   # 窗口外
[ -z "${_src_errfile}" ] && alert … 16                      # 窗口外
set -a            # ← 窗口现在只包住 source
set +u
. "${ENV_FILE}" 2>"${_src_errfile}"
_src_rc=$?
set -u
set +a            # ← 立刻关
_src_err="$(cat "${_src_errfile}" 2>/dev/null)"   # 普通变量，不导出
rm -f "${_src_errfile}"
unset _src_errfile   # ← 在任何后续 helper 之前
SOURCE_RC=${_src_rc}; unset _src_rc
set -u
```

（`SOURCE_RC` 的捕获位置**未动** —— 复审明确说它本来就对。）

### 15.2 用例从「空检查」变成「真闸」

**问题 2（原用例守不住）**：用例名宣称 `…_never_reaches_alert_log_or_child_env`，
断言却只有三条，其中 `:959` 拿到的是**路径名字符串列表**、不是文件内容，
且**没有任何一条 query 子进程 env**；桩解释器也不 dump env。
复审的变异测试（把泄漏做大）→ **41 条仍全绿**。

**本轮的三处补强**：
1. 桩解释器在收到环境时 **dump 一次 `env`**（`STUB_ENV_DUMP`）；
2. `:959` 改成**真的读文件内容**（`p.is_file()` → `p.read_text()`），
   并排除「输入的凭据文件本身」；
3. 新增 **helper env 记录器**：把 `cat`/`rm`/`sed`/`sort`/`tr` 换成记录自己
   environ 的桩（装在 PATH 前面，wrapper 调的是**裸命令**，只能靠 PATH 拦截），
   断言 `_src_err=`、`_src_errfile=` 与**假值**都不在其中。

> 途中踩到的三个坑（都写进注释）：
> - `cat`/`rm` 在 macOS 是 `/bin/`，`sed`/`sort`/`tr` 在 `/usr/bin/` ——
>   桩里写死 `/usr/bin/$tool` 会让 `rm` 桩报 `No such file or directory`，
>   文件**永远删不掉**，测试误报「有残留」；
> - 桩目录要注入 wrapper **自己 export 的那行 PATH**（wrapper 会覆盖 PATH），
>   塞进子进程 env 没用；
> - 测试用的临时文件必须落在 `tmp_path` 内（改写 wrapper 里的 `mktemp` 模板），
>   否则会污染共享 `/tmp`，并让「无残留」断言被**别的运行**的文件带崩。

### 15.3 变异测试（证明用例是真闸）

| 变异 | 期望 | 实测 |
|---|---|---|
| A：把 `_src_err` 的导出做大（复审员第五轮的做法：`export _src_err`） | 至少 1 条红 | **1 failed**（`test_the_quoted_credential_line_never_reaches_alert_log_or_child_env`），40 passed |
| B：退回上一轮的可预测 0644 临时文件（第四轮打回项） | 至少 1 条红 | **1 failed**（`test_stderr_temp_file_is_private_and_leaves_nothing_behind`），40 passed |

（两次变异都在**副本**上做，跑完即还原；仓库文件零改动。）

### 15.4 5 条 🟢

| 编号 | 处理 |
|---|---|
| 🟢-1 | `test_stderr_temp_file_is_private_and_leaves_nothing_behind` 原来不断言 0600、不断言名字不可猜（复审变异测试：退回可预测 0644 仍 1 passed）→ 现在用**探针桩**（`cat` 桩 stat 它被要求读的文件）把 **mode 与 path** 记录下来，断言 mode ∈ {600,400}、名字不是 `.$$` 形式、后缀 ≥ 6 字符；并把 wrapper 的临时文件重定向到 `tmp_path` 内 |
| 🟢-2 | plist 里 work root 有 3 处裸字面量 → 全部改用 **`{{WORK_ROOT}}`** 占位符（`TA_WORK_ROOT` / `StandardOutPath` / `StandardErrorPath`），「Path coupling」补第 5 项，安装段的 `sed` 补 `{{WORK_ROOT}}` 替换。**注**：当时自述「检查段补 `StandardOutPath` 的占位符核对」是**不成立**的 —— 那一轮并没有真的加上检查（全文没有任何一处查 `WORK_ROOT` 或查 `{{` 残留），复审员在第六轮指出后才真正补上（见 §18） |
| 🟢-3 | 「手工执行」WARN 打在 `mkdir -p "${OUT_DIR}"` **之后** → 现在在**第一笔写之前**先往 **stderr** 打一行（`log()` 要写 `LOG_FILE`（在 OUT_DIR 内）无法整体前置，但 stderr 可以） |
| 🟢-4 | 退出码表 16a–16e 漏了「拿不到私有临时文件」这条 → 补 **16f**（并把「五种原因」改成「六种」） |
| 🟢-5 | 文档现场数字被今晚 20:10 的运行写旧 → 更正为「`ALERT.launchd` **378 字节、exit 13**（现装作业的定时运行）、`label_pipeline.log` **47 行**；15:56 的原文仍完整保存在 `ALERT.launchd.stray-20261020T1556`（380 字节，exit 11），**留证件未动**」 |

### 15.5 本轮新增用例（3 条）

`test_stderr_temp_file_is_private_and_leaves_nothing_behind`（改为断言 mode/名字/残留）、
`test_the_quoted_credential_line_never_reaches_alert_log_or_child_env`（补子进程 env 与
文件内容断言）、`test_plist_work_root_appears_in_exactly_three_places_as_one_placeholder`。

## 16. 发布卡追加核对（总控 10-20 21:50 令）

> **新标签检出里的 `expiry_label_pipeline.py` blob 必须等于 `ed9c330d`；不等就停。**

**理由**：2129 链改了**标签管道**与**账本脚本**；2129 一旦并入主干，2284 发布时
新建的标签检出会**顺带带上改过的标签管道**，等于在 10-22 读数前一天换掉标签生成方式。
**2129 已改为在 10-22 读数完成之后才合入**，故本核对项是发布卡上的兜底闸。

### 16.1 令文里那个 blob 是什么（已核实）

`ed9c330d` 是 **blob**（文件内容哈希），不是 commit：

```
ed9c330dff600a764cd8c5ac990e75cfc66f7fd7
  = scripts/phase2/expiry_label_pipeline.py @ 主干（当前 tip ec98d2bf；2026-10-20
    实测该 tip 上仍是这个 blob —— 主干在此期间前进过，但 DAV-2271 的三个提交
    未触及该文件）
  = 1444 行，DAV-1875 标签管道
```

- **主干 tip 上该文件的 blob 正是 `ed9c330d`** → 令文要求的是「**当前主干版**」。
- 2129 提交（`64664a96 feat(dav-2129)`）之后的 blob 是 **`9c14979d9dd671ecf403bd93a804c125e7f9b3a1`** ——
  **不同** → 这正是本核对项要拦的东西。
- 2129 目前**未并入主干**（`git merge-base --is-ancestor 64664a96 FETCH_HEAD` 为假；
  它只在 `origin/agent/1/28d08e4bfd38` 上），与令文「10-22 读数后才合入」一致。
  → **本卡交付时该核对项必然通过**；它是一条**兜底闸**，防的是「发布窗口内 2129
  抢先合入」或「发布检出拉到了别的分支」。

### 16.2 发布卡可直接执行的核对命令

```bash
# 在**新建的标签检出**里（不是主检出）：
git -C "${LABELS_DIR}" rev-parse HEAD:scripts/phase2/expiry_label_pipeline.py
#   必须输出 ed9c330dff600a764cd8c5ac990e75cfc66f7fd7
#   不等 → 停（该检出带上了 2129 的标签管道）
```

等价的**内容**核对（不依赖 git 对象库，直接比对文件）：

```bash
git -C "${LABELS_DIR}" hash-object scripts/phase2/expiry_label_pipeline.py
#   同样必须等于 ed9c330dff600a764cd8c5ac990e75cfc66f7fd7
```

（两条命令均已在临时检出里**实跑验证**，输出确为
`ed9c330dff600a764cd8c5ac990e75cfc66f7fd7`；路径必须是检出内的
`scripts/phase2/expiry_label_pipeline.py`。）

> 注意：不要用 `git rev-parse <commit>:path` 去查**主干 tip**——那永远等于
> `ed9c330d`，测不到「新建检出里到底是什么」。必须查**新建检出自己的 HEAD**。

### 16.3 本会发布卡的其余前置（已并入本发布会话，令文原文）

1. **安装前任何人不得往 `~/ta-p4-labels-cb82987b` 提交或修改**；安装时核对它的
   工作树**只有那 3 行改动**（基线指纹见 §14.1）；
2. **`bootstrap` 前先跑 `plist_log_preflight.py`，FAIL 就停**；
3. 安装窗口 **10-21 晚上 或 10-22 白天**；卡面写「**总控签字后执行**」。

### 16.4 preflight 的实测（本卡已代跑，供发布卡参考）

`releases/launchd/plist_log_preflight.py <plist>` 是通用工具（接受任意 plist，
「占位符须已替换」）。本卡用候选 plist 实测：

| 场景 | 结果 |
|---|---|
| `{{WORK_ROOT}}` 指向**尚不存在**的目录 | **FAIL `[LOGPATH_PARENT_MISSING]`** → 正是它要防的 EX_CONFIG 78（launchd 不会自建日志父目录） |
| `{{WORK_ROOT}}` 指向**真实** `/Users/davidliu/ta-p4-2e1e199f/work` | **PASS**（2 条 `LOGPATH_TARGET_EXISTING_APPENDABLE` WARN，**不阻断**；既存日志文件属主=当前 uid、可追加写） |

→ 发布卡在 `bootstrap` 前跑它时，**预期结果是 WARN 而非 FAIL**；若出现 FAIL
（多为 `LOGPATH_PARENT_MISSING`）说明 work root 下的 `phase2-labels/` 目录还没建，
**先建目录再装**。

### 16.5 与本卡交付的关系

本卡**未改** `scripts/phase2/expiry_label_pipeline.py`
（`git diff e534a6f9 <候选> -- scripts/phase2/expiry_label_pipeline.py` 为空），
所以本卡候选建出的检出，其该文件 blob **就是** `ed9c330d`。
本节只是把令文登记进分支，供发布卡逐字照搬。

## 17. 复审 PASS 后的基线更正（2026-10-20 23:0x，DAV-2292 指出）

**DAV-2292 已对最终候选 `18193767` 出 PASS**（无 🔴/🟡）。该结论同时指出**我此前
卡面里的一条事实陈述是错的**，发布卡**不要照抄**：

> 「主干 tip `ec98d2bf` 是候选的祖先」——**不成立**。

实测：

```
$ git merge-base --is-ancestor ec98d2bf 18193767 ; echo $?
1                                   # 假
$ git merge-base ec98d2bf 18193767
e534a6f9c0606fc9f2f50ec00ea4d8000ea9c118
$ git log --oneline 18193767..ec98d2bf | wc -l
3                                   # 候选落后主干 3 个提交（DAV-2271 链）
```

即候选**落后**主干 3 个提交（`ec98d2bf` / `c93495f1` / `4ae152a9`，DAV-2271 链）。
连带后果：卡面写的「`git diff --numstat e534a6f9→ec98d2bf` 后仍是同样 5 个白名单
文件」**不成立** —— 实跑 `git diff --numstat ec98d2bf 18193767` 是 **8 个文件**，
多出 `scripts/phase2/m1_base_rate.py`（3/126）与
`tests/test_dav2271_signal_date_shape_guard.py`（0/289），
**方向是「候选里没有这些」，不是越界改动**。

**发布卡必须用 `e534a6f9` 作比对/合入基线**（不是 `ec98d2bf`），
否则现场核「与主干差 5 文件」会核不过。

**对标签发布无影响**：本卡只依赖
`scripts/phase2/{run_expiry_label_daily.sh, ta_secrets_install.sh, expiry_label_pipeline.py}`，
且 `git rev-parse 18193767:scripts/phase2/expiry_label_pipeline.py` =
`ed9c330dff600a764cd8c5ac990e75cfc66f7fd7` = 当前主干 tip 的同一路径
→ §16 那条「blob 必须 = `ed9c330d`」的核对项**值不变** ✓。

> 教训（复审员一并指出）：「候选相对主干非空」「主干 tip 是候选的祖先」这类断言，
> 今后一律用 `merge-base --is-ancestor` **实测**，不要以「主干 tip 比候选新」推定。
## 18. 第六轮返修（DAV-2292 对 `233aeb96..c76d5913` 的 1🟡 + 2🟢）

### 17.1 🟡-1 `{{WORK_ROOT}}` 只改了 plist，§3 的安装 `sed` 与检查都没跟上

**复审诊断正确，已复现**（复审员给的最小复现，我逐字跑过）：

```
$ LABELS_DIR=/tmp/x
$ sed -e "s|{{LABELS_DIR}}|${LABELS_DIR}|g" \
      scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist > /tmp/installed.plist
$ grep -c '{{WORK_ROOT}}' /tmp/installed.plist            → 5   （3 处是活值）
$ plutil -extract EnvironmentVariables.TA_WORK_ROOT raw -o - /tmp/installed.plist
                                                            → {{WORK_ROOT}}
$ plutil -extract StandardOutPath raw -o - /tmp/installed.plist
                                                            → {{WORK_ROOT}}/phase2-labels/launchd.out.log
```

而 §3 紧跟的两条检查只 `grep -q 'LABELS_DIR'` → **全部通过**，`plutil -lint` 也是 OK。
后果：照 §3 装，10-22 20:10 的标签任务会以 `TA_WORK_ROOT='{{WORK_ROOT}}'` 启动 →
**exit 12**（forward ledger missing: `{{WORK_ROOT}}/phase2-ledger/forward_ledger.jsonl`），
并在 `WorkingDirectory`（= release 检出）里建出一个名为 `{{WORK_ROOT}}` 的垃圾目录。

**三处修法（纯文档 + plist 内嵌注释，不改脚本逻辑）**：

1. **§3 的安装 `sed` 补第二条替换**（与 plist 自己的安装注释 `:44-46` 对齐）：
   ```bash
   WORK_ROOT=/Users/davidliu/ta-p4-2e1e199f/work
   sed -e "s|{{LABELS_DIR}}|${LABELS_DIR}|g" \
       -e "s|{{WORK_ROOT}}|${WORK_ROOT}|g" \
       scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist \
       > ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist
   ```
2. **检查段改成查「残留占位符本身 `{{`」**（不再查某个具体名字 → 以后再加占位符
   也不用改检查段），并**补上 `StandardOutPath` / `StandardErrorPath` 两条**
   （旧版漏了它们，而它们恰恰是活值）。§3 与 plist 内嵌注释**两处都改了**。
3. **§15.4 🟢-2 的自述更正**：那一轮声称「检查段补 `StandardOutPath` 的占位符
   核对」，**实际没补**（全文没有任何一处查 `WORK_ROOT` 或查 `{{` 残留）——
   与本卡反复打回的「自述与实际不符」同型。已在 §15.4 标注为不成立。
   **§11.3 的表格补了** `TA_WORK_ROOT` / `StandardOutPath` / `StandardErrorPath`
   三行，并把「替换后 `grep -c '{{'` 必须为 0」这句与 §3 的实现对齐。

**修后实测**（同一台机、逐字照抄 §3）：

| 场景 | 旧检查 | 新检查 |
|---|---|---|
| 两个占位符都替换 | PASS | **PASS** ✓ |
| 只替换 `{{LABELS_DIR}}`（旧 §3 的 sed） | **PASS ← 假绿** | **FAIL**（`EnvironmentVariables` / `StandardOutPath` / `StandardErrorPath` 三处都拦下） |
| 模板原样（两个都没替换） | FAIL（旧检查在模板上也是 FAIL：`ProgramArguments` 含 `{{LABELS_DIR}}` → grep 命中 → 取反 → exit 1） | FAIL |

### 17.2 2 条 🟢

- **🟢-1 `_src_rc` 仍进 helper 的 environ** → 已修：`_src_rc` 是在 allexport 窗口内
  赋值的（第五轮修 `_src_err` 时没顾上它），所以 `cat`/`rm` 的 env 里有 `_src_rc=0`。
  修法用 `export -n _src_rc`（在窗口内、赋值之后去掉导出，**不改变值**），
  随后立刻 `set +a`。**注意**：`set +a` 会把 `$?` 覆盖成 0，所以**不能**把捕获
  放到 `set +a` 之后 —— 必须先捕获、再 `export -n`、再 `set +a`。
  新增用例 `test_no_script_private_variable_reaches_a_helper_environ`
  （断言 `_src_rc=` / `_src_err=` / `_src_errfile=` / `_src_lines=` 都不在
  helper 的 environ 里）。**变异测试**：删掉那行 `export -n` → 该用例 FAILED ✓。
- **🟢-2 §16.1 的「主干 tip e534a6f9」过期** → 改为「主干当前 tip `ec98d2bf`
  上该 blob 仍为 `ed9c330d`（2026-10-20 实测）」。**结论不变**：
  `git rev-parse ec98d2bf:scripts/phase2/expiry_label_pipeline.py` =
  `ed9c330dff600a764cd8c5ac990e75cfc66f7fd7`（DAV-2271 的三个提交未触及该文件）。

### 17.3 本轮新增/改强的用例（3 条）

- `test_the_documented_placeholder_check_returns_zero_after_installation`
  → 改为**替换两个占位符**，并用统一的 `_placeholder_check()` 助手跑**文档里那四条**
  检查；模板原样必须 FAIL。
- `test_the_documented_placeholder_check_catches_a_half_resolved_plist`
  → **新增**：只替换 `{{LABELS_DIR}}` 时检查必须非 0（这正是复审员指出的假绿形态）。
- `test_no_script_private_variable_reaches_a_helper_environ` → **新增**（🟢-1）。

> 本轮**未改任何脚本的行为**（只加了 `export -n _src_rc` 一行去泄漏，
> 不改判定逻辑），因此**无需重跑 RT-FULL**。


## 19. 复审 PASS 后的 3 条 🟢 收尾（2026-10-20 23:1x）

DAV-2292 对最终候选 `18193767` **出了 PASS**（无 🔴/🟡），并附 3 条**不阻塞**的 🟢。
三条都很便宜，本轮一并做掉，免得再留「自述与实际不符」的小账：

| # | 处理 |
|---|---|
| 🟢-1 | §18.1 表格里「模板原样（两个都没替换）」那格的**旧检查**列原写 `PASS`，**实际是 FAIL**（`ProgramArguments` 含 `{{LABELS_DIR}}` → grep 命中 → 取反 → exit 1；复审实测 `old check FAIL (rc=1)`）→ 已改成 `FAIL`。旧检查唯一的假绿是「只替 `{{LABELS_DIR}}`」那一行（该行写得对） |
| 🟢-2 | **安装指引的文本本身此前没有任何用例钉住** —— 复审的变异 D+E（删掉 §3 的 `-e "s\|{{WORK_ROOT}}|…"` 一行 + 把 plist 注释里 4 条 `grep -q '{{'` 全部退回 `grep -q 'LABELS_DIR'`）→ **44 passed, 0 failed**：因为那三条用例跑的是 `_placeholder_check()`（Python 复刻），不是操作员真正执行的那几行 shell。现补 `test_the_install_instructions_text_is_pinned`：**§3 与 plist 安装注释各必须含 2 条 `-e` 替换与 4 条 `grep -q '{{'`，且不得再有只查某个具体名字的 `grep -q 'LABELS_DIR'`**。**变异 D/E 实测各红 1 条** ✓ |
| 🟢-3 | `_placeholder_check()`（Python 复刻）与文档 shell 在「key 缺失」时语义不同：helper 在 `plutil -extract` 返回非 0 时 fail-closed，而文档的 `! plutil … \| grep -q '{{'` 拿到空输入 → grep rc=1 → 取反 → **判通过**。当前模板四个 key 都在、后面还有 `plutil -lint`，**无实际影响**；已在 helper 的 docstring 里写明「helper 是复刻、不是文档，两者别当同一个东西」，并说明谁守文本、谁守行为 |

### 复审 PASS 的实测数据（可引用）

- 父侧 `c76d5913` / 候选侧 `18193767` **各跑一次 RT-FULL**（标准口径，含 DAV-979 deselect）：
  `1 failed, 8227 passed` → `1 failed, 8229 passed, 29 skipped, 7 deselected, 3 subtests passed`
  —— 差值 +2 = 本轮两条新用例，**无新增失败**；两侧那 1 failed 是同一条
  `test_dav1392_price_gate.py::…readonly_corpus_available`（读生产语料，父侧同样 failed）。
- 定向组（5 文件）：**153 passed, 1 skipped**（与开发自报一致）。
- 复审的冻结核对：审前审后两次 `git ls-remote` 均为 `18193767…`，**分支头未变、冻结未被破坏**。
- 复审抽查的既有不变量全部保持：闸唯一定义且与主干 inline 版**逐字节相同**
  （143 字节，sha256 `bb573be14dc0312561890446e22f63cde7a54c8597d3969a118dfe068d2054ad`）；
  路径写死三项仍为 env 优先 + 脚本目录推导（`ta-p4-ef8aa9d9` 只在注释里作为历史说明出现）；
  护栏 `source` → rc 64 且**连工作目录都没建**；正常 `bash <脚本>` → rc 12（不是 64）；
  退出码表 16a–16f + 18 + 64；手工 WARN 在 `mkdir -p "${OUT_DIR}"` 之前。
