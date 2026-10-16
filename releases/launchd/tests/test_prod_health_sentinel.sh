#!/usr/bin/env bash
# DAV-2063 四情形桩测试：通 / 不通（连续两次才告警）/ 版本不符 / 恢复
# 用法：bash releases/launchd/tests/test_prod_health_sentinel.sh
# 全程只用临时目录 + 随机高位端口的桩服务，**不碰生产 8000**，不安装 launchd。
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHD_DIR="$(dirname "${SCRIPT_DIR}")"
SENTINEL="${LAUNCHD_DIR}/ta-prod-health-sentinel.sh"
PY_BIN="${PY_BIN:-$(command -v python3)}"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
WATCH="${TMP}/ta-watch-1-ok"          # 每个情形独立 WATCH_DIR，state 不互相污染
watch_reset() { WATCH="${TMP}/ta-watch-$1"; rm -rf "${WATCH}"; mkdir -p "${WATCH}"; }
STUB_LOG="${TMP}/stub.log"
STUB_PID=""
EXPECT_SHA="deadbeefcafe1234"
PORT=""

pass=0; fail=0
ok()   { echo "  PASS: $*"; pass=$((pass+1)); }
bad()  { echo "  FAIL: $*"; fail=$((fail+1)); }

start_stub() {  # $1=mode: ok | bad_sha | http500 | garbage | slow
  stop_stub
  PORT=$("${PY_BIN}" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
  cat > "${TMP}/stub.py" <<'PY'
import json, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = sys.argv[1]; SHA = sys.argv[2]

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith("/healthz"):
            self.send_response(404); self.end_headers(); return
        if MODE == "http500":
            self.send_response(500); self.end_headers(); self.wfile.write(b"{}"); return
        if MODE == "garbage":
            self.send_response(200); self.end_headers(); self.wfile.write(b"not json"); return
        if MODE == "slow":
            time.sleep(30); self.send_response(200); self.end_headers(); return
        body = json.dumps({"status": "ok", "commit_sha": "0" * 16 if MODE == "bad_sha" else SHA}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", int(sys.argv[3])), H).serve_forever()
PY
  "${PY_BIN}" "${TMP}/stub.py" "$1" "${EXPECT_SHA}" "${PORT}" >>"${STUB_LOG}" 2>&1 &
  STUB_PID=$!
  for _ in $(seq 1 50); do
    if "${PY_BIN}" - "$PORT" <<'PY' 2>/dev/null
import socket,sys
s=socket.create_connection(("127.0.0.1", int(sys.argv[1])), 0.2); s.close()
PY
    then return 0; fi
    sleep 0.1
  done
  echo "stub failed to start (mode=$1)"; cat "${STUB_LOG}"; return 1
}

stop_stub() {
  if [ -n "${STUB_PID}" ]; then kill "${STUB_PID}" 2>/dev/null; wait "${STUB_PID}" 2>/dev/null; STUB_PID=""; fi
}

run_sentinel() {  # env 覆盖 + 一次执行；输出到 stdout
  HEALTH_BASE_URL="http://127.0.0.1:${PORT}" \
  EXPECT_PROD_SHA="${EXPECT_SHA}" \
  WATCH_DIR="${WATCH}" \
  FAIL_THRESHOLD=2 \
  HTTP_TIMEOUT=3 \
  TA_WATCH_NOTIFY=0 \
  PYTHON_BIN="${PY_BIN}" \
    bash "${SENTINEL}" 2>&1
}

# 告警/恢复计数一律以 **脚本 stdout 的事件行** 为准，不以目录内文件数为准：
# 落地文件名是秒级时间戳（ta-prod-health-sentinel.sh 的 ts_utc），同一秒内的多次
# 告警会写同一个文件名并互相覆盖，目录文件计数因此跨秒随机。stdout 事件行不受影响。
alert_lines()  { printf '%s\n' "$1" | grep -c 'sentinel: ALERT ' || true; }
recovered_lines() { printf '%s\n' "$1" | grep -c 'sentinel: RECOVERED ' || true; }
alert_files()  { ls "${WATCH}" 2>/dev/null | grep -c '^ALERT-prod-[0-9]' || true; }
recovered_files() { ls "${WATCH}" 2>/dev/null | grep -c '^ALERT-prod-recovered-' || true; }

echo "== 情形 1：healthz 通（sha 匹配）→ 无告警 =="
watch_reset 1-ok
start_stub ok || exit 1
out=$(run_sentinel); rc=$?
[ "${rc}" = "0" ] && ok "exit 0" || bad "exit ${rc}"
[ "$(alert_files)" = "0" ] && ok "无告警文件" || bad "出现告警文件"
[ "$(recovered_files)" = "0" ] && ok "无恢复记录（首次健康不写恢复）" || bad "误写恢复记录"
echo "${out}" | grep -q 'ok' && ok "输出 ok" || bad "输出异常: ${out}"

echo "== 情形 2：healthz 不通 → 第一次不告警，第二次才告警 =="
watch_reset 2-unreachable
stop_stub
PORT=1   # 无监听端口，必然连接失败
out1=$(run_sentinel); n1=$(alert_lines "${out1}")
out2=$(run_sentinel); n2=$(alert_lines "${out2}")
[ "${n1}" = "0" ] && ok "第 1 次失败无告警" || bad "第 1 次失败就告警（未去抖）"
echo "${out1}" | grep -q '1/2' && ok "第 1 次计入 1/2" || bad "第 1 次未计数: ${out1}"
[ "${n2}" = "$((n1 + 1))" ] && ok "第 2 次失败新增 1 个告警" || bad "第 2 次未告警（${n1}->${n2}）"
[ "$(recovered_files)" = "0" ] && ok "未恢复则无恢复记录" || bad "误写恢复记录"
f=$(ls "${WATCH}" | grep '^ALERT-prod-[0-9]' | head -1)
grep -q 'reason=unreachable' "${WATCH}/${f}" && ok "告警原因为 unreachable" || bad "原因字段不对"

echo "== 情形 3：commit_sha 与期望不符 → 立即告警（独立全新 state）=="
watch_reset 3-sha   # 不复用情形 2 的 state，fresh 下单次命中即验证
start_stub bad_sha || exit 1
n_before=$(alert_files)
out3=$(run_sentinel)
[ "$(alert_lines "${out3}")" = "1" ] && ok "单次即告警（不等两次）" || bad "版本漂移未即时告警：$(printf '%s' "${out3}" | tr '\n' '|')"
[ "$(alert_files)" -ge "$((n_before + 1))" ] || echo "  WARN: 告警文件与前次同秒被覆盖（文件名秒级精度），仅告警"
f=$(ls -t "${WATCH}" | grep '^ALERT-prod-[0-9]' | head -1)
grep -q 'reason=sha_mismatch' "${WATCH}/${f}" && ok "原因为 sha_mismatch" || bad "原因字段不对"
grep -q "observed_commit_sha=$(printf '0%.0s' {1..16})" "${WATCH}/${f}" && ok "记录了观测 sha" || bad "未记录观测 sha"

echo "== 情形 4：恢复 → 写恢复记录（沿用情形 3 的告警状态）=="
n_recovered_before=$(recovered_files)
stop_stub; start_stub ok || exit 1
out4=$(run_sentinel)
[ "$(recovered_lines "${out4}")" = "1" ] && ok "恢复后写出恢复记录" || bad "未写恢复记录"
f=$(ls -t "${WATCH}" | grep '^ALERT-prod-recovered-' | head -1)
grep -q 'previous_alert_reason=sha_mismatch' "${WATCH}/${f}" && ok "记录前序告警原因" || bad "缺前序原因"
grep -q 'time_utc=' "${WATCH}/${f}" && ok "含恢复时间" || bad "缺恢复时间"
out_healthy=$(run_sentinel)
[ "$(recovered_lines "${out_healthy}")" = "0" ] && ok "持续健康不重复写恢复记录" || bad "重复写恢复记录"

echo "== 附加：非 2xx / 垃圾响应 / 并发锁 =="
watch_reset 4-extra
start_stub http500 || exit 1
out5=$(run_sentinel); out6=$(run_sentinel)   # 非 2xx 也走去抖，需两次
n_alert=$(alert_lines "${out5}"); n_alert=$((n_alert + $(alert_lines "${out6}")))
[ "${n_alert}" = "1" ] && ok "HTTP 500 计为不通（两次后告警）" || bad "HTTP 500 未告警（告警 ${n_alert} 次）"
f=$(ls "${WATCH}" | grep '^ALERT-prod-[0-9]' | head -1)
grep -q 'reason=unreachable' "${WATCH}/${f}" && ok "非 2xx 原因为 unreachable" || bad "原因字段不对"
stop_stub; start_stub garbage || exit 1
out7=$(run_sentinel); out8=$(run_sentinel)
n=$(alert_lines "${out7}"); n=$((n + $(alert_lines "${out8}")))
[ "${n}" -gt "0" ] && ok "2xx 但响应体不可解析计为不通" || bad "垃圾响应未告警（两次执行均无 ALERT 事件）"

mkdir -p "${WATCH}/.sentinel/check.lock"
out=$(run_sentinel)
echo "${out}" | grep -q '跳过' && ok "已有锁时跳过（并发去重）" || bad "锁未生效: ${out}"
rmdir "${WATCH}/.sentinel/check.lock"

stop_stub
echo
echo "通过 ${pass}，失败 ${fail}"
[ "${fail}" = "0" ] || exit 1