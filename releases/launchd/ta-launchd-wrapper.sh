#!/usr/bin/env bash
# ta-launchd-wrapper.sh — com.tradingagents.ashare 包装脚本（DAV-1790，DAV-1800 返修）
#
# 职责：
#   1. 拉起节流：同一小时（滑动窗口 3600s）内最多拉起 3 次。超限即以 0 退出码
#      快速失败（不 sleep、不拉起），配合 plist KeepAlive={"SuccessfulExit":false}：
#      非零退出/信号退出（含 SIGKILL、OOM）由 launchd 重拉；限流停手走 exit 0，
#      视为"成功退出"不再 respawn，等窗口过后由 StartInterval 周期探测自动恢复，
#      或人工 launchctl kickstart / TA_SKIP_THROTTLE=1 旁路。
#   2. 固化生产启动环境（env -i 基线 + DATABASE_URL/代理白名单/TA_* 开关，
#      与 releases/logs/launch-<sha>.txt 留档启动令 15/16 键逐字一致，
#      唯一差异 TMPDIR 改字面量，见下），exec uvicorn 前台运行，
#      让 launchd 直接管到 uvicorn 进程本体（bash 被原地替换，实测）。
#   3. fail-closed：计数目录/文件不可写、写戳失败、锁拿不到，一律错误日志 +
#      exit 1 拒绝拉起（宁可停服也不无限重启；该 exit 1 会被 KeepAlive 重拉，
#      由 ThrottleInterval=10 兜底避免高速死循环）。
#
# 占位符（部署时改）：RELEASE_SHA / RELEASES_ROOT / VENV_PYTHON / LOG_DIR。
# 说明：计数文件 $STAMP_DIR/restart-stamps 只在本机运行期有效，
#       机器重启后窗口自然过期清零；但 3600s 内反复崩溃重启会跨机器重启累计。

set -u

# ---- 部署时按当前生产 release 改写（见 releases/RESTART.md，宿主机侧文件，见 LAUNCHD_SWITCH.md） ----
RELEASE_SHA="65988c79"
RELEASES_ROOT="/Users/davidliu/Documents/TradingAgents-AShare-releases"
RELEASE_DIR="${RELEASES_ROOT}/${RELEASE_SHA}"
VENV_PYTHON="/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"
LOG_DIR="${RELEASES_ROOT}/logs"
STAMP_DIR="${RELEASES_ROOT}/logs/.launchd"
STAMP_FILE="${STAMP_DIR}/restart-stamps"
LOCK_DIR="${STAMP_DIR}/restart-stamps.lock"   # mkdir 原子锁（macOS 无 flock）
DIAG_LOG="${LOG_DIR}/wrapper-diagnostic.log"  # 限流/失败诊断独立落点（追加式，轮转见 LAUNCHD_SWITCH.md）

# 限流参数：窗口秒数 / 窗口内允许的最大拉起次数（同一小时最多 3 次）
WINDOW_SEC=3600
MAX_RESTARTS=3

ts() { date '+%F %T'; }
# 诊断：fd2（→ launchd-stderr.log）+ 独立落盘，双写保证 launchd 视图与文件都可查
diag() {
  local msg="[$(ts)] launchd wrapper: $*"
  echo "${msg}" >&2
  echo "${msg}" >> "${DIAG_LOG}" 2>/dev/null || true
}
die() { diag "FATAL: $*（拒绝拉起，exit 1）"; exit 1; }

# ---- 人工旁路：launchctl kickstart -e 注入 TA_SKIP_THROTTLE=1 可跳过限流计数 ----
SKIP_THROTTLE="${TA_SKIP_THROTTLE:-0}"

# ---- 前置断言（fail-closed）----
mkdir -p "${STAMP_DIR}" "${LOG_DIR}" 2>/dev/null \
  || die "无法创建 ${STAMP_DIR} 或 ${LOG_DIR}（检查权限/磁盘）"
[ -w "${STAMP_DIR}" ] || die "${STAMP_DIR} 不可写"
[ -w "${LOG_DIR}" ]   || die "${LOG_DIR} 不可写"

now=$(date +%s)
cutoff=$((now - WINDOW_SEC))
locked=0   # 是否持有原子锁（skip-throttle 时不持锁，也不得释放他人锁）

if [ "${SKIP_THROTTLE}" = "1" ]; then
  diag "TA_SKIP_THROTTLE=1：跳过限流计数（人工旁路），直接拉起"
  count=0
else
  # ---- 原子锁：mkdir 原子操作，防 kickstart 与自动重拉并发双写丢戳 ----
  i=0
  while [ "${i}" -lt 50 ]; do
    if mkdir "${LOCK_DIR}" 2>/dev/null; then locked=1; break; fi
    sleep 0.1
    i=$((i + 1))
  done
  if [ "${locked}" != "1" ]; then
    # 可能残留死锁目录（持有者被 kill -9）：>60s 的旧锁视为死锁清除重试一次
    if [ -d "${LOCK_DIR}" ]; then
      lock_mtime=$(stat -f %m "${LOCK_DIR}" 2>/dev/null || echo 0)
      if [ $((now - lock_mtime)) -gt 60 ] && rmdir "${LOCK_DIR}" 2>/dev/null; then
        mkdir "${LOCK_DIR}" 2>/dev/null && locked=1
      fi
    fi
    [ "${locked}" = "1" ] || die "限流锁 ${LOCK_DIR} 5s 内拿不到（疑似并发卡死）"
  fi
  trap 'rmdir "${LOCK_DIR}" 2>/dev/null || true' EXIT

  # 丢掉窗口外的时间戳（单写者在锁内，读改写安全）
  if [ -f "${STAMP_FILE}" ]; then
    awk -v c="${cutoff}" 'NF && $1 >= c' "${STAMP_FILE}" > "${STAMP_FILE}.tmp" \
      || die "清理过期戳失败 ${STAMP_FILE}"
    mv "${STAMP_FILE}.tmp" "${STAMP_FILE}" \
      || die "替换戳文件失败 ${STAMP_FILE}"
  else
    : > "${STAMP_FILE}" 2>/dev/null || die "计数文件不可写 ${STAMP_FILE}"
  fi

  count=$(wc -l < "${STAMP_FILE}" | tr -d ' ')
  case "${count}" in ''|*[!0-9]*) die "戳计数异常 count='${count}'";; esac

  if [ "${count}" -ge "${MAX_RESTARTS}" ]; then
    oldest=$(head -n1 "${STAMP_FILE}" | tr -d ' ')
    recover_in=$(( oldest + WINDOW_SEC - now ))
    [ "${recover_in}" -lt 0 ] && recover_in=0
    diag "${WINDOW_SEC}s 窗口内已达 ${MAX_RESTARTS} 次拉起上限，exit 0 停手" \
         "（不拉起、不 sleep）。恢复方式：约 ${recover_in}s 后窗口过期，" \
         "StartInterval 周期探测自动重拉；或人工 launchctl kickstart /" \
         "TA_SKIP_THROTTLE=1 kickstart -e 旁路。请检查 uvicorn 崩溃根因。"
    exit 0
  fi

  # 记一次拉起（含计划内首启动——额度语义见 LAUNCHD_SWITCH.md）
  echo "${now}" >> "${STAMP_FILE}" 2>/dev/null || die "写戳失败 ${STAMP_FILE}"
fi

diag "拉起 uvicorn (release=${RELEASE_SHA}, 窗口内第 $((count + 1))/${MAX_RESTARTS} 次)"

# 即将 exec：释放锁并清 trap（EXIT trap 在 exec 上不触发，必须显式释放；
# 仅在自己持锁时释放，skip-throttle 路径不得动他人锁）
if [ "${locked}" = "1" ]; then
  rmdir "${LOCK_DIR}" 2>/dev/null || true
  locked=0
fi
trap - EXIT

# ---- 启动前置断言（fail-closed）----
[ -d "${RELEASE_DIR}" ] || die "release 目录不存在 ${RELEASE_DIR}"
[ -x "${VENV_PYTHON}" ] || die "解释器不可执行 ${VENV_PYTHON}"
cd "${RELEASE_DIR}" || die "cd ${RELEASE_DIR} 失败"

# 环境基线：与 releases/logs/launch-<sha>.txt 比对 15/16 键逐字一致；
# 唯一差异 TMPDIR —— 留档启动令本身为字面量，此处同取字面量（launchd 下
# ${TMPDIR:-…} 会取 launchd 私有目录，属环境污染，已按复审订正为字面量）。
exec env -i \
  HOME=/Users/davidliu USER=davidliu LOGNAME=davidliu SHELL=/bin/zsh \
  PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  TMPDIR=/var/folders/8k/kg3t37vn3_b1jxs_gdbwpk0c0000gn/T/ \
  DATABASE_URL="sqlite:////Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db" \
  TA_SOCIAL_MODE=disabled APP_VERSION=0.6.0 \
  TA_PRICE_REF_REVISION_ENABLED=1 TA_E04_REVISION_ENABLED=1 \
  http_proxy=http://127.0.0.1:7897 https_proxy=http://127.0.0.1:7897 all_proxy=socks5://127.0.0.1:7897 \
  no_proxy="localhost,127.0.0.1,100.65.130.33,100.67.61.23,100.64.0.0/10,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn" \
  NO_PROXY="localhost,127.0.0.1,100.65.130.33,100.67.61.23,100.64.0.0/10,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn" \
  "${VENV_PYTHON}" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 \
    >> "${LOG_DIR}/uvicorn-${RELEASE_SHA}.log" 2>&1
