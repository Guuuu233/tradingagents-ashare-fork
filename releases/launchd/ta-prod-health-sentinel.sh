#!/usr/bin/env bash
# ta-prod-health-sentinel.sh — com.tradingagents.prod-health-sentinel 哨兵脚本
# （DAV-2063；总控 10-15 令：只交模板，不安装；10-17 与 DAV-1852 一起安装）
#
# 职责（纯脚本 + launchd，独立于任何 Claude/agent 会话）：
#   1. 拉起即查一次（plist RunAtLoad=true），此后由 launchd StartInterval=300
#      每 5 分钟触发一次；单次执行必须短于间隔（curl --max-time 10）。
#   2. GET ${HEALTH_BASE_URL}/healthz：
#        - 不通（连接失败/非 2xx/超时/响应体无法解析）：**连续两次**
#          （FAIL_THRESHOLD，默认 2）才告警，第一次只记状态不告警；
#        - 通但 commit_sha 与 EXPECT_PROD_SHA 不符：**立即**告警
#          （版本漂移不做去抖，避免长时间跑错 release 而无人知）。
#   3. 告警落地：写 ${WATCH_DIR}/ALERT-prod-<utc时间戳>-<原因>-<pid>.log，
#      并用 osascript 发系统通知（TA_WATCH_NOTIFY=0 可关，测试用）。
#   4. 从告警状态恢复到健康后写一条恢复记录：
#      ${WATCH_DIR}/ALERT-prod-recovered-<utc时间戳>-<pid>.log（含恢复时间、前序原因）。
#
#   文件名带进程号（DAV-2157）：时间戳只精确到秒（ts_utc），同秒内发生的两次记录
#   此前会写同一个文件名、后一次静默覆盖前一次（DAV-2149 根因：丢一条告警）。
#   哨兵有 mkdir 原子锁，同一时刻至多一个哨兵进程在跑；单次执行最多写一个记录
#   文件（告警与恢复分支互斥，且各自只调一次）。因此 <pid> 后缀足以保证同秒不撞名，
#   无需额外计数器；秒级时间戳保留，便于人读与按名排序。
#   5. **不做任何自动重启/拉起**——重启语义由 com.tradingagents.ashare
#      （DAV-1790 体系 wrapper + DAV-1852 安装）负责，哨兵只观测与告警。
#
# 配置（全部走环境变量，由 plist 的 EnvironmentVariables 注入，模板给占位符）：
#   HEALTH_BASE_URL  默认 http://127.0.0.1:8000
#   EXPECT_PROD_SHA  期望 commit_sha；留空 = 只探活不校验版本
#   WATCH_DIR        默认 ~/ta-watch
#   FAIL_THRESHOLD   连续失败次数门槛，默认 2
#   HTTP_TIMEOUT     单次 curl 超时秒数，默认 10
#   TA_WATCH_NOTIFY  0 = 不发 osascript 通知（测试默认 0）
#
# 并发：launchd 每 300s 一次、脚本耗时 ≪ 间隔，正常不会重叠；仍用 mkdir 原子锁
#      防 kickstart 与定时唤起并发（macOS 无 flock，与 1790 wrapper 同一手法）。
#      拿不到锁直接 exit 0（上一轮还在跑，不重复探测）。

set -u

HEALTH_BASE_URL="${HEALTH_BASE_URL:-http://127.0.0.1:8000}"
EXPECT_PROD_SHA="${EXPECT_PROD_SHA:-}"
WATCH_DIR="${WATCH_DIR:-${HOME}/ta-watch}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-2}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-10}"
TA_WATCH_NOTIFY="${TA_WATCH_NOTIFY:-1}"

STATE_DIR="${WATCH_DIR}/.sentinel"
STATE_FILE="${STATE_DIR}/state"          # last_status / consec_fail / alerting / last_reason
LOCK_DIR="${STATE_DIR}/check.lock"
CURL_BIN="${CURL_BIN:-/usr/bin/curl}"

ts_utc() { date -u '+%Y%m%dT%H%M%SZ'; }
ts_human() { date '+%F %T %Z'; }

# 通知失败不得影响判定（通知通道坏了也要留告警文件）
notify() {
  local title="$1" body="$2"
  [ "${TA_WATCH_NOTIFY}" = "1" ] || return 0
  command -v osascript >/dev/null 2>&1 || return 0
  osascript -e "display notification \"${body//\"/\'}\" with title \"${title//\"/\'}\"" \
    >/dev/null 2>&1 || true
}

# ---- 状态读写（单行 key=value；解析只取自己认识的键）----
read_state() {
  last_status="unknown"; consec_fail=0; alerting=0; last_reason=""
  [ -r "${STATE_FILE}" ] || return 0
  while IFS='=' read -r k v; do
    case "$k" in
      last_status) last_status="$v" ;;
      consec_fail) consec_fail="$v" ;;
      alerting)    alerting="$v" ;;
      last_reason) last_reason="$v" ;;
    esac
  done < "${STATE_FILE}"
  case "${consec_fail}" in ''|*[!0-9]*) consec_fail=0 ;; esac
  case "${alerting}"    in ''|*[!0-9]*) alerting=0 ;; esac
}

write_state() {
  local tmp="${STATE_FILE}.tmp.$$"
  {
    echo "last_status=${1}"
    echo "consec_fail=${2}"
    echo "alerting=${3}"
    echo "last_reason=${4}"
  } > "${tmp}" 2>/dev/null || return 1
  mv -f "${tmp}" "${STATE_FILE}" 2>/dev/null || { rm -f "${tmp}" 2>/dev/null; return 1; }
}

raise_alert() {   # $1=原因标签 $2=人类可读细节
  local reason="$1" detail="$2" stamp file
  stamp=$(ts_utc)
  file="${WATCH_DIR}/ALERT-prod-${stamp}-${reason}-$$.log"
  mkdir -p "${WATCH_DIR}" 2>/dev/null
  {
    echo "time_human=$(ts_human)"
    echo "time_utc=${stamp}"
    echo "reason=${reason}"
    echo "detail=${detail}"
    echo "health_base_url=${HEALTH_BASE_URL}"
    echo "expect_prod_sha=${EXPECT_PROD_SHA:-<unset>}"
    echo "observed_commit_sha=${observed_sha:-<none>}"
    echo "observed_status=${observed_status:-<none>}"
    echo "consecutive_failures=${consec_fail}"
    echo "prev_status=${last_status}"
  } > "${file}" 2>/dev/null || echo "sentinel: cannot write ${file}" >&2
  notify "TradingAgents 生产告警" "${reason}: ${detail}（${stamp}）"
  echo "sentinel: ALERT ${reason} -> ${file}"
}

record_recovery() {  # $1=前序告警原因
  local stamp file
  stamp=$(ts_utc)
  file="${WATCH_DIR}/ALERT-prod-recovered-${stamp}-$$.log"
  mkdir -p "${WATCH_DIR}" 2>/dev/null
  {
    echo "time_human=$(ts_human)"
    echo "time_utc=${stamp}"
    echo "previous_alert_reason=${1}"
    echo "observed_commit_sha=${observed_sha:-<none>}"
    echo "observed_status=${observed_status:-<none>}"
    echo "health_base_url=${HEALTH_BASE_URL}"
  } > "${file}" 2>/dev/null || echo "sentinel: cannot write ${file}" >&2
  notify "TradingAgents 生产恢复" "已恢复健康（前序：${1}），${stamp}"
  echo "sentinel: RECOVERED -> ${file}"
}

# ---- 前置：目录与锁 ----
if ! mkdir -p "${STATE_DIR}" 2>/dev/null || [ ! -w "${STATE_DIR}" ]; then
  echo "sentinel: state dir unusable ${STATE_DIR}（fail-closed，本次不判定）" >&2
  exit 0
fi
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "sentinel: 上一轮探测仍在运行（锁 ${LOCK_DIR}），本次跳过" >&2
  exit 0
fi
trap 'rmdir "${LOCK_DIR}" 2>/dev/null || true' EXIT

read_state

# ---- 探测 ----
observed_status=""
observed_sha=""
observed_error=""
probe_reason="unreachable"   # 本轮失败类别：unreachable | sha_mismatch

body_file="${STATE_DIR}/body.$$"
http_code=$("${CURL_BIN}" -sS -o "${body_file}" -w '%{http_code}' \
  --max-time "${HTTP_TIMEOUT}" "${HEALTH_BASE_URL}/healthz" 2>/dev/null) || http_code="000"

if [ "${http_code}" = "000" ] || [ -z "${http_code}" ]; then
  observed_error="healthz 不可达（curl 退出非零或 http_code=${http_code:-empty}）"
elif [ "${http_code}" -lt 200 ] 2>/dev/null || [ "${http_code}" -ge 300 ] 2>/dev/null; then
  observed_error="healthz 返回 HTTP ${http_code}"
  observed_status=$(tr -d '\n' < "${body_file}" 2>/dev/null | cut -c1-300)
else
  # 解析 commit_sha：优先 python3（生产锁定解释器可选 CURL_JQ/PYTHON_BIN），退化为 grep。
  py_bin="${PYTHON_BIN:-}"
  if [ -z "${py_bin}" ]; then
    for cand in /usr/bin/python3; do [ -x "${cand}" ] && { py_bin="${cand}"; break; }; done
  fi
  if [ -n "${py_bin}" ]; then
    observed_sha=$("${py_bin}" -c '
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    print(""); sys.exit(0)
print(d.get("commit_sha") or "")
' "${body_file}" 2>/dev/null)
  fi
  if [ -z "${observed_sha}" ]; then
    observed_sha=$(tr -d '\n' < "${body_file}" 2>/dev/null \
      | sed -n 's/.*"commit_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  fi
  observed_status=$(tr -d '\n' < "${body_file}" 2>/dev/null | cut -c1-300)
  [ -z "${observed_sha}" ] && observed_error="healthz 2xx 但响应缺 commit_sha"
fi
rm -f "${body_file}" 2>/dev/null

# ---- 判定 ----
if [ -z "${observed_error}" ] && [ -n "${EXPECT_PROD_SHA}" ] \
   && [ "${observed_sha}" != "${EXPECT_PROD_SHA}" ]; then
  probe_reason="sha_mismatch"
  observed_error="commit_sha 不符：observed=${observed_sha:-<empty>} expected=${EXPECT_PROD_SHA}"
fi

if [ -z "${observed_error}" ]; then
  # 健康
  if [ "${alerting}" = "1" ]; then
    record_recovery "${last_reason:-unknown}"
  fi
  write_state "ok" 0 0 "" \
    || echo "sentinel: state write failed（${STATE_FILE}，下次运行将按未知前序判定）" >&2
  echo "sentinel: ok (commit_sha=${observed_sha:-<unset-check-skipped>})"
  exit 0
fi

consec_fail=$((consec_fail + 1))
if [ "${probe_reason}" = "sha_mismatch" ]; then
  # 版本漂移不去抖：单次命中即告警（否则长时间跑错 release 而无人知）
  raise_alert "${probe_reason}" "${observed_error}"
  write_state "${probe_reason}" "${consec_fail}" 1 "${probe_reason}" \
    || echo "sentinel: state write failed（${STATE_FILE}）" >&2
elif [ "${consec_fail}" -ge "${FAIL_THRESHOLD}" ]; then
  raise_alert "${probe_reason}" "${observed_error}"
  write_state "${probe_reason}" "${consec_fail}" 1 "${probe_reason}" \
    || echo "sentinel: state write failed（${STATE_FILE}）" >&2
else
  # 未达门槛：只记状态，不告警（第一次失败静默，避免瞬时抖动刷屏）
  write_state "degraded" "${consec_fail}" 0 "${probe_reason}:${observed_error}" \
    || echo "sentinel: state write failed（${STATE_FILE}）" >&2
  echo "sentinel: failure ${consec_fail}/${FAIL_THRESHOLD}（未达门槛，不告警）: ${observed_error}"
fi
exit 0