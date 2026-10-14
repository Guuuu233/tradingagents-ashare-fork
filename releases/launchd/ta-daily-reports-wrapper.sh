#!/usr/bin/env bash
# ta-daily-reports-wrapper.sh — com.davidliu.ta-daily-reports 包装脚本
# DAV-2035 模板（总控 10-13 令：**只交模板，不安装**；10-18 部署后与定时任务
# 检出升级同发布卡安装）。
#
# 职责（每个交易日 20:30，由 launchd 唤起一次）：
#   1. 跑 `scripts/dav1507_exit_progress.py --json`  → 当日进度表 JSON；
#   2. 跑 `scripts/dav1950_exit_acceptance_report.py --json` → 当日 1950 验收报告 JSON；
#   3. 两份脚本都只读 `~/ta-snapshot/current.db`
#      （脚本内以 `file:…?mode=ro&immutable=1` 的 URI 方式打开——依赖 DAV-2033；
#      本模板不自行拼 URI，只负责传 `--db`，读取口径由 2033 统一）；
#   4. 输出统一落 `~/ta-work/reports/YYYY-MM-DD/`，**保留 30 天**，过期目录清理；
#   5. 任一脚本失败 → 写告警文件 `ALERT-<ts>.log` + 状态标记 + **非零退出**，
#      绝不静默（launchd 只看退出码，fail-closed 靠非零）。
#
# 占位符/可调项（部署时按需改，全部可用环境变量覆盖，见下方 "部署" 节）：
#   PYTHON_BIN   解释器（生产固定 .venv310）
#   PROJECT_ROOT 专用发布检出（如 ~/ta-reports-<sha>，由 plist 显式提供；
#                无默认值，主检出 ~/Documents/TradingAgents-AShare 明确拒收）。
#                启动时 git -C "$PROJECT_ROOT" merge-base --is-ancestor
#                8571c80b HEAD 核对该检出包含 DAV-2033，不满足即 fail_alert。
#   REQUIRED_DAV2033_COMMIT 上述核对的锚定提交（默认 8571c80b，可覆盖）
#   SNAPSHOT_DB  只读快照整库
#   OUT_ROOT     报告输出根目录
#   RETENTION_DAYS 保留天数（默认 30）
#
# 纪律：不安装、不碰生产；实测方式见 tests/test_ta_daily_reports_launchd.py
# （用桩解释器 + 临时目录跑全链路，不依赖真实快照）。

set -u

# ---- 部署时可改（缺省值即总控令口径） ----------------------------------------
PYTHON_BIN="${TA_PYTHON_BIN:-/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python}"
# 总控 10-14 令：PROJECT_ROOT 无默认值——必须由 plist/环境显式给一个专用检出
# （如 ~/ta-reports-<sha>），绝不再回落到主检出。
PROJECT_ROOT="${TA_PROJECT_ROOT:-}"
MAIN_CHECKOUT="/Users/davidliu/Documents/TradingAgents-AShare"
# DAV-2033 主干合入提交（codex/dav-4-p2a-trunk 上的 8571c80b）。
# 早前用的 cac73667 未被主干包含，文档化安装步骤产出的检出会被这里拒收，
# 故默认锚点只接受主干可达的 SHA。
REQUIRED_DAV2033_COMMIT="${TA_REQUIRED_DAV2033_COMMIT:-8571c80b3578c7ea40e3bdf6eed2ad7646415db9}"
SNAPSHOT_DB="${TA_SNAPSHOT_DB:-/Users/davidliu/ta-snapshot/current.db}"
OUT_ROOT="${TA_REPORTS_ROOT:-/Users/davidliu/ta-work/reports}"
RETENTION_DAYS="${TA_RETENTION_DAYS:-30}"
# 交易日历显式传入，避免依赖 1507 内部对 ~/Documents 路径的猜测；
# 总控 10-14 令：文件不存在时直接 fail_alert，不再 WARN 放行。
CALENDAR_JSON="${TA_CALENDAR_JSON:-${PYTHON_BIN%/bin/python}/lib/python3.10/site-packages/akshare/file_fold/calendar.json}"
LOG_DIR="${TA_REPORTS_LOG_DIR:-${OUT_ROOT}/_logs}"

PROGRESS_SCRIPT="${PROJECT_ROOT}/scripts/dav1507_exit_progress.py"
ACCEPT_SCRIPT="${PROJECT_ROOT}/scripts/dav1950_exit_acceptance_report.py"

RUN_DATE="$(date '+%F')"
RUN_TS="$(date '+%F %T')"
STAMP="$(date '+%Y%m%d-%H%M%S')"
DAY_DIR="${OUT_ROOT}/${RUN_DATE}"
ALERT_FILE="${DAY_DIR}/ALERT-${STAMP}.log"

ts() { date '+%F %T'; }

# fail-closed：写告警文件 → stderr → 非零退出。告警目录不可写时退化到
# ${LOG_DIR}，再退化 stderr（永不静默）。
fail_alert() {
  local msg="$*"
  local written=""
  if mkdir -p "${DAY_DIR}" 2>/dev/null; then
    {
      echo "[${RUN_TS}] DAV-2035 daily reports FAILED"
      echo "reason: ${msg}"
      echo "snapshot_db: ${SNAPSHOT_DB}"
      echo "out_dir: ${DAY_DIR}"
      echo "see ${LOG_DIR}/launchd.err.log for full stderr"
    } > "${ALERT_FILE}" 2>/dev/null && written="${ALERT_FILE}"
  fi
  if [ -z "${written}" ] && mkdir -p "${LOG_DIR}" 2>/dev/null; then
    {
      echo "[${RUN_TS}] DAV-2035 daily reports FAILED"
      echo "reason: ${msg}"
    } > "${LOG_DIR}/ALERT-${STAMP}.log" 2>/dev/null && written="${LOG_DIR}/ALERT-${STAMP}.log"
  fi
  if [ -n "${written}" ]; then
    echo "[${RUN_TS}] ALERT written: ${written}" >&2
  else
    echo "[${RUN_TS}] ALERT FILE UNWRITABLE; reason: ${msg}" >&2
  fi
  echo "FAILED: ${msg}" > "${DAY_DIR}/run-status-${STAMP}.txt" 2>/dev/null
  exit 1
}

# ---- 前置断言（fail-closed） -------------------------------------------------
[ -n "${PROJECT_ROOT}" ] || fail_alert "TA_PROJECT_ROOT unset — plist must point at the dedicated reports checkout (e.g. ~/ta-reports-<sha>)"
# 主检出比对前做 pwd -P 规范化，避免 // 或符号链接绕过字符串拒收。
PROJECT_ROOT_RESOLVED="${PROJECT_ROOT}"
if [ -d "${PROJECT_ROOT}" ]; then
  PROJECT_ROOT_RESOLVED="$(cd "${PROJECT_ROOT}" && pwd -P)"
fi
[ "${PROJECT_ROOT_RESOLVED}" != "${MAIN_CHECKOUT}" ] \
  || fail_alert "PROJECT_ROOT is the main checkout ${MAIN_CHECKOUT} (resolved: ${PROJECT_ROOT_RESOLVED}) — forbidden; use a dedicated ~/ta-reports-<sha>"
git -C "${PROJECT_ROOT}" merge-base --is-ancestor "${REQUIRED_DAV2033_COMMIT}" HEAD 2>/dev/null \
  || fail_alert "PROJECT_ROOT ${PROJECT_ROOT} is not a checkout containing DAV-2033 (${REQUIRED_DAV2033_COMMIT})"
[ -x "${PYTHON_BIN}" ] || fail_alert "interpreter not executable: ${PYTHON_BIN}"
[ -f "${PROGRESS_SCRIPT}" ] || fail_alert "missing script: ${PROGRESS_SCRIPT}"
[ -f "${ACCEPT_SCRIPT}" ] || fail_alert "missing script: ${ACCEPT_SCRIPT}"
[ -f "${SNAPSHOT_DB}" ] || fail_alert "snapshot db not found: ${SNAPSHOT_DB}"

mkdir -p "${DAY_DIR}" "${LOG_DIR}" 2>/dev/null || fail_alert "cannot create ${DAY_DIR} / ${LOG_DIR}"
[ -w "${DAY_DIR}" ] || fail_alert "output dir not writable: ${DAY_DIR}"

# 交易日历：10-14 令——缺失即 fail_alert（不再 WARN 放行）。
[ -f "${CALENDAR_JSON}" ] || fail_alert "trading calendar not found: ${CALENDAR_JSON} (set TA_CALENDAR_JSON)"
cal_arg=(--calendar-json "${CALENDAR_JSON}")

# 统一子进程前缀：env -u PYTHONPATH 剥离宿主 PYTHONPATH（venv310 门禁铁律），
# unset 代理（脚本零出网），stdout/stderr 各自落当日日志便于事后取证。
run_py() {
  env -u PYTHONPATH -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY \
      -u HTTPS_PROXY -u ALL_PROXY \
    "${PYTHON_BIN}" "$@"
}

# ---- 1. 进度表（1507）--------------------------------------------------------
PROGRESS_JSON="${DAY_DIR}/exit-progress-${RUN_DATE}.json"
PROGRESS_LOG="${LOG_DIR}/exit-progress-${RUN_DATE}.log"
run_py "${PROGRESS_SCRIPT}" --db "${SNAPSHOT_DB}" --as-of "${RUN_DATE}" \
    "${cal_arg[@]+"${cal_arg[@]}"}" --json > "${PROGRESS_JSON}" 2> "${PROGRESS_LOG}"
progress_rc=$?
[ "${progress_rc}" -eq 0 ] || fail_alert "dav1507_exit_progress.py exit ${progress_rc} (stderr: ${PROGRESS_LOG})"
[ -s "${PROGRESS_JSON}" ] || fail_alert "dav1507_exit_progress.py produced empty ${PROGRESS_JSON}"

# ---- 2. 1950 验收报告 --------------------------------------------------------
ACCEPT_JSON="${DAY_DIR}/acceptance-${RUN_DATE}.json"
ACCEPT_LOG="${LOG_DIR}/acceptance-${RUN_DATE}.log"
run_py "${ACCEPT_SCRIPT}" --db "${SNAPSHOT_DB}" --as-of "${RUN_DATE}" \
    "${cal_arg[@]+"${cal_arg[@]}"}" --json > "${ACCEPT_JSON}" 2> "${ACCEPT_LOG}"
accept_rc=$?
[ "${accept_rc}" -eq 0 ] || fail_alert "dav1950_exit_acceptance_report.py exit ${accept_rc} (stderr: ${ACCEPT_LOG})"
[ -s "${ACCEPT_JSON}" ] || fail_alert "dav1950_exit_acceptance_report.py produced empty ${ACCEPT_JSON}"

# ---- 3. 保留策略：清理 ${RETENTION_DAYS} 天前的 YYYY-MM-DD 目录 -------------
# 只删「名字形如 YYYY-MM-DD 的目录」，正则不匹配的一律跳过（不误删 _logs/ 等）。
pruned=""
if [ -d "${OUT_ROOT}" ]; then
  cutoff_epoch=$(( $(date '+%s') - RETENTION_DAYS * 86400 ))
  for d in "${OUT_ROOT}"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]; do
    [ -d "${d}" ] || continue
    dir_epoch=$(date -j -f '%Y-%m-%d' '+%s' "${d##*/}" 2>/dev/null) || continue
    if [ "${dir_epoch}" -lt "${cutoff_epoch}" ]; then
      rm -rf "${d}" && pruned="${pruned} ${d##*/}"
    fi
  done
fi

# ---- 4. 成功标记 ------------------------------------------------------------
{
  echo "OK ${RUN_TS}"
  echo "progress=${PROGRESS_JSON}"
  echo "acceptance=${ACCEPT_JSON}"
  echo "snapshot=${SNAPSHOT_DB}"
  echo "retention_days=${RETENTION_DAYS}"
  echo "pruned=${pruned:-none}"
} > "${DAY_DIR}/run-status-${STAMP}.txt" || fail_alert "cannot write run-status in ${DAY_DIR}"

echo "[${RUN_TS}] OK progress=${PROGRESS_JSON} acceptance=${ACCEPT_JSON} pruned=${pruned:-none}"
exit 0