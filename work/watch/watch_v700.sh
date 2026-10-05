#!/bin/bash
# watch_v700 — launchd 定时驱动的事件守望（DAV-1509）
#
# 与 v600 的差异：
#   * v600 是会话内后台 while 循环：poll→事件→写 alerts→退出→靠会话/人重新布防。
#   * v700 由 launchd 每 POLL 秒拉起一次，单轮检测后必退出；不依赖任何聊天会话常开。
#   * 检出事件时不再等总工处理，而是把事件负载写入 $PAYLOAD_DIR/latest.json，
#     并 `multica autopilot trigger` 一次性唤起一个 run_only 智能体（无历史）去读
#     负载并处理。处理结果与后续动作由该智能体在 issue 内自行交付。
#
# 事件范围与 v600 相同（总控 10-05 令）：
#   1) 新卡（issue number > 已见最大）
#   2) 受监控卡的成员评论
#   3) 受监控卡的 run 进入终态 / URGENT 级新 run（守卫卡被未预期唤起）
#   4) 主干 tip 与已签清单不符（含 NON-FF 改写）
#   5) prod 底线：进程消失且 healthz 不可达、healthz commit_sha 偏离、真崩溃 Traceback
#   6) 无卡批量分析突增（reports 20min 内非固定测量账户 >=4 档，D-072）
#
# 沿用防抖：CLI 拉空不算事件、不覆盖种子；0-run 卡跳过 empty 检测；
#           runid 用 rid:status:agent 三元组 diff（前缀碰撞安全）。
#
# 只检测与派发；不合并、不部署、不改库、不改模型通道。

REPO="/Users/davidliu/Documents/TradingAgents-AShare"
REL="/Users/davidliu/Documents/TradingAgents-AShare-releases"
LOG="$REL/logs/uvicorn-c170334f.log"
REMOTE_URL="https://github.com/Guuuu233/tradingagents-ashare-fork.git"
TRUNK_BR="codex/dav-4-p2a-trunk"
EXPECT_PROD_SHA="c170334f0f41909efd840f3efd76dd0d37d7558f"
BASELINE="db9c69099b9bb04f6cfbdd5aca076a86839a26f1"
PRODDB="$REPO/data/tradingagents.db"
export HOME=/Users/davidliu

# ---- 可配置（环境变量覆盖；默认与 v600 一致） ----
POLL=${WATCH_POLL:-120}
# 一次性智能体：Multica autopilot（run_only，无聊天历史）。
# 由 bootstrap 脚本 `work/watch/bootstrap_autopilot.sh` 创建后把 id 写入 $AUTOPILOT_ID_FILE；
# 也可直接 export WATCH_AUTOPILOT_ID=<uuid>。
AUTOPILOT_ID_FILE=${WATCH_AUTOPILOT_ID_FILE:-/tmp/watch_v700.autopilot_id}
AUTOPILOT_ID=${WATCH_AUTOPILOT_ID:-$(cat "$AUTOPILOT_ID_FILE" 2>/dev/null)}
# 派发节流：同一 payload 未消费不重复 trigger；两次 trigger 至少间隔 N 秒。
DISPATCH_MIN_INTERVAL=${WATCH_DISPATCH_MIN_INTERVAL:-300}

# ---- 状态文件 ----
STATE=${WATCH_STATE:-/tmp/watch_v700.state}
ALERTS=${WATCH_ALERTS:-/tmp/watch_v700.alerts}
CARDSTATE=${WATCH_CARDSTATE:-/tmp/watch_v700.cards}
SEEN_COMMENTS=${WATCH_SEEN_COMMENTS:-/tmp/watch_v700.comments}
HIGHNUM=${WATCH_HIGHNUM:-/tmp/watch_v700.highnum}
PAYLOAD_DIR=${WATCH_PAYLOAD_DIR:-/tmp/watch_v700_payload}
LOCKFILE=${WATCH_LOCKFILE:-/tmp/watch_v700.lock}
DISPATCH_LOG=${WATCH_DISPATCH_LOG:-/tmp/watch_v700.dispatch}
mkdir -p "$PAYLOAD_DIR"

# ---- 已签主干清单（与 v600 相同；新签名追加到行尾即可） ----
SIGNED="48f2f9b95b26b7c8e6516c8a37a4d67b918fbfc0 c679c3c7e522109d068755996c652bf04cd53007 cf7bda36 c170334f0f41909efd840f3efd76dd0d37d7558f 3596a66655dfa329de7e82daa1f3faf3170b8a1d 126cc9565ba5b4c7dac4de dab8cef5678cf9d67e3ff315e7ee0cadc8e2bbf6 d2c8ac9e32bb93d932231b5adf532e82f84d265c 9d61dd1a6d0cc678ed40eb54e8a80028a8df5fef 428b416f57c720691b3ed1c1730099a7c27b7272 7b3fc9c15b5a241e5e77187a96c2d4a8a044401c 6c13b9a6d1c836d58057df2f306f14fa3448f107 2a53a9485eb6a2ffe6431a0dec690c0f350eec9b 6a7138149ff499f70197917f32ac94c5870c9cd1 1329dae58d61df0199da06033be90d94bc9eeee2 4926fb56 65988c79 ae677a8e 7da35a7 7da35a7ca8dbf131b9d29a837d2c5c39015755f0 2e1e199fc368fcbdd1460eb1c3a851220d293599 db9c69099b9bb04f6cfbdd5aca076a86839a26f1 ae677a8efc40ba59d7024c96e43024f95f24abc9"
CARDS="1440 1432 1429 1441 1443 1444 1445 1446 1447 1448 1449 1450 1451 1453 1454 1455 1456 1458 1459 1460 1462 1466 1474 1475 1477 1478 1479 1480 1481 1482 1484 1463 1472 1476"
GUARD_ANY="1450"
GUARD_BACKLOG="1478:资深开发2"
GUARD_REVIEW="1448 1456 1484"
REVIEW_OK_SHA=""
REVIEWER="代码审核员"
TERMINAL="completed failed cancelled"

# 本机无 GNU timeout，用 perl alarm 包裹 CLI（防挂死）
mx(){ perl -e 'alarm 90; exec @ARGV' "$@"; }
alert(){ echo "[WATCH $(date '+%m-%d %H:%M:%S')] $*" >> "$ALERTS"; }
event(){ echo "[WATCH-EVENT $(date '+%m-%d %H:%M:%S')] $*"; alert "$*"; }
remote_tip(){ mx git ls-remote "$REMOTE_URL" "$TRUNK_BR" 2>/dev/null | awk '{print $1}'; }

runs_line(){ mx multica issue runs "DAV-$1" 2>/dev/null | awk 'NR>1 && $1 ~ /^[0-9a-f]/ {print $1":"$3":"$2}' | tr '\n' ','; }
is_terminal(){ case " $TERMINAL " in *" $1 "*) return 0;; esac; return 1; }
is_urgent_newrun(){
  _c=$1; _rst=$2; _rag=$3
  [ "$_rst" = "running" ] || [ "$_rst" = "queued" ] || return 1
  case " $GUARD_ANY " in *" $_c "*) return 0;; esac
  for gb in $GUARD_BACKLOG; do
    gbc=${gb%%:*}; gba=${gb##*:}
    [ "$_c" = "$gbc" ] && [ "$_rag" = "$gba" ] && return 0
  done
  case " $GUARD_REVIEW " in *" $_c "*)
    if [ "$_rag" = "$REVIEWER" ]; then
      [ "$_c" = "1456" ] && [ -n "$REVIEW_OK_SHA" ] && return 1
      [ "$_c" = "1484" ] && return 1
      return 0
    fi ;;
  esac
  return 1
}

cd "$REPO" || exit 1

# ---- 单实例互斥：launchd 每 POLL 秒拉起；上一轮未结束时本轮直接退 ----
# macOS 无 flock；用 PID 文件 + 存活检测做互斥（PID 复用概率对本场景可接受）。
if [ -f "$LOCKFILE" ]; then
  OLDPID=$(cat "$LOCKFILE" 2>/dev/null)
  if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
    alert "skip: previous tick still running (pid $OLDPID)"
    exit 0
  fi
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE" "$EVENTS_FILE"' EXIT

# ---- 布防：种子缺失才初始化（首轮吸收现状，不算事件） ----
if [ ! -f "$SEEN_COMMENTS" ] || [ ! -f "$HIGHNUM" ]; then
  touch "$SEEN_COMMENTS"
  for c in $CARDS; do
    mx multica issue comment list "DAV-$c" --recent 10 --summary --output json 2>/dev/null | \
      python3 -c "import json,sys; [print(x.get('id','')) for x in (json.load(sys.stdin) if True else [])]" 2>/dev/null | \
      grep -E '^[0-9a-f-]{36}$' >> "$SEEN_COMMENTS" 2>/dev/null
  done
  sort -u "$SEEN_COMMENTS" -o "$SEEN_COMMENTS"
  HN=$(mx multica issue list --sort created_at --direction desc --limit 1 --output json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('issues') or [{}])[0].get('number',0))" 2>/dev/null)
  echo "${HN:-0}" > "$HIGHNUM"
fi
[ -f "$CARDSTATE" ] || { touch "$CARDSTATE"; for c in $CARDS; do echo "$c=$(runs_line $c)" >> "$CARDSTATE"; done; }
[ -f "$STATE" ] || { TIP=$(remote_tip); echo "${TIP:-$BASELINE}" > "$STATE"; }
BASE_ERR=0
[ -f "$LOG" ] && BASE_ERR=$(awk '
  /Traceback \(most recent call last\)/ {tb=NR; blk=""}
  tb && NR>tb && NR<=tb+60 {blk=blk"\n"$0}
  tb && (NR==tb+60 || /Error:|Exception/){
    if (blk ~ /DebateProtocolError|ValidationError|fail-closed|debate_utils/) istb=0; else istb=1
    crash+=istb; tb=0
  }
  /CRITICAL|FATAL/ {crash++}
  END{print crash+0}' "$LOG")
alert "armed-v700 launchd tick poll=${POLL}s baseline=${BASELINE:0:8} autopilot=${AUTOPILOT_ID:-none}"

# ---- 把检出的事件写成一次性智能体负载并触发 autopilot ----
# $1 = 事件类型标签；$2 = 事件正文（已按出现顺序累积到 EVENTS_FILE）
dispatch(){
  # 无 autopilot id：只记录，不炸。安装后先 bootstrap。
  if [ -z "$AUTOPILOT_ID" ]; then
    alert "NO-AP: autopilot id unset; event(s) logged but no agent dispatched. Run work/watch/bootstrap_autopilot.sh."
    return 0
  fi
  # 节流：距上次 trigger < DISPATCH_MIN_INTERVAL 则只累积 payload，不再 trigger。
  NOW=$(date +%s)
  LAST=$(cat "$DISPATCH_LOG" 2>/dev/null | tail -1 | awk '{print $1}'); LAST=${LAST:-0}
  PAYLOAD="$PAYLOAD_DIR/latest.json"
  python3 - "$PAYLOAD" "$EVENTS_FILE" "$NOW" <<'PY'
import json,sys,os
payload_path, events_path, now = sys.argv[1], sys.argv[2], int(sys.argv[3])
events=[l.rstrip("\n") for l in open(events_path) if l.strip()]
prev={}
if os.path.exists(payload_path):
    try: prev=json.load(open(payload_path))
    except Exception: prev={}
pending=prev.get("events",[]) if not prev.get("consumed",True) else []
data={"generated_at":now,"consumed":False,"events":pending+events}
json.dump(data,open(payload_path,"w"),ensure_ascii=False,indent=2)
PY
  if [ $((NOW-LAST)) -lt "$DISPATCH_MIN_INTERVAL" ]; then
    alert "dispatch-throttled: ${#EVENTS_FILE_HITS:-1} event(s) appended to payload; last trigger $((NOW-LAST))s ago (<${DISPATCH_MIN_INTERVAL}s)"
    return 0
  fi
  # 触发一次性智能体。它自行读 $PAYLOAD_DIR/latest.json → 处理 → 在 issue 交付。
  TRIG=$(mx multica autopilot trigger "$AUTOPILOT_ID" --output json 2>/dev/null)
  RC=$?
  echo "$NOW rc=$RC $TRIG" >> "$DISPATCH_LOG"
  if [ $RC -eq 0 ]; then
    event "dispatched one-shot agent (autopilot $AUTOPILOT_ID) payload=$PAYLOAD"
  else
    alert "DISPATCH-FAIL rc=$RC autopilot=$AUTOPILOT_ID out=${TRIG:0:200}"
  fi
  return 0
}

EVENTS_FILE=$(mktemp /tmp/watch_v700.events.XXXXXX)
EVENTS_FILE_HITS=0
record(){ echo "$*" >> "$EVENTS_FILE"; EVENTS_FILE_HITS=$((EVENTS_FILE_HITS+1)); event "$*"; }

# ---- 单轮检测：任一命中→record()→末尾统一 dispatch；无事件→返回 1 ----
one_round(){
  # 1) 新卡
  HN=$(cat "$HIGHNUM" 2>/dev/null); HN=${HN:-0}
  NEWISSUES=$(mx multica issue list --sort created_at --direction desc --limit 20 --output json 2>/dev/null | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  for i in d.get('issues',[]):
    if (i.get('number') or 0) > $HN:
      print(str(i.get('number'))+'|'+str(i.get('identifier'))+'|'+str(i.get('status'))+'|'+str(i.get('title'))[:80])
except: pass" 2>/dev/null)
  if [ -n "$NEWISSUES" ]; then
    while IFS= read -r l; do [ -n "$l" ] && record "NEW issue: $l"; done <<< "$NEWISSUES"
    mx multica issue list --sort created_at --direction desc --limit 1 --output json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('issues') or [{}])[0].get('number',0))" 2>/dev/null > "$HIGHNUM"
  fi

  # 2) 成员评论（只报 member；已有 id 跳过；CLI 拉空跳过）
  for c in $CARDS; do
    mx multica issue comment list "DAV-$c" --recent 5 --summary --output json 2>/dev/null | python3 -c "
import json,sys
try:
  for x in json.load(sys.stdin):
    if x.get('author_type')=='member' and x.get('id'):
      print(x['id']+'|'+str(x.get('created_at'))+'|'+str(x.get('content'))[:120].replace(chr(10),' '))
except: pass" 2>/dev/null > /tmp/watch_v700.mc.tmp
    if [ -s /tmp/watch_v700.mc.tmp ]; then
      while IFS= read -r l; do
        cid=$(echo "$l" | cut -d'|' -f1)
        grep -qx "$cid" "$SEEN_COMMENTS" 2>/dev/null && continue
        echo "$cid" >> "$SEEN_COMMENTS"
        record "NEW member comment DAV-$c: $l"
      done < /tmp/watch_v700.mc.tmp
    fi
    rm -f /tmp/watch_v700.mc.tmp
  done

  # 3) 运行进入终态 + URGENT 类新 run（CLI 拉空跳过，不覆盖种子）
  for c in $CARDS; do
    NEWLINE=$(runs_line $c)
    [ -z "$NEWLINE" ] && continue
    OLDLINE=$(grep "^$c=" "$CARDSTATE" 2>/dev/null | cut -d= -f2-)
    [ "$NEWLINE" = "$OLDLINE" ] && continue
    for tok in $(echo "$NEWLINE" | tr ',' ' '); do
      rid=$(echo "$tok" | cut -d: -f1); rst=$(echo "$tok" | cut -d: -f2); rag=$(echo "$tok" | cut -d: -f3-)
      oldrst=$(echo "$OLDLINE" | tr ',' '\n' | grep "^$rid:" | cut -d: -f2)
      if [ -z "$oldrst" ]; then
        if is_terminal "$rst"; then record "DAV-$c run terminal (new): $tok"; fi
        if is_urgent_newrun "$c" "$rst" "$rag"; then record "URGENT DAV-$c unexpected NEW run: [$NEWLINE]"; fi
      elif [ "$oldrst" != "$rst" ]; then
        if is_terminal "$rst"; then record "DAV-$c run terminal: $rid $oldrst->$rst($rag)"; fi
      fi
    done
    grep -v "^$c=" "$CARDSTATE" > "$CARDSTATE.tmp" 2>/dev/null; echo "$c=$NEWLINE" >> "$CARDSTATE.tmp"; mv "$CARDSTATE.tmp" "$CARDSTATE"
  done

  # 4) 主干 tip 与已签清单
  NEW=$(remote_tip)
  CUR=$(cat "$STATE" 2>/dev/null)
  if [ -n "$NEW" ] && [ -n "$CUR" ] && [ "$NEW" != "$CUR" ]; then
    if ! git merge-base --is-ancestor "$CUR" "$NEW" 2>/dev/null; then
      record "URGENT trunk NON-FF/rewrite: ${CUR:0:8} -> ${NEW:0:8}"
    fi
    ok=0
    for s in $SIGNED; do
      case "$NEW" in "$s"*) ok=1;; esac
      git merge-base --is-ancestor "$s" "$NEW" 2>/dev/null && ok=1
    done
    SUBJ=$(git log -1 --format='%h %s' "$NEW" 2>/dev/null)
    if [ $ok -eq 1 ]; then record "trunk FF -> $SUBJ (signed-chain ok)"; else record "URGENT trunk moved to UNSIGNED $SUBJ"; fi
    echo "$NEW" > "$STATE"
  fi

  # 5) 生产底线
  HZ=$(curl -s --max-time 15 http://127.0.0.1:8000/healthz 2>/dev/null)
  UVICORN_UP=$(pgrep -f "uvicorn api.main:app" | head -1)
  if [ -z "$HZ" ]; then
    HZ=$(curl -s --max-time 45 http://127.0.0.1:8000/healthz 2>/dev/null)
    if [ -z "$HZ" ] && [ -z "$UVICORN_UP" ]; then
      record "URGENT prod DOWN: uvicorn process gone AND healthz unreachable"
    fi
  else
    PSHA=$(echo "$HZ" | python3 -c "import json,sys;print(json.load(sys.stdin).get('commit_sha',''))" 2>/dev/null)
    if [ "$PSHA" != "$EXPECT_PROD_SHA" ]; then
      record "URGENT prod healthz SHA changed/anomaly: got ${PSHA:-none} expect ${EXPECT_PROD_SHA:0:8}"
    fi
  fi
  if [ -f "$LOG" ]; then
    ERR=$(awk '
      /Traceback \(most recent call last\)/ {tb=NR; blk=""}
      tb && NR>tb && NR<=tb+60 {blk=blk"\n"$0}
      tb && (NR==tb+60 || /Error:|Exception/){
        if (blk ~ /DebateProtocolError|ValidationError|fail-closed|debate_utils/) istb=0; else istb=1
        crash+=istb; tb=0
      }
      /CRITICAL|FATAL/ {crash++}
      END{print crash+0}' "$LOG")
    if [ "$ERR" -gt "$BASE_ERR" ]; then
      record "URGENT prod log new crash/traceback: $ERR (baseline $BASE_ERR)"
    fi
  fi

  # 6) 无卡批量分析突增（D-072；v600 中 PRODDB 未定义、此项实际为死代码——本脚本已修正）
  if [ -f "$PRODDB" ]; then
    SURGE=$(sqlite3 "file:$PRODDB?immutable=1" 2>/dev/null "
      SELECT COALESCE(user_id, 'unknown'), COUNT(*) FROM reports
       WHERE created_at > datetime('now','localtime','-20 minutes')
       GROUP BY 1 HAVING COUNT(*) >= 4 AND COALESCE(user_id,'unknown') != '429163f7-50b6-4982-8bdf-96ae99506843'
    " 2>/dev/null)
    if [ -n "$SURGE" ]; then
      record "URGENT un-carded batch analysis surge (non-daily-account, >=4 in 20min): $SURGE"
    fi
  fi

  [ "$EVENTS_FILE_HITS" -gt 0 ]
}

if one_round; then
  dispatch
  echo "v700 tick: $EVENTS_FILE_HITS event(s) -> dispatched"
else
  echo "v700 tick: no event"
  exit 0
fi
