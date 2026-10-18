#!/bin/zsh
# 存储健康巡检 I — 统一脚本 v5（每日巡检 + 每周三基线上传清单）
# 口径（2026-10-13 路径迁移版，快照目录统一 ~/ta-snapshot；DAV-2219 修订）：
#   每日：日期 + 水位三色(>=100GiB绿/60-100GiB黄只记录/<60GiB红立即唤醒并停止建整库副本，df -g)
#         + 热备副本(同级 releases + 快照)<=15GiB（精确 KiB 比较）+ 无主大文件清单
#   DAV-2219（修 DAV-2216 复审 2🟡/2🟢）：
#     🟡-1 du 失败/无输出/非数值 ⇒ 该目录记「不可测」，热备行判「未知」+ 退出码 2，绝不按 0 判绿；
#     🟡-2 预算按 KiB 精确比较（≤15728640 KiB = 15 GiB），不再整数截断；
#     🟢-2 预算只覆盖「副本」（releases + 快照），工作树/活库单列、不计预算（否则含活库长期假红）。
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

daily_report() {
  local avail_gib light d res rc kb proj_kb rel_kb snap_kb
  local budget_kb=0 unable=0 budget_unknown=0 unable_lines=""
  echo "## 存储健康巡检 I — $NOW"
  echo
  avail_gib=$(df -g /System/Volumes/Data | awk 'NR==2{print $4}')
  if   [ "$avail_gib" -ge 100 ]; then light="绿"
  elif [ "$avail_gib" -ge 60 ]; then light="黄"
  else light="红"; fi
  echo "**磁盘水位：$light** — $(df -h /System/Volumes/Data | awk 'NR==2{printf "容量 %s，已用 %s，可用 %s（%s）",$2,$3,$4,$5}')"
  echo
  # 热备预算口径（DAV-2219 🟢-2）：只覆盖「副本」= 同级 releases + 快照目录；
  # PROJ 是工作树/活库所在（data/tradingagents.db 为活库），单列、不计预算。
  proj_kb=""; rel_kb=""; snap_kb=""
  for d in "$PROJ" "$REL" "$SNAP_DIR"; do
    [ -d "$d" ] || continue
    res="$(du_kb "$d")"; rc=$?
    if [ "$rc" -ne 0 ]; then
      unable=$((unable + 1))
      unable_lines="${unable_lines}  - ⚠️ 不可测：$d — ${res#ERR }"$'\n'
      [ "$d" = "$PROJ" ] || budget_unknown=1
      continue
    fi
    kb="${res#OK }"
    if   [ "$d" = "$PROJ" ]; then proj_kb="$kb"
    elif [ "$d" = "$REL" ]; then rel_kb="$kb"
    else snap_kb="$kb"; fi
  done
  budget_kb=$(( ${rel_kb:-0} + ${snap_kb:-0} ))
  # 🟡-2：精确 KiB 判预算（15 GiB = 15728640 KiB），(15,16) GiB 窗口必须判红
  if [ "$budget_unknown" -eq 1 ]; then
    echo "**热备副本占用：未知 ⚠️** — 有目录不可测，预算判定不成立（不判绿）；已测得 ${budget_kb} KiB（不完整）"
  elif [ "$budget_kb" -le 15728640 ]; then
    echo "**热备副本占用：绿** — releases + 快照合计 $(fmt_gib "$budget_kb") GiB（${budget_kb} KiB；预算 ≤15 GiB = 15728640 KiB）"
  else
    echo "**热备副本占用：红（超预算）** — releases + 快照合计 $(fmt_gib "$budget_kb") GiB（${budget_kb} KiB；预算 ≤15 GiB = 15728640 KiB）"
  fi
  if [ -n "$proj_kb" ]; then echo "  - 工作树/活库（含 data/tradingagents.db，不计入热备预算）：$(fmt_gib "$proj_kb") GiB  $PROJ"; fi
  if [ -n "$rel_kb" ];  then echo "  - 发布/回滚副本：$(fmt_gib "$rel_kb") GiB  $REL"; fi
  if [ -n "$snap_kb" ]; then echo "  - 共享快照：$(fmt_gib "$snap_kb") GiB  $SNAP_DIR"; fi
  printf '%s' "$unable_lines"
  echo
  echo "**无主/大文件清单（≥200 MiB）**"
  find "$PROJ" "$REL" "$SNAP_DIR" -type f -size +200M -not -path "*/.git/*" -exec du -h {} \; 2>/dev/null | sort -rh | while read -r sz p; do
    case "$p" in
      *data/tradingagents.db*)   cause="活库，不处理" ;;
      *releases/backups/*)       cause="发布/回滚备份" ;;
      *ta-snapshot/*.db.zst)     cause="DAV-1528 共享快照（保留3份）" ;;
      *ta-snapshot/current.db)   cause="DAV-1528 解压只读副本，计入副本上限" ;;
      *logs/*|*.log)             cause="日志" ;;
      *.venv*|*site-packages*)   cause="依赖产物" ;;
      *)                         cause="待人工判断" ;;
    esac
    echo "- $sz  \`$p\` — $cause"
  done
  echo
  # 🟡-1：只要有目录不可测就不算「巡检通过」，用专门状态 + 退出码 2 让调用方/日志看到
  if [ "$unable" -gt 0 ]; then
    echo "> ⚠️ 有 ${unable} 个目录不可测：占用合计不完整，热备行按「未知」处理，本次退出码 2（不得据此判绿）。"
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
