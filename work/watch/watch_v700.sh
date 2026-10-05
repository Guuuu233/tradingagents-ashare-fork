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
# v700-r2（DAV-1515 复审修订）：
#   * A1 崩溃计数基线落盘 ERRSTATE（布防守卫内吸现状），之后跨 tick 比较、告警时抬线——
#     原实现每轮重算全量基线，ERR 恒等于基线，真崩溃永不上报。
#   * A2 DISPATCH_LOG 只写 `epoch rc` 单行（trigger 的多行 JSON 另存 payload 目录），
#     LAST 取值前做纯数字校验；坏行降级为 0 而不是让算术中断静默吞掉后续 tick。
#   * A6 远端 tip 先 git cat-file 判本地对象存在；缺则 fetch 单 ref（--depth 50），
#     仍缺记 "unfetched remote tip" 事件而非误报 NON-FF/rewrite；已签清单统一
#     40 位全 SHA 精确匹配（短 SHA 前缀会与同前缀长 SHA 误配）。
#   * A4 成员评论改用 --since <RFC3339> 增量过滤——--recent N cap 的是线程数不是
#     评论数，落在老 thread 的新回复会漏检。
#   * A3 runs 文本表按状态词定位列（queued/running/completed/failed/cancelled），
#     agent 名含空格不再错位——原 awk 固定取 $2:$3。
#   * A9 无 autopilot id 时事件仍先累积进 payload（consumed:false），bootstrap 后
#     可补处理——原实现在写 payload 前 return，事件事后不可恢复。
#   * A7 check#6 用 mode=ro 读库——immutable=1 对未 checkpoint 的 WAL 行返回空，
#     会漏报真突增；聚合键优先 dedupe_key（有则按请求去重）。
#   * B* 互斥加 pgrep 双确认（kill -9 后 PID 复用不再永久 skip）；trap 注册移到
#     EVENTS_FILE 创建之后；throttle 日志用真计数 $EVENTS_FILE_HITS；
#     EXPECT_PROD_SHA/CARDS/SURGE_MIN 全部 env 可覆盖。
#   * 34 卡×2 次逐卡 CLI 并发化（MAX_PAR，默认 6），单轮最坏耗时压进 StartInterval 内。
#
# 只检测与派发；不合并、不部署、不改库、不改模型通道。

REPO="${WATCH_REPO:-/Users/davidliu/Documents/TradingAgents-AShare}"
REL="/Users/davidliu/Documents/TradingAgents-AShare-releases"
LOG="${WATCH_LOG:-$REL/logs/uvicorn-c170334f.log}"
REMOTE_URL="${WATCH_REMOTE_URL:-https://github.com/Guuuu233/tradingagents-ashare-fork.git}"
TRUNK_BR="codex/dav-4-p2a-trunk"
EXPECT_PROD_SHA="${WATCH_EXPECT_PROD_SHA:-c170334f0f41909efd840f3efd76dd0d37d7558f}"
BASELINE="db9c69099b9bb04f6cfbdd5aca076a86839a26f1"
PRODDB="${WATCH_PRODDB:-$REPO/data/tradingagents.db}"
export HOME=${HOME:-/Users/davidliu}

# ---- 可配置（环境变量覆盖） ----
POLL=${WATCH_POLL:-180}
# 单次 CLI 调用的 alarm 上限（秒）；launchd 拉起时网络栈可能未就绪，给足但不无限。
MX_TIMEOUT=${WATCH_CLI_TIMEOUT:-90}
# 逐卡 CLI 的并发度：34 卡×2 轮串行最坏 ~90s+，并发后单轮控制在 ~60s 内。
MAX_PAR=${WATCH_MAX_PAR:-6}
# 无卡批量分析阈值：同一账户 20min 内 >=N 个去重请求档。
SURGE_MIN=${WATCH_SURGE_MIN:-4}
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
# 崩溃计数基线（tick 间持久化；首轮吸现状）
ERRSTATE=${WATCH_ERRSTATE:-/tmp/watch_v700.errstate}
# 成员评论增量检测水位（上次 tick 的 UTC 时刻，RFC3339）
SINCE_FILE=${WATCH_SINCE_FILE:-/tmp/watch_v700.since}
mkdir -p "$PAYLOAD_DIR"

# ---- 已签主干清单（与 v600 相同；统一 40 位全 SHA 精确匹配，新签名追加到行尾） ----
# v600 的短 SHA/伪 20 位串已按对象库解析为全 SHA（git rev-parse），避免前缀误配。
SIGNED="48f2f9b95b26b7c8e6516c8a37a4d67b918fbfc0 c679c3c7e522109d068755996c652bf04cd53007 cf7bda36c5642ed5efd34a46eb75406acacd7786 c170334f0f41909efd840f3efd76dd0d37d7558f 3596a66655dfa329de7e82daa1f3faf3170b8a1d 126cc9565ba5b4c7dac4de3ac36ef8cb076a8e6c dab8cef5678cf9d67e3ff315e7ee0cadc8e2bbf6 d2c8ac9e32bb93d932231b5adf532e82f84d265c 9d61dd1a6d0cc678ed40eb54e8a80028a8df5fef 428b416f57c720691b3ed1c1730099a7c27b7272 7b3fc9c15b5a241e5e77187a96c2d4a8a044401c 6c13b9a6d1c836d58057df2f306f14fa3448f107 2a53a9485eb6a2ffe6431a0dec690c0f350eec9b 6a7138149ff499f70197917f32ac94c5870c9cd1 1329dae58d61df0199da06033be90d94bc9eeee2 4926fb56867644ad35e0e723213700c70c766a05 65988c799d454fc41c12fbc8e5dbe89114511c69 ae677a8efc40ba59d7024c96e43024f95f24abc9 7da35a7ca8dbf131b9d29a837d2c5c39015755f0 2e1e199fc368fcbdd1460eb1c3a851220d293599 db9c69099b9bb04f6cfbdd5aca076a86839a26f1"
CARDS=${WATCH_CARDS:-"1440 1432 1429 1441 1443 1444 1445 1446 1447 1448 1449 1450 1451 1453 1454 1455 1456 1458 1459 1460 1462 1466 1474 1475 1477 1478 1479 1480 1481 1482 1484 1463 1472 1476"}
GUARD_ANY="1450"
GUARD_BACKLOG="1478:资深开发2"
GUARD_REVIEW="1448 1456 1484"
REVIEW_OK_SHA=""
REVIEWER="代码审核员"
TERMINAL="completed failed cancelled"
# runs 文本表里的状态词集合（含 CLI 可能输出但不在 TERMINAL 里的活跃态）
RUNSTATUS="queued dispatched running pending completed failed cancelled error timeout"

# 本机无 GNU timeout，用 perl alarm 包裹 CLI（防挂死；launchd 场景 PATH/env 受限）
export WATCH_MX_TIMEOUT="$MX_TIMEOUT"
mx(){ perl -e 'alarm $ENV{WATCH_MX_TIMEOUT}||90; exec @ARGV' "$@"; }
export -f mx 2>/dev/null || true

alert(){ echo "[WATCH $(date '+%m-%d %H:%M:%S')] $*" >> "$ALERTS"; }
event(){ echo "[WATCH-EVENT $(date '+%m-%d %H:%M:%S')] $*"; alert "$*"; }
remote_tip(){ mx git ls-remote "$REMOTE_URL" "$TRUNK_BR" 2>/dev/null | awk '{print $1}'; }

# runs 列表：A3 修订——文本表按"状态词"定位列，agent 名取它之前的所有列合并，
# 兼容含空格 agent 名（`multica issue runs --output json` 无 agent name 字段，只能走文本表）。
runs_line(){
  mx multica issue runs "DAV-$1" 2>/dev/null | python3 -c '
import sys,re
STATUS={"queued","dispatched","running","pending","completed","failed","cancelled","error","timeout"}
for ln in sys.stdin:
    f=ln.split()
    if len(f)<3 or not re.fullmatch(r"[0-9a-f]{8}", f[0]):
        continue
    si=next((i for i in range(1,len(f)) if f[i].lower() in STATUS), -1)
    if si<0 or si==len(f)-1:  # no status token, or status is the last column
        continue
    print(f[0]+":"+f[si].lower()+":"+" ".join(f[1:si]))' | tr '\n' ','
}
export -f runs_line 2>/dev/null || true

is_terminal(){ case " $TERMINAL " in *" $1 "*) return 0;; esac; return 1; }
is_urgent_newrun(){
  _c=$1; _rst=$2; _rag=$3
  [ "$_rst" = "running" ] || [ "$_rst" = "queued" ] || [ "$_rst" = "dispatched" ] || return 1
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
# macOS 无 flock；PID 文件 + pgrep -f 脚本名双重确认，避免 kill -9 后 PID 复用永久 skip。
if [ -f "$LOCKFILE" ]; then
  OLDPID=$(cat "$LOCKFILE" 2>/dev/null)
  if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null \
     && pgrep -f "$(basename "$0")" 2>/dev/null | grep -qx "$OLDPID"; then
    alert "skip: previous tick still running (pid $OLDPID)"
    exit 0
  fi
fi
echo $$ > "$LOCKFILE"

# ---- 崩溃计数器（A1 修订） ----
count_crashes(){
  [ -f "$LOG" ] || { echo 0; return; }
  awk '
    /Traceback \(most recent call last\)/ {tb=NR; blk=""}
    tb && NR>tb && NR<=tb+60 {blk=blk"\n"$0}
    tb && (NR==tb+60 || /Error:|Exception/){
      if (blk ~ /DebateProtocolError|ValidationError|fail-closed|debate_utils/) istb=0; else istb=1
      crash+=istb; tb=0
    }
    /CRITICAL|FATAL/ {crash++}
    END{print crash+0}' "$LOG"
}
export -f count_crashes 2>/dev/null || true

# ---- 成员评论增量（A4 修订：--since RFC3339；无水位时退化--since seed_start） ----
# 输出 `id|created_at|content…`；空输出=无新或 CLI 失败（防抖不覆盖种子）。
member_comments(){
  _c=$1; _since=$2
  mx multica issue comment list "DAV-$_c" --since "$_since" --summary --output json 2>/dev/null | python3 -c '
import json,sys
try:
  d=json.load(sys.stdin)
except Exception:
  sys.exit(0)
arr=d if isinstance(d,list) else (d.get("comments") or d.get("items") or [])
for x in arr:
    if x.get("author_type")=="member" and x.get("id"):
        content=str(x.get("content","")[:120]).replace(chr(10)," ")
        print(x["id"]+"|"+str(x.get("created_at"))+"|"+content)'
}
export -f member_comments 2>/dev/null || true

# ---- 并发原语（bash 3.2 兼容：jobs -rp 轮询，不用 wait -n） ----
par_wait_slot(){
  while [ "$(jobs -rp | wc -l)" -ge "$MAX_PAR" ]; do sleep 0.2; done
}
par_comments(){ par_wait_slot; member_comments "$1" "$2" > "$3/$1" 2>/dev/null & }
par_runs(){ par_wait_slot; runs_line "$1" > "$2/$1" 2>/dev/null & }
export -f par_wait_slot par_comments par_runs 2>/dev/null || true

# ---- 布防：种子缺失才初始化（首轮吸收现状，不算事件） ----
if [ ! -f "$SEEN_COMMENTS" ] || [ ! -f "$HIGHNUM" ]; then
  touch "$SEEN_COMMENTS"
  SEED_START=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  SEED_DIR=$(mktemp -d /tmp/watch_v700.seed.XXXXXX)
  for c in $CARDS; do par_comments "$c" "$SEED_START" "$SEED_DIR"; done
  wait
  cat "$SEED_DIR"/* 2>/dev/null | cut -d'|' -f1 | grep -E '^[0-9a-f-]{36}$' | sort -u >> "$SEEN_COMMENTS"
  rm -rf "$SEED_DIR"
  HN=$(mx multica issue list --sort created_at --direction desc --limit 1 --output json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('issues') or [{}])[0].get('number',0))" 2>/dev/null)
  echo "${HN:-0}" > "$HIGHNUM"
  # 无水位文件时，用 seed 起始时刻建立水位（布防期间到达的评论下一轮再报，不丢）
  [ -f "$SINCE_FILE" ] || echo "$SEED_START" > "$SINCE_FILE"
fi
if [ ! -f "$CARDSTATE" ]; then
  touch "$CARDSTATE"
  CS_DIR=$(mktemp -d /tmp/watch_v700.cs.XXXXXX)
  for c in $CARDS; do par_runs "$c" "$CS_DIR"; done
  wait
  for c in $CARDS; do [ -f "$CS_DIR/$c" ] && echo "$c=$(cat "$CS_DIR/$c")" >> "$CARDSTATE"; done
  rm -rf "$CS_DIR"
fi
[ -f "$STATE" ] || { TIP=$(remote_tip); echo "${TIP:-$BASELINE}" > "$STATE"; }
[ -f "$ERRSTATE" ] || count_crashes > "$ERRSTATE"
[ -f "$SINCE_FILE" ] || date -u '+%Y-%m-%dT%H:%M:%SZ' > "$SINCE_FILE"
alert "armed-v700 launchd tick poll=${POLL}s baseline=${BASELINE:0:8} autopilot=${AUTOPILOT_ID:-none}"

# ---- 把检出的事件写成一次性智能体负载并触发 autopilot ----
# A9 修订：先写 payload（无 autopilot id 时仍累积 consumed:false），再按节流 trigger。
dispatch(){
  NOW=$(date +%s)
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
  if [ -z "$AUTOPILOT_ID" ]; then
    alert "NO-AP: autopilot id unset; $EVENTS_FILE_HITS event(s) appended to payload, no agent dispatched. Run work/watch/bootstrap_autopilot.sh."
    return 0
  fi
  # 节流：距上次 trigger < DISPATCH_MIN_INTERVAL 则只累积 payload，不再 trigger。
  # A2 修订：DISPATCH_LOG 每行 `epoch rc` 单行；LAST 取值前做纯数字校验，坏行降级为 0。
  LAST=$(tail -1 "$DISPATCH_LOG" 2>/dev/null | awk '{print $1}')
  [[ "$LAST" =~ ^[0-9]+$ ]] || LAST=0
  if [ $((NOW-LAST)) -lt "$DISPATCH_MIN_INTERVAL" ]; then
    alert "dispatch-throttled: $EVENTS_FILE_HITS event(s) appended to payload; last trigger $((NOW-LAST))s ago (<${DISPATCH_MIN_INTERVAL}s)"
    return 0
  fi
  # 触发一次性智能体。它自行读 $PAYLOAD_DIR/latest.json → 处理 → 在 issue 交付。
  TRIG=$(mx multica autopilot trigger "$AUTOPILOT_ID" --output json 2>/dev/null)
  RC=$?
  echo "$NOW rc=$RC" >> "$DISPATCH_LOG"
  # trigger 的多行 JSON 原文另存（排障用，不进节流解析链）
  [ -n "$TRIG" ] && echo "$TRIG" > "$PAYLOAD_DIR/last_trigger_response.json"
  if [ "$RC" -eq 0 ]; then
    event "dispatched one-shot agent (autopilot $AUTOPILOT_ID) payload=$PAYLOAD"
  else
    alert "DISPATCH-FAIL rc=$RC autopilot=$AUTOPILOT_ID out=${TRIG:0:200}"
  fi
  return 0
}

EVENTS_FILE=$(mktemp /tmp/watch_v700.events.XXXXXX)
trap 'rm -f "$LOCKFILE" "$EVENTS_FILE"' EXIT
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

  # 2) 成员评论（--since 增量；只报 member；已有 id 跳过；CLI 拉空跳过）
  # N3-1 修订：水位 = 本次扫描开始时刻，避免"扫描中到达"的评论被写后的水位吃掉
  SINCE=$(cat "$SINCE_FILE" 2>/dev/null)
  SCAN_START=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  MC_DIR=$(mktemp -d /tmp/watch_v700.mc.XXXXXX)
  for c in $CARDS; do par_comments "$c" "$SINCE" "$MC_DIR"; done
  wait
  for c in $CARDS; do
    [ -s "$MC_DIR/$c" ] || continue
    while IFS= read -r l; do
      cid=$(echo "$l" | cut -d'|' -f1)
      grep -qx "$cid" "$SEEN_COMMENTS" 2>/dev/null && continue
      echo "$cid" >> "$SEEN_COMMENTS"
      record "NEW member comment DAV-$c: $l"
    done < "$MC_DIR/$c"
  done
  rm -rf "$MC_DIR"
  echo "$SCAN_START" > "$SINCE_FILE"

  # 3) 运行进入终态 + URGENT 类新 run（CLI 拉空跳过，不覆盖种子）
  RS_DIR=$(mktemp -d /tmp/watch_v700.rs.XXXXXX)
  for c in $CARDS; do par_runs "$c" "$RS_DIR"; done
  wait
  for c in $CARDS; do
    [ -f "$RS_DIR/$c" ] || continue
    NEWLINE=$(cat "$RS_DIR/$c")
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
  rm -rf "$RS_DIR"

  # 4) 主干 tip 与已签清单（A6 修订：先判本地对象，缺则 fetch 单 ref，仍缺记 "unfetched"）
  NEW=$(remote_tip)
  CUR=$(cat "$STATE" 2>/dev/null)
  if [ -n "$NEW" ] && [ -n "$CUR" ] && [ "$NEW" != "$CUR" ]; then
    HAVE_NEW=0
    if git cat-file -e "$NEW" 2>/dev/null; then
      HAVE_NEW=1
    else
      mx git -C "$REPO" fetch --quiet origin "$TRUNK_BR" 2>/dev/null || true
      git cat-file -e "$NEW" 2>/dev/null && HAVE_NEW=1
    fi
    if [ "$HAVE_NEW" -eq 0 ]; then
      record "URGENT trunk moved to unfetched remote tip ${NEW:0:8} (object unavailable locally)"
    else
      if ! git merge-base --is-ancestor "$CUR" "$NEW" 2>/dev/null; then
        record "URGENT trunk NON-FF/rewrite: ${CUR:0:8} -> ${NEW:0:8}"
      else
        # 仅 FF 时才判签名链；NON-FF 已告警，不再用 signed-ancestor 粉饰成"正常前进"
        ok=0
        for s in $SIGNED; do
          if [ "$NEW" = "$s" ]; then ok=1; break; fi
          if git merge-base --is-ancestor "$s" "$NEW" 2>/dev/null; then ok=1; break; fi
        done
        SUBJ=$(git log -1 --format='%h %s' "$NEW" 2>/dev/null)
        if [ "$ok" -eq 1 ]; then record "trunk FF -> $SUBJ (signed-chain ok)"; else record "URGENT trunk moved to UNSIGNED $SUBJ"; fi
      fi
    fi
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
  # 崩溃增量（A1 修订）：ERR 与落盘基线比较；告警时抬基线，未告警时基线不动
  ERR=$(count_crashes)
  BASE_ERR=$(cat "$ERRSTATE" 2>/dev/null); [[ "$BASE_ERR" =~ ^[0-9]+$ ]] || BASE_ERR=0
  if [ "$ERR" -gt "$BASE_ERR" ]; then
    record "URGENT prod log new crash/traceback: $ERR (baseline $BASE_ERR)"
    echo "$ERR" > "$ERRSTATE"
  fi

  # 6) 无卡批量分析突增（D-072；v600 中 PRODDB 未定义、此项实际为死代码——本脚本已修正）
  # A7 修订：mode=ro（非 immutable=1）能见到未 checkpoint 的 WAL 行；dedupe_key 存在则按请求去重。
  if [ -f "$PRODDB" ]; then
    HAS_DEDUPE=$(sqlite3 "file:$PRODDB?mode=ro" "SELECT COUNT(*) FROM pragma_table_info('reports') WHERE name='dedupe_key'" 2>/dev/null)
    if [ "${HAS_DEDUPE:-0}" -ge 1 ] 2>/dev/null; then
      SURGE=$(sqlite3 "file:$PRODDB?mode=ro" "
        SELECT COALESCE(user_id,'unknown'), COUNT(DISTINCT dedupe_key) FROM reports
         WHERE created_at > datetime('now','localtime','-20 minutes')
         GROUP BY 1 HAVING COUNT(DISTINCT dedupe_key) >= $SURGE_MIN AND COALESCE(user_id,'unknown') != '429163f7-50b6-4982-8bdf-96ae99506843'
      " 2>/dev/null)
    else
      SURGE=$(sqlite3 "file:$PRODDB?mode=ro" "
        SELECT COALESCE(user_id,'unknown'), COUNT(*) FROM reports
         WHERE created_at > datetime('now','localtime','-20 minutes')
         GROUP BY 1 HAVING COUNT(*) >= $SURGE_MIN AND COALESCE(user_id,'unknown') != '429163f7-50b6-4982-8bdf-96ae99506843'
      " 2>/dev/null)
    fi
    if [ -n "$SURGE" ]; then
      record "URGENT un-carded batch analysis surge (non-daily-account, >=${SURGE_MIN} in 20min): $SURGE"
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
