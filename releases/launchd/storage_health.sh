#!/bin/zsh
# 存储健康巡检 I — 统一脚本 v6（每日巡检 + 每周三基线上传清单）
# 口径（2026-10-13 路径迁移版，快照目录统一 ~/ta-snapshot；DAV-2219 / DAV-2238 修订）：
#   每日：日期 + 水位三色(>=100GiB绿/60-100GiB黄只记录/<60GiB红立即唤醒并停止建整库副本，df -g)
#         + 热备预算(三项，见下)<=15GiB（精确 KiB 比较）+ 无主大文件清单
#   DAV-2238（按 DAV-2237 §10 签字对齐热备口径，1508 安装前必修）：
#     预算项 = 三项单列，合计判 `≤ 15728640 KiB`（= 15 GiB）：
#       ① `backups/`（$REL/backups）② `~/ta-snapshot/*.db.zst` ③ 发布期 `.pre-slim` 类副本；
#     不计入：活库 `data/tradingagents.db`、`~/ta-snapshot/current.db`（未压缩工作副本，
#       归 D-073 整库副本上限管）、`~/ta-work/dav-2013/` 实验副本（脚本不扫 ta-work）；
#     du 失败/无输出/非数值 ⇒ 该测量项记「不可测」，热备行判「红」（fail-closed）+ 退出码 2。
#   DAV-2239 🟡-1（返修）：预算项②③改用 `find` 枚举并**检查退出码**——目录「存在但不可读/
#     不可进入」时 zsh 的 `(N)` 通配会静默返回 0 个匹配（无 stderr、无失败态），会把该预算项
#     按 0 计入并可能判绿；find 报错时 `rc≠0`，据此记「不可测」。find 也不跳过 `.` 开头的
#     路径段，隐藏目录/隐藏文件同样枚举到。三类状态写死：不存在→跳过；可读→枚举（可为 0 个
#     文件）；存在但不可读/枚举失败→不可测。
#     同时（同一根因收口）：路径存在性用 `path_state` 三态判定（present/absent/unknown），
#     `[ -e ]` 失败不等于「不存在」——上级目录不可读时 stat 报 EACCES，必须按 unknown 记不可测，
#     否则整棵看不见的目录树会被静默按 0 计入（含 `backups/` 与 PROJ）。
#     🟢-1（同批）：已测得部分和就超预算时，先出「红（超预算）」（部分和只会低估，判定可靠），
#     并把「另有 N 项不可测、合计不完整」附在同一条里。
#   DAV-2219：
#     🟡-2 预算按 KiB 精确比较（≤15728640 KiB = 15 GiB），不再整数截断；
#     🟢-2 工作树/活库单列、不计预算（否则含活库长期假红，DAV-2216 实测）。
#   每周三：从 DAV-1528 产出目录 ~/ta-snapshot/ 挑当周最后一份
#           tradingagents-*.db.zst 作为周基线，记录大小+SHA256 并上传夸克；
#           （DAV-2017：快照目录 10-13 自 /private/tmp/ta-snapshot 迁出）
#           夸克只留最近 2 份，超出部分列清单由人手动清理，脚本绝不动云端。
#           （DAV-2017：快照目录 10-13 自 /private/tmp/ta-snapshot 迁出至 ~/ta-snapshot）
#           本卡不再另做整库备份。
# 只读纪律：df/du/find/ls/shasum；无任何删除逻辑（基线不再本地轮转——
#           DAV-1528 自行保留最近 3 份快照）。
set -u
PROJ="${STORAGE_PROJ:-$HOME/Documents/TradingAgents-AShare}"
REL="$(dirname "$PROJ")/TradingAgents-AShare-releases"
SNAP_DIR="${TA_SNAPSHOT_DIR:-$HOME/ta-snapshot}"
QUARK_REMOTE_DIR="${QUARK_BASELINE_DIR:-/TradingAgents-AShare/baselines}"
MODE="${1:-auto}"   # daily | weekly | auto（auto=每日巡检，周三附带基线）
NOW="$(date '+%Y-%m-%d %H:%M %Z')"
DOW="$(date '+%u')" # 3=周三

# KiB → GiB 一位小数（四舍五入到 0.1，仅用于展示；判定一律走精确 KiB）
fmt_gib() {
  local t=$(( ($1 * 10 + 524288) / 1048576 ))
  printf '%d.%d' $(( t / 10 )) $(( t % 10 ))
}

# 单目录占用测量：成功打印 "OK <KiB>"，失败/无输出/非数值打印 "ERR <原因>" 并返回非零。
# DAV-2219 🟡-1：du 失败绝不按 0 计入（测不到 ≠ 占用为 0），由调用方判「未知」。
# 注意 stderr 一并收进变量（2>&1）以便写进报告留证，而不是丢弃。
du_kb() {
  local out rc first kb
  out="$(du -sk "$1" 2>&1)"; rc=$?
  first="${out%%$'\n'*}"
  kb="${first%%[[:space:]]*}"
  if [ "$rc" -ne 0 ]; then
    [ -n "$first" ] || first="du 无输出（退出码 $rc）"
    echo "ERR $first"; return 1
  fi
  case "$kb" in
    ''|*[!0-9]*) echo "ERR du 输出非数值：$first"; return 1 ;;
  esac
  echo "OK $kb"; return 0
}

# 多路径合计测量：用于热备预算里的「文件集合」（快照 *.db.zst、.pre-slim 副本）。
# 成功打印 "OK <KiB 合计>"，失败/无输出/非数值打印 "ERR <首行原因>" 并返回非零。
# 调用方必须保证至少传入 1 个真实路径——`du` 无参数会误测当前目录！
du_kb_total() {
  local out rc first kb
  out="$(du -sk "$@" 2>&1)"; rc=$?
  first="${out%%$'\n'*}"
  if [ "$rc" -ne 0 ]; then
    [ -n "$first" ] || first="du 无输出（退出码 $rc）"
    echo "ERR $first"; return 1
  fi
  [ -n "$out" ] || { echo "ERR du 无输出（退出码 0，输出为空）"; return 1; }
  kb="$(printf '%s\n' "$out" | awk '$1 ~ /^[0-9]+$/ {s += $1} END {print s + 0}')"
  case "$kb" in
    ''|*[!0-9]*) echo "ERR du 输出非数值：$first"; return 1 ;;
  esac
  echo "OK $kb"; return 0
}

# 文件枚举（DAV-2239 🟡-1）：用 `find` 并按退出码判成败。
# 为什么不用 zsh 的 `(N)` 通配：目录「存在但不可读/不可进入」时通配静默返回 0 个匹配，
# stderr 与退出状态都不报错，于是预算项被按 0 计入、可能把热备行判成绿；
# `find` 在此情形返回非零并给出 `Permission denied`，据此记「不可测」（fail-closed）。
# `find` 同时会在递归中枚举 `.` 开头的路径段（通配默认不匹配），隐藏副本不会被漏掉。
# 成功：把匹配写入全局 ENUM_FILES（可能 0 个），返回 0；失败：把首行原因写入全局 ENUM_ERR，返回 1。
# 注意：**不可用 `$(enumerate_files …)` 调用**（命令替换是子 shell，ENUM_FILES 会丢失）——
#       必须直接调用后取 `$?`，从 ENUM_FILES / ENUM_ERR 读结果。
# $3 = "1" 只枚举本层（`-maxdepth 1`，用于 `~/ta-snapshot/*.db.zst`）；否则递归。
ENUM_FILES=(); ENUM_ERR=""
enumerate_files() {
  local root="$1" pat="$2" shallow="${3:-0}" out rc f
  ENUM_FILES=(); ENUM_ERR=""
  if [ "$shallow" = "1" ]; then
    out="$(find "$root" -maxdepth 1 -name "$pat" -type f -print 2>&1)"; rc=$?
  else
    out="$(find "$root" -name "$pat" -type f -print 2>&1)"; rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    ENUM_ERR="${out%%$'\n'*}"; return 1
  fi
  for f in ${(f)out}; do
    [ -n "$f" ] && ENUM_FILES+=( "$f" )
  done
  return 0
}

# 路径三态（DAV-2239 🟡-1 收口）：present / absent / unknown。
# `[ -e ]` 失败并不等于「不存在」：上级目录不可读/不可进入时 stat 返回 EACCES，
# 此时必须按 unknown 处理（记不可测），否则整棵看不见的目录树会被静默按 0 计入并可能判绿。
path_state() {
  local p="$1" anc up
  [ -e "$p" ] && { echo present; return 0; }
  anc="$(dirname "$p")"
  while [ ! -e "$anc" ]; do
    up="$(dirname "$anc")"
    [ "$up" = "$anc" ] && { echo unknown; return 0; }
    anc="$up"
  done
  if [ -r "$anc" ] && [ -x "$anc" ]; then echo absent; else echo unknown; fi
}

daily_report() {
  local avail_gib light res rc
  local bak_kb="" zst_kb="" pre_kb="" proj_kb="" cur_kb=""
  local budget_kb=0 unable=0 unable_lines="" rel_bak="$REL/backups"
  echo "## 存储健康巡检 I — $NOW"
  echo
  avail_gib=$(df -g /System/Volumes/Data | awk 'NR==2{print $4}')
  if   [ "$avail_gib" -ge 100 ]; then light="绿"
  elif [ "$avail_gib" -ge 60 ]; then light="黄"
  else light="红"; fi
  echo "**磁盘水位：$light** — $(df -h /System/Volumes/Data | awk 'NR==2{printf "容量 %s，已用 %s，可用 %s（%s）",$2,$3,$4,$5}')"
  echo
  # ---- 热备预算（DAV-2238，按 DAV-2237 §10 签字口径）----------------------
  # 计入（三项单列）：backups/ + ~/ta-snapshot/*.db.zst + 发布期 .pre-slim 类副本；
  # 不计入：活库 data/tradingagents.db、~/ta-snapshot/current.db（未压缩工作副本）、
  #         ~/ta-work/dav-2013/ 实验副本（脚本不扫 ta-work）。
  # ① 发布/回滚备份 backups/
  local bak_state bak_absent=0
  bak_state="$(path_state "$rel_bak")"
  case "$bak_state" in
    present)
      res="$(du_kb "$rel_bak")"; rc=$?
      if [ "$rc" -eq 0 ]; then bak_kb="${res#OK }"; else
        unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$rel_bak — ${res#ERR }"$'\n'
      fi ;;
    absent) bak_absent=1 ;;
    *)
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$rel_bak（上级目录不可读/不可进入，路径存在性无法判断）"$'\n' ;;
  esac
  # ② 共享快照基线 *.db.zst（current.db 是未压缩工作副本，不计入）
  #    三态：目录不存在 → 跳过（不算不可测）；可读 → 枚举（可为 0 个文件）；
  #          存在但不可读/枚举失败 → 不可测（DAV-2239 🟡-1）
  local -a zst_files=(); local zst_ok=1 snap_state
  snap_state="$(path_state "$SNAP_DIR")"
  if [ "$snap_state" = "present" ]; then
    enumerate_files "$SNAP_DIR" '*.db.zst' 1; rc=$?
    if [ "$rc" -ne 0 ]; then
      zst_ok=0
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$SNAP_DIR/*.db.zst（存在但不可读/不可进入，枚举失败）— ${ENUM_ERR:-未知原因}"$'\n'
    else
      zst_files=( "${ENUM_FILES[@]}" )
      if [ "${#zst_files}" -gt 0 ]; then
        res="$(du_kb_total "${zst_files[@]}")"; rc=$?
        if [ "$rc" -eq 0 ]; then zst_kb="${res#OK }"; else
          unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$SNAP_DIR/*.db.zst（${#zst_files} 个文件）— ${res#ERR }"$'\n'
        fi
      fi
    fi
  elif [ "$snap_state" = "unknown" ]; then
    zst_ok=0
    unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$SNAP_DIR/*.db.zst（上级目录不可读/不可进入，路径存在性无法判断）"$'\n'
  fi
  # ③ 发布期 .pre-slim 类副本（只扫 PROJ/REL/SNAP；ta-work 实验副本不在口径内）
  #    与②同一三态：不存在→跳过；可读→枚举；不可读/存在性未知→不可测
  local -a pre_files=(); local root pre_ok=1 root_state
  for root in "$PROJ" "$REL" "$SNAP_DIR"; do
    root_state="$(path_state "$root")"
    if [ "$root_state" = "absent" ]; then continue; fi
    if [ "$root_state" = "unknown" ]; then
      pre_ok=0
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$root 下的 .pre-slim 副本（上级目录不可读/不可进入，路径存在性无法判断）"$'\n'
      continue
    fi
    enumerate_files "$root" '*.pre-slim*' 0; rc=$?
    if [ "$rc" -ne 0 ]; then
      pre_ok=0
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$root 下的 .pre-slim 副本（存在但不可读/不可进入，枚举失败）— ${ENUM_ERR:-未知原因}"$'\n'
    else
      pre_files+=( "${ENUM_FILES[@]}" )
    fi
  done
  if [ "${#pre_files}" -gt 0 ]; then
    res="$(du_kb_total "${pre_files[@]}")"; rc=$?
    if [ "$rc" -eq 0 ]; then pre_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：.pre-slim 副本（${#pre_files} 个文件）— ${res#ERR }"$'\n'
    fi
  fi
  # 非预算项（只列示，不进合计）：工作树/活库、快照目录里的未压缩 current.db
  local proj_state cur_state
  proj_state="$(path_state "$PROJ")"
  if [ "$proj_state" = "present" ]; then
    res="$(du_kb "$PROJ")"; rc=$?
    if [ "$rc" -eq 0 ]; then proj_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$PROJ — ${res#ERR }"$'\n'
    fi
  elif [ "$proj_state" = "unknown" ]; then
    unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$PROJ（上级目录不可读/不可进入，路径存在性无法判断）"$'\n'
  fi
  cur_state="$(path_state "$SNAP_DIR/current.db")"
  if [ "$cur_state" = "present" ]; then
    res="$(du_kb "$SNAP_DIR/current.db")"; rc=$?
    if [ "$rc" -eq 0 ]; then cur_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$SNAP_DIR/current.db — ${res#ERR }"$'\n'
    fi
  elif [ "$cur_state" = "unknown" ]; then
    unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$SNAP_DIR/current.db（所在目录不可读/不可进入，路径存在性无法判断）"$'\n'
  fi
  budget_kb=$(( ${bak_kb:-0} + ${zst_kb:-0} + ${pre_kb:-0} ))
  # 🟡-2 精确 KiB 判预算（15 GiB = 15728640 KiB），(15,16) GiB 窗口必须判红
  # DAV-2238：任何测量项不可测 ⇒ 热备行判「红」（fail-closed），不再只报「未知」
  # DAV-2239 🟢-1：已测得部分和就超预算时优先出「红（超预算）」——部分和只会低估真实合计，
  #                该判定可靠；「另有 N 项不可测」附在同一条里，不再让诊断串被盖掉
  if [ "$budget_kb" -gt 15728640 ]; then
    echo "**热备副本占用：红（超预算）** — backups + 快照 *.db.zst + .pre-slim 合计 $(fmt_gib "$budget_kb") GiB（${budget_kb} KiB；预算 ≤15 GiB = 15728640 KiB）${unable:+；另有 ${unable} 个测量项不可测，合计不完整（只多不少）}"
  elif [ "$unable" -gt 0 ]; then
    echo "**热备副本占用：红（不可测，fail-closed）** — 有 ${unable} 个测量项不可测，预算判定不成立；已测得 ${budget_kb} KiB（不完整；预算 ≤15 GiB = 15728640 KiB）"
  else
    echo "**热备副本占用：绿** — backups + 快照 *.db.zst + .pre-slim 合计 $(fmt_gib "$budget_kb") GiB（${budget_kb} KiB；预算 ≤15 GiB = 15728640 KiB）"
  fi
  # 逐项明细：预算项三项单列（DAV-2238），非预算项另行注明
  # （枚举失败时不出「N 个文件」行，以免把「不可测」读成「空」——DAV-2239 🟡-1）
  if [ -n "$bak_kb" ]; then
    echo "  - 计入预算①发布/回滚备份 \`backups/\`：$(fmt_gib "$bak_kb") GiB（${bak_kb} KiB）  $rel_bak"
  elif [ "$bak_absent" = "1" ]; then
    echo "  - 计入预算①发布/回滚备份 \`backups/\`：目录不存在  $rel_bak"
  fi
  if [ "${#zst_files}" -gt 0 ] && [ -n "$zst_kb" ]; then
    echo "  - 计入预算②共享快照基线 \`*.db.zst\`（${#zst_files} 个文件）：$(fmt_gib "$zst_kb") GiB（${zst_kb} KiB）  $SNAP_DIR"
  elif [ "$zst_ok" = "1" ] && [ "${#zst_files}" -eq 0 ]; then
    echo "  - 计入预算②共享快照基线 \`*.db.zst\`：0 个文件  $SNAP_DIR"
  fi
  if [ "${#pre_files}" -gt 0 ] && [ -n "$pre_kb" ]; then
    echo "  - 计入预算③发布期 \`.pre-slim\` 副本（${#pre_files} 个文件）：$(fmt_gib "$pre_kb") GiB（${pre_kb} KiB）  $PROJ $REL $SNAP_DIR"
  elif [ "$pre_ok" = "1" ] && [ "${#pre_files}" -eq 0 ]; then
    echo "  - 计入预算③发布期 \`.pre-slim\` 副本：0 个文件  $PROJ $REL $SNAP_DIR"
  fi
  if [ -n "$proj_kb" ]; then echo "  - 不计入预算：工作树/活库（含 data/tradingagents.db）：$(fmt_gib "$proj_kb") GiB  $PROJ"; fi
  if [ -n "$cur_kb" ]; then echo "  - 不计入预算：快照目录未压缩副本 current.db（归 D-073 整库副本上限管）：$(fmt_gib "$cur_kb") GiB  $SNAP_DIR/current.db"; fi
  printf '%s' "$unable_lines"
  echo
  echo "**无主/大文件清单（≥200 MiB）**"
  # DAV-2239 🟢-3：find 报错（目录不可读）不能静默成「空清单」——错误原因归上方「不可测」清单，
  #                这里至少留一行「清单不完整」；仍不改变灯色与退出码（该清单不驱动判定）
  local bf_rc=0 bf_out=""
  local -a bf_roots=()
  for root in "$PROJ" "$REL" "$SNAP_DIR"; do [ -d "$root" ] && bf_roots+=( "$root" ); done
  if [ "${#bf_roots}" -gt 0 ]; then
    bf_out="$(find "${bf_roots[@]}" -type f -size +200M -not -path "*/.git/*" -exec du -h {} \; 2>/dev/null)"; bf_rc=$?
  fi
  if [ -n "$bf_out" ]; then
    printf '%s\n' "$bf_out" | sort -rh | while read -r sz p; do
      case "$p" in
        *data/tradingagents.db*)   cause="活库，不处理（不计入热备预算）" ;;
        *releases/backups/*)       cause="发布/回滚备份（计入热备预算）" ;;
        *ta-snapshot/*.db.zst)     cause="DAV-1528 共享快照基线（保留3份，计入热备预算）" ;;
        *ta-snapshot/current.db)   cause="DAV-1528 解压只读副本（不计入热备预算，归整库副本上限）" ;;
        *.pre-slim*)               cause="发布期 .pre-slim 副本（计入热备预算）" ;;
        *logs/*|*.log)             cause="日志" ;;
        *.venv*|*site-packages*)   cause="依赖产物" ;;
        *)                         cause="待人工判断" ;;
      esac
      echo "- $sz  \`$p\` — $cause"
    done
  fi
  if [ "$bf_rc" -ne 0 ]; then
    echo "> ⚠️ 大文件清单不完整：find 未能遍历全部目录（rc=$bf_rc），不可读对象的报错见上方「不可测」清单。"
  fi
  echo
  # DAV-2238：只要有测量项不可测就不算「巡检通过」，热备行判红 + 退出码 2，绝不按 0 判绿
  if [ "$unable" -gt 0 ]; then
    echo "> ⚠️ 有 ${unable} 个测量项不可测：占用合计不完整，热备行按「红（fail-closed）」处理，本次退出码 2（不得据此判绿）。"
    return 2
  fi
  return 0
}

weekly_baseline() {
  echo "## 每周基线（上传当周最后一份 DAV-1528 快照）— $NOW"
  echo
  # 取日期最大的一份（=最后一份快照）；兼容周一/周二跑时本周尚未产新快照的情况
  echo "- 快照目录：\`$SNAP_DIR\`（DAV-1528 产出，本地保留最近 3 份）"
  echo "- 选择口径：全部 tradingagents-*.db.zst 中日期最大的一份"
  if [ ! -d "$SNAP_DIR" ]; then
    echo "- ⚠️ 快照目录不存在，本周无可上传基线"; echo; return 0
  fi
  latest=""
  for f in "$SNAP_DIR"/tradingagents-*.db.zst(N); do
    fname=$(basename "$f")
    fdate=$(echo "$fname" | sed -E 's/tradingagents-([0-9]{4}-[0-9]{2}-[0-9]{2})\.db\.zst/\1/')
    [ "$fdate" = "$fname" ] && continue
    [ -z "$latest" ] && { latest="$f"; continue; }
    ldate=$(basename "$latest" | sed -E 's/tradingagents-([0-9]{4}-[0-9]{2}-[0-9]{2})\.db\.zst/\1/')
    [[ "$fdate" > "$ldate" ]] && latest="$f"
  done
  if [ -z "$latest" ]; then
    echo "- ⚠️ 当周窗口内无 tradingagents-*.db.zst，本周无可上传基线"; echo; return 0
  fi
  sha=$(shasum -a 256 "$latest" | awk '{print $1}')
  sz=$(du -h "$latest" | awk '{print $1}')
  bytes=$(stat -f %z "$latest")
  echo "- 当周最后一份：\`$latest\`"
  echo "- 大小：$sz（$bytes 字节）｜SHA256：$sha"
  echo "- 上传目标：夸克 \`$QUARK_REMOTE_DIR\`（保留最近 2 份）"
  # 上传动作：调用方环境已装 quark 工具时执行；否则在报告中提示人工上传。
  if command -v quark >/dev/null 2>&1; then
    quark upload "$latest" "$QUARK_REMOTE_DIR/" && echo "- 上传：完成（远端回读请人工核对文件名/大小/SHA256）" || echo "- ⚠️ 上传命令返回非零，请人工处理"
  else
    echo "- 上传：本机无 quark CLI，请人工上传后核对文件名/大小/SHA256 前三项"
  fi
  echo "- 云端超限清理：脚本不动云端；若远端已超 2 份，请人工删除最旧份"
  echo
}

exit_rc=0
case "$MODE" in
  daily)  daily_report; exit_rc=$? ;;
  weekly) weekly_baseline ;;
  auto)
    daily_report; exit_rc=$?
    if [ "$DOW" = "3" ]; then weekly_baseline; fi
    ;;
esac
echo "> 只读纪律：df/du/find/ls/shasum；脚本不含任何本地或云端删除逻辑。"
exit "$exit_rc"
