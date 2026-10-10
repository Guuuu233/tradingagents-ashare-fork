#!/usr/bin/env bash
# ta-launchd-wrapper.sh — com.tradingagents.ashare 包装脚本（DAV-1790）
#
# 职责：
#   1. 意外退出自动拉起前的节流：同一小时（滑动窗口 3600s）内最多拉起 3 次，
#      超限即 sleep 到窗口外再退出非零——launchd 视为"拉起失败"不再 respawn，
#      等待下一个整窗口或人工 launchctl kickstart。
#   2. 固化生产启动环境（与 releases/logs/launch-<sha>.txt 留档启动令一致：
#      env -i 基线 + DATABASE_URL/代理白名单/TA_* 开关），exec uvicorn 前台运行，
#      让 launchd 直接管到 uvicorn 进程本体。
#
# 占位符（部署时改）：RELEASE_SHA / RELEASES_ROOT / VENV_PYTHON / LOG_DIR。
# 说明：计数文件 $STAMP_DIR/restart-stamps 只在本机运行期有效，
#       机器重启后 launchd 重新 RunAtLoad/kickstart，窗口自然清零。

set -u

# ---- 部署时按当前生产 release 改写（见 releases/RESTART.md） ----
RELEASE_SHA="65988c79"
RELEASES_ROOT="/Users/davidliu/Documents/TradingAgents-AShare-releases"
RELEASE_DIR="${RELEASES_ROOT}/${RELEASE_SHA}"
VENV_PYTHON="/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"
LOG_DIR="${RELEASES_ROOT}/logs"
STAMP_DIR="${RELEASES_ROOT}/logs/.launchd"
STAMP_FILE="${STAMP_DIR}/restart-stamps"

# 限流参数：窗口秒数 / 窗口内允许的最大拉起次数（同一小时最多 3 次）
WINDOW_SEC=3600
MAX_RESTARTS=3

mkdir -p "${STAMP_DIR}" "${LOG_DIR}"

now=$(date +%s)

# 丢掉窗口外的时间戳
if [ -f "${STAMP_FILE}" ]; then
  cutoff=$((now - WINDOW_SEC))
  awk -v c="${cutoff}" 'NF && $1 >= c' "${STAMP_FILE}" > "${STAMP_FILE}.tmp" || true
  mv "${STAMP_FILE}.tmp" "${STAMP_FILE}"
else
  : > "${STAMP_FILE}"
fi

count=$(wc -l < "${STAMP_FILE}" | tr -d ' ')

if [ "${count}" -ge "${MAX_RESTARTS}" ]; then
  oldest=$(head -n1 "${STAMP_FILE}" | tr -d ' ')
  wait_s=$(( oldest + WINDOW_SEC - now + 1 ))
  [ "${wait_s}" -lt 1 ] && wait_s=1
  echo "[$(date '+%F %T')] launchd wrapper: ${WINDOW_SEC}s 内已达 ${MAX_RESTARTS} 次拉起上限，" \
       "sleep ${wait_s}s 到窗口外后退出（不再 respawn，待人工介入）" >&2
  sleep "${wait_s}"
  exit 1
fi

# 记一次拉起
echo "${now}" >> "${STAMP_FILE}"

echo "[$(date '+%F %T')] launchd wrapper: 拉起 uvicorn (release=${RELEASE_SHA}, " \
     "窗口内第 $((count + 1))/${MAX_RESTARTS} 次)" >&2

cd "${RELEASE_DIR}"

# 与 releases/logs/launch-<sha>.txt 完全一致的环境基线
exec env -i \
  HOME=/Users/davidliu USER=davidliu LOGNAME=davidliu SHELL=/bin/zsh \
  PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  TMPDIR="${TMPDIR:-/var/folders/8k/kg3t37vn3_b1jxs_gdbwpk0c0000gn/T/}" \
  DATABASE_URL="sqlite:////Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db" \
  TA_SOCIAL_MODE=disabled APP_VERSION=0.6.0 \
  TA_PRICE_REF_REVISION_ENABLED=1 TA_E04_REVISION_ENABLED=1 \
  http_proxy=http://127.0.0.1:7897 https_proxy=http://127.0.0.1:7897 all_proxy=socks5://127.0.0.1:7897 \
  no_proxy="localhost,127.0.0.1,100.65.130.33,100.67.61.23,100.64.0.0/10,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn" \
  NO_PROXY="localhost,127.0.0.1,100.65.130.33,100.67.61.23,100.64.0.0/10,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn" \
  "${VENV_PYTHON}" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 \
    >> "${LOG_DIR}/uvicorn-${RELEASE_SHA}.log" 2>&1
