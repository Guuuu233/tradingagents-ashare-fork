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
# 0. 代码检出升到含本卡的 trunk（CODE_DIR 内必须有 expiry_label_pipeline.py）
# 1. 生成/刷新凭据副本：目录 700、文件 600、原子落位、不打印任何值
scripts/phase2/ta_secrets_install.sh
scripts/phase2/ta_secrets_install.sh --check     # 严格门禁，非 0 即停

# 2. 装 plist（TA_ENV_FILE 已是绝对路径 /Users/davidliu/ta-secrets/ta.env）
cp scripts/phase2/com.davidliu.ta-p4-expiry-labels.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.davidliu.ta-p4-expiry-labels.plist

# 3. 一次手工触发（非交易日会 exit 0 + 一行 skip 日志，也算通过）
launchctl kickstart -k gui/$UID/com.davidliu.ta-p4-expiry-labels
```

## 4. RESTART.md 待粘贴段落（卡面第 5 项）

把下面整段追加到宿主机 `/Users/davidliu/Documents/TradingAgents-AShare-releases/RESTART.md`
（建议追加在文末「回退方法」之前；该文件不入 git，无法在本分支 diff 校验）：

````markdown
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
````

只改一处会出现「服务正常但标签任务 nightly exit 13/18」或「标签任务用旧 key」，
两种都不会自动报警到人。

````

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
| 🟡-1 | `--src` / `--dest` 缺值时 `shift 2` 失败且不移动位置参数 → 参数循环永不前进（实测 >4s 仍在转，~70% CPU、零输出、只能 kill）。**本脚本是发布卡安装第 1 步**，运维漏一个值就表现为「命令卡住不说话」 | 改为先判 `$#` 再取 `$2`（两个独立 `if`：`set -u` 下 `a || b` 也会先展开 `$2`，`||` 救不了），缺值 → **rc 2** 立即报错（实测 0.004s）；同时把 `say`/`fail`/`mode_file` **上移到参数循环之前**（原先 `fail` 在循环后才定义，循环里调用会 `command not found` 然后继续往下跑） |
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
label_pipeline.log  追加两行：
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
| 告警与日志 | **被污染**：生产 `ALERT.launchd` 被覆盖（现为 0 字节空文件，原内容已由总工留证）、`label_pipeline.log` 追加 2 行 |

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
| `test_plist_*` / `test_installer_gate_is_byte_identical_*` | 无写入 | 无 | 否 |

实现机制：wrapper 的沙箱化是**改写被拷贝副本里的固定常量**（`PYTHON=`、
`ENV_FILE=`、`CODE_DIR=`、`WORK_ROOT=` 四个），`OUT_DIR` 由 `WORK_ROOT` 推导，
因此**测试永远落在 `tmp_path` 下**；测试文件里**不出现** `~/ta-p4-2e1e199f`
与 `~/Documents/TradingAgents-AShare`（仅一处断言 plist 里的 `TA_ENV_FILE` 字面量，
不读文件）。

**既有 `tests/test_dav1875_expiry_label_pipeline.py` 的 wrapper 用例**同构
（`_wrapper_harness` 改写同样四个常量），也全部落在 `tmp_path` 下。

### 10.5 残留

- `ALERT.launchd` 现为 **0 字节空文件**（总工改名留证后的残留），不是有效告警；
  有效内容在 `ALERT.launchd.stray-20261020T1556`。若发布卡要求目录干净，
  可删该空文件（**不删留证文件**）——本卡未动生产目录。
- `label_pipeline.log` 尾部两行是 15:56 误执行的痕迹；未删（属取证材料）。
- 15:56 误执行**读到了真实凭据**（仅在进程内，未落盘、未外泄）；本卡未轮换密钥，
  是否轮换由总控定。
