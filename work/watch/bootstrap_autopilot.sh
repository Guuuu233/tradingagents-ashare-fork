#!/bin/bash
# bootstrap_autopilot.sh — 创建 watch_v700 派发的一次性智能体 autopilot（一次性手工执行）
#
# 用法：
#   bash work/watch/bootstrap_autopilot.sh "<agent 名或 uuid>" "<autopilot 标题>"
# 例：
#   bash work/watch/bootstrap_autopilot.sh "高级开发·支援" "TA守望·一次性处理"
#
# 产物：
#   1. 一个 run_only autopilot（无聊天历史）；其 description 即每次 trigger 时的 run prompt。
#   2. autopilot UUID 写入 /tmp/watch_v700.autopilot_id（watch_v700.sh 默认读取该文件）。
#
# 之后 watch_v700 检出事件时会：
#   * 把事件写入 /tmp/watch_v700_payload/latest.json
#   * `multica autopilot trigger <id>` 唤起一个无历史 run
# 该 run 的指令（description 下方模板）要求它读 latest.json、处理事件、在对应 issue 交付，
# 并把 latest.json 标记为 consumed，便于 watch 的节流继续累积。

set -euo pipefail
AGENT=${1:?"agent name or uuid required"}
TITLE=${2:-"TA守望·一次性处理"}
ID_FILE=${WATCH_AUTOPILOT_ID_FILE:-/tmp/watch_v700.autopilot_id}
export HOME=/Users/davidliu
mx(){ perl -e 'alarm 60; exec @ARGV' "$@"; }

PROMPT=$(cat <<'EOF'
你是 TA守望 检出事件的一次性处理智能体（无历史、由 launchd 定时脚本按需唤起）。

输入：
  事件负载在 /tmp/watch_v700_payload/latest.json，结构：
    {"generated_at":<epoch>,"consumed":false,"events":["<事件行>",...]}
  每条事件行与守望告警同格式，可能含 `URGENT` 前缀。

任务：
  1. 读 latest.json，逐条解析事件（新卡 / 成员评论 / run 终态 / trunk 异动 / prod 底线 / 无卡批量突增）。
  2. 按事件定位到对应 Multica issue，用 `multica` CLI 核实后处理：
     - 新卡/成员评论：阅读并在该 issue 内按需响应；
     - run 终态：核对结果并记录；
     - URGENT（trunk 未签/NON-FF、守卫卡未预期 run、prod DOWN/SHA 偏离/真崩溃、无卡批量突增）：
       优先在对应 issue 上报并标记需要总控注意；
  3. 处理完把 latest.json 里 "consumed" 置为 true（其余字段保留），使守望脚本的节流继续累积。

纪律：
  - 只读+评论级响应；不合入主干、不部署、不改库、不改模型通道、不动用户配置。
  - 证据优先：引 issue id / run id / SHA；不臆造。
  - 每条 URGENT 必须在相关 issue 留痕；普通事件按需留痕。
EOF
)

echo "[bootstrap] creating run_only autopilot agent=$AGENT title=$TITLE"
OUT=$(mx multica autopilot create \
  --mode run_only \
  --agent "$AGENT" \
  --title "$TITLE" \
  --description "$PROMPT" \
  --output json)
echo "$OUT"
ID=$(echo "$OUT" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d.get('id') or (d.get('autopilot') or {}).get('id') or '')")
if [ -z "$ID" ]; then
  echo "[bootstrap] WARN: could not parse autopilot id from output above" >&2
  exit 1
fi
echo "$ID" > "$ID_FILE"
echo "[bootstrap] autopilot id -> $ID_FILE : $ID"
echo "[bootstrap] done. watch_v700.sh will pick it up on next tick (or export WATCH_AUTOPILOT_ID=$ID)."
