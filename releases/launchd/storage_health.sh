#!/bin/zsh
# 存储健康巡检 I — 统一脚本 v4（每日巡检 + 每周三基线上传清单）
# 口径（2026-10-13 路径迁移版，快照目录统一 ~/ta-snapshot）：
#   每日：日期 + 水位三色(>=100GiB绿/60-100GiB黄只记录/<60GiB红立即唤醒并停止建整库副本，df -g) + 热备<=15GiB + 无主大文件清单
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

daily_report() {
  echo "## 存储健康巡检 I — $NOW"
  echo
  avail_gib=$(df -g /System/Volumes/Data | awk 'NR==2{print $4}')
  if   [ "$avail_gib" -ge 100 ]; then light="绿"
  elif [ "$avail_gib" -ge 60 ]; then light="黄"
  else light="红"; fi
  echo "**磁盘水位：$light** — $(df -h /System/Volumes/Data | awk 'NR==2{printf "容量 %s，已用 %s，可用 %s（%s）",$2,$3,$4,$5}')"
  echo
  total_kb=0
  for d in "$PROJ" "$REL" "$SNAP_DIR"; do
    [ -d "$d" ] || continue
    kb=$(du -sk "$d" 2>/dev/null | awk '{print $1}')
    total_kb=$((total_kb + kb))
  done
  total_gib=$(( total_kb / 1048576 ))
  [ "$total_gib" -le 15 ] && h="绿" || h="红（超预算）"
  echo "**热备占用：$h** — 合计约 ${total_gib} GiB（预算 ≤15 GiB）"
  for d in "$PROJ" "$REL" "$SNAP_DIR"; do [ -d "$d" ] && echo "  - $(du -sh "$d" 2>/dev/null | awk '{print $1}')  $d"; done
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

case "$MODE" in
  daily)  daily_report ;;
  weekly) weekly_baseline ;;
  auto)
    daily_report
    if [ "$DOW" = "3" ]; then weekly_baseline; fi
    ;;
esac
echo "> 只读纪律：df/du/find/ls/shasum；脚本不含任何本地或云端删除逻辑。"
