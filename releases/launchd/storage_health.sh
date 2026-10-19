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
  if [ -d "$rel_bak" ]; then
    res="$(du_kb "$rel_bak")"; rc=$?
    if [ "$rc" -eq 0 ]; then bak_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$rel_bak — ${res#ERR }"$'\n'
    fi
  fi
  # ② 共享快照基线 *.db.zst（current.db 是未压缩工作副本，不计入）
  local -a zst_files=( "$SNAP_DIR"/*.db.zst(N) )
  if [ "${#zst_files}" -gt 0 ]; then
    res="$(du_kb_total "${zst_files[@]}")"; rc=$?
    if [ "$rc" -eq 0 ]; then zst_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$SNAP_DIR/*.db.zst（${#zst_files} 个文件）— ${res#ERR }"$'\n'
    fi
  fi
  # ③ 发布期 .pre-slim 类副本（只扫 PROJ/REL/SNAP；ta-work 实验副本不在口径内）
  local -a pre_files=(); local root
  for root in "$PROJ" "$REL" "$SNAP_DIR"; do
    [ -e "$root" ] || continue
    pre_files+=( "$root"/**/*.pre-slim*(N) )
  done
  if [ "${#pre_files}" -gt 0 ]; then
    res="$(du_kb_total "${pre_files[@]}")"; rc=$?
    if [ "$rc" -eq 0 ]; then pre_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：.pre-slim 副本（${#pre_files} 个文件）— ${res#ERR }"$'\n'
    fi
  fi
  # 非预算项（只列示，不进合计）：工作树/活库、快照目录里的未压缩 current.db
  if [ -d "$PROJ" ]; then
    res="$(du_kb "$PROJ")"; rc=$?
    if [ "$rc" -eq 0 ]; then proj_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$PROJ — ${res#ERR }"$'\n'
    fi
  fi
  if [ -f "$SNAP_DIR/current.db" ]; then
    res="$(du_kb "$SNAP_DIR/current.db")"; rc=$?
    if [ "$rc" -eq 0 ]; then cur_kb="${res#OK }"; else
      unable=$((unable + 1)); unable_lines="${unable_lines}  - ⚠️ 不可测：$SNAP_DIR/current.db — ${res#ERR }"$'\n'
    fi
  fi
  budget_kb=$(( ${bak_kb:-0} + ${zst_kb:-0} + ${pre_kb:-0} ))
  # 🟡-2 精确 KiB 判预算（15 GiB = 15728640 KiB），(15,16) GiB 窗口必须判红
  # DAV-2238：任何测量项不可测 ⇒ 热备行判「红」（fail-closed），不再只报「未知」
  if [ "$unable" -gt 0 ]; then
    echo "**热备副本占用：红（不可测，fail-closed）** — 有 ${unable} 个测量项不可测，预算判定不成立；已测得 ${budget_kb} KiB（不完整；预算 ≤15 GiB = 15728640 KiB）"
  elif [ "$budget_kb" -le 15728640 ]; then
    echo "**热备副本占用：绿** — backups + 快照 *.db.zst + .pre-slim 合计 $(fmt_gib "$budget_kb") GiB（${budget_kb} KiB；预算 ≤15 GiB = 15728640 KiB）"
  else
    echo "**热备副本占用：红（超预算）** — backups + 快照 *.db.zst + .pre-slim 合计 $(fmt_gib "$budget_kb") GiB（${budget_kb} KiB；预算 ≤15 GiB = 15728640 KiB）"
  fi
  # 逐项明细：预算项三项单列（DAV-2238），非预算项另行注明
  if [ -n "$bak_kb" ]; then
    echo "  - 计入预算①发布/回滚备份 \`backups/\`：$(fmt_gib "$bak_kb") GiB（${bak_kb} KiB）  $rel_bak"
  elif [ ! -d "$rel_bak" ]; then
    echo "  - 计入预算①发布/回滚备份 \`backups/\`：目录不存在  $rel_bak"
  fi
  if [ "${#zst_files}" -gt 0 ]; then
    [ -n "$zst_kb" ] && echo "  - 计入预算②共享快照基线 \`*.db.zst\`（${#zst_files} 个文件）：$(fmt_gib "$zst_kb") GiB（${zst_kb} KiB）  $SNAP_DIR"
  else
    echo "  - 计入预算②共享快照基线 \`*.db.zst\`：0 个文件  $SNAP_DIR"
  fi
  if [ "${#pre_files}" -gt 0 ]; then
    [ -n "$pre_kb" ] && echo "  - 计入预算③发布期 \`.pre-slim\` 副本（${#pre_files} 个文件）：$(fmt_gib "$pre_kb") GiB（${pre_kb} KiB）  $PROJ $REL $SNAP_DIR"
  else
    echo "  - 计入预算③发布期 \`.pre-slim\` 副本：0 个文件  $PROJ $REL $SNAP_DIR"
  fi
  if [ -n "$proj_kb" ]; then echo "  - 不计入预算：工作树/活库（含 data/tradingagents.db）：$(fmt_gib "$proj_kb") GiB  $PROJ"; fi
  if [ -n "$cur_kb" ]; then echo "  - 不计入预算：快照目录未压缩副本 current.db（归 D-073 整库副本上限管）：$(fmt_gib "$cur_kb") GiB  $SNAP_DIR/current.db"; fi
  printf '%s' "$unable_lines"
  echo
  echo "**无主/大文件清单（≥200 MiB）**"
  find "$PROJ" "$REL" "$SNAP_DIR" -type f -size +200M -not -path "*/.git/*" -exec du -h {} \; 2>/dev/null | sort -rh | while read -r sz p; do
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
