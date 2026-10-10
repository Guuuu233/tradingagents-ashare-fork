#!/usr/bin/env bash
# ta-launchd-wrapper.sh — com.tradingagents.ashare 包装脚本
# （DAV-1790，DAV-1800/DAV-1802 返修，DAV-1806 三轮返修）
#
# 职责：
#   1. 拉起节流：同一小时（滑动窗口 3600s）内最多拉起 3 次，**成功与失败路径
#      同等计入**（DAV-1802 🔴-1：戳记录提前到所有前置断言之前，logs/ 不可写
#      时退化兜底戳目录）。超限即打诊断日志并以 exit 0 快速停手（不 sleep、
#      不拉起）——exit 0 在 KeepAlive={"SuccessfulExit":false} 下属"成功退出"
#      不再 respawn，由 ThrottleInterval=10s 兜底的 launchd 重拉也随之停止；
#      等窗口过期由 StartInterval 周期探测自动恢复，或人工哨兵文件旁路（见下）。
#   2. 固化生产启动环境（env -i 基线 + DATABASE_URL/代理白名单/TA_* 开关，
#      与 releases/logs/launch-<sha>.txt 留档启动令 16/16 键逐字一致，
#      TMPDIR 取字面量，见下），exec uvicorn 前台运行，
#      让 launchd 直接管到 uvicorn 进程本体（bash 被原地替换，实测）。
#   3. fail-closed + 有界性不变量（DAV-1806 收口）：**任何 exit 1 出口之前
#      唤起计数必然已完成**——计数段内部的所有失败（戳文件不可写/清窗失败/
#      计数异常/写戳失败）一律以 exit 0 停手（见 fail_closed()：记账设施坏
#      ≠ 服务崩，让 launchd 重拉毫无意义且必然无界）；release 目录/解释器/
#      日志目录缺失等 die 路径在写戳之后发生，KeepAlive 重拉 ≤2 次即触顶
#      exit 0——"同一小时最多 3 次"对成功与失败路径同样成立。
#   4. DAV-1809 四轮返修：旁路哨兵路径（skip_throttle=1）下 release_lock 的
#      ${locked}/${lock_dir} 引用加 :- 兜底，修 set -u unbound variable 崩溃。
#
# 占位符（部署时改）：RELEASE_SHA / RELEASES_ROOT / VENV_PYTHON / LOG_DIR。
# 说明：计数文件 <stamp_dir>/restart-stamps 只在本机运行期有效，
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

# DAV-1802 🔴-1：logs/ 不可写时的兜底戳/锁目录（与主戳目录同结构、同窗口语义）。
# DAV-1806 🟡-1 修订：路径带实例命名空间（${UID} + release SHA），防同机多实例
# 串扰与同用户第三方路径预占/篡改；探测时拒绝符号链接（🟡-1：不跟随预置
# symlink，避免限流状态被写到非预期位置或被外部劫持）。
# TMPDIR 在 launchd 下是 launchd 私有目录且每次唤起不同，故取 /tmp 字面量。
FALLBACK_STAMP_DIR="/tmp/ta-launchd-die-stamps-${UID}-$(basename "${RELEASE_SHA}")"

# DAV-1802 🔴-2：人工旁路哨兵文件（替代 macOS 27.x 不存在的 launchctl kickstart -e）。
# 存在即跳过限流计数直接拉起（一次性：wrapper 读取后删除）。创建/删除命令见
# LAUNCHD_SWITCH.md。注意：哨兵只影响本次唤起——若拉起后再次崩溃仍走正常限流。
BYPASS_FILE="${LOG_DIR}/.launchd/manual-bypass"

# 限流参数：窗口秒数 / 窗口内允许的最大拉起次数（同一小时最多 3 次）
WINDOW_SEC=3600
MAX_RESTARTS=3

ts() { date '+%F %T'; }

# 诊断：fd2（→ launchd-stderr.log）+ DIAG_LOG 落盘双写，保证 launchd 视图与
# 文件都可查。DAV-1806 🟡-3：DIAG_LOG 写不进时（logs/ 不可写正是最需要留证的
# 场景）再退化写到兜底戳目录的 diag-fallback.log；连兜底也写不进只剩 fd2。
# 两个写点均收敛 stderr（不裸奔），且兜底目录可能尚不存在（探测在后面），先 mkdir。
diag() {
  local msg="[$(ts)] launchd wrapper: $*"
  echo "${msg}" >&2
  { echo "${msg}" >> "${DIAG_LOG}"; } 2>/dev/null || {
    mkdir -p "${FALLBACK_STAMP_DIR}" 2>/dev/null
    { echo "${msg}" >> "${FALLBACK_STAMP_DIR}/diag-fallback.log"; } 2>/dev/null || true
  }
}

# fail_closed：计数设施自身故障（戳文件不可写/清窗失败/计数异常/写戳失败）的
# 唯一出口（DAV-1806 🔴-1）。fail-closed 语义不变——仍然拒绝拉起；但**必须
# exit 0 而非 exit 1**：这些路径尚未写戳，exit 1 会被 KeepAlive/ThrottleInterval
# 无限重拉而限流器永远喂不到数据（实测戳文件 chmod 000 时 75s 内 8 次无界
# 重拉）；exit 0 属"成功退出"不再 respawn，仅 StartInterval 周期探测，有界。
fail_closed() {
  diag "FATAL: $*（拒绝拉起，exit 0 停手——计数设施故障下 exit 1 会造成" \
       "无界重拉；仅 ${WINDOW_SEC}s 后窗口过期或 StartInterval 探测恢复）"
  exit 0
}

# die：写戳完成后所有失败出口的唯一通道。到达此处前唤起计数必然已完成
# （戳已写），exit 1 被 KeepAlive 重拉 ≤2 次后限流触顶 exit 0 停手。
# 兜底中的兜底：若连兜底戳目录都不可用（stamp_dir=""），说明本次唤起未被
# 计数，此时 exit 1 会无界重拉——改用 exit 0（KeepAlive 不再重拉，仅
# StartInterval 周期探测）换取有界性，并在诊断日志显式说明。
die() {
  diag "FATAL: $*（拒绝拉起）"
  if [ -z "${stamp_dir}" ]; then
    diag "FATAL: 兜底戳目录 ${FALLBACK_STAMP_DIR} 亦不可用，本次唤起未计数；" \
         "为守『同一小时最多 ${MAX_RESTARTS} 次』硬约束改以 exit 0 停手（不再重拉）"
    exit 0
  fi
  diag "exit 1（本次唤起已计数 ${count}/${MAX_RESTARTS}；KeepAlive 重拉触顶即停）"
  exit 1
}

# release_lock：锁目录唯一释放点（DAV-1806 🟢 收敛）。EXIT trap 与 exec 前
# 显式释放共用；exec 路径 trap 不触发，故靠显式调用。
# DAV-1809 🔴-1：旁路哨兵路径（skip_throttle=1）下计数段被跳过，locked/lock_dir
# 从未赋值；release_lock 在 set -u 下对 ${locked} 求值即 unbound variable，exec 前
# 即崩（实测哨兵消费后 rc=1、uvicorn 未拉起）。两处引用均加 :- 兜底——旁路路径
# 本就无锁可释，兜底后函数安全空转。
release_lock() {
  [ "${locked:-0}" = "1" ] && rmdir "${lock_dir:-}" 2>/dev/null; locked=0; true
}

# ---- 唤起计数（DAV-1802 🔴-1：提前到一切前置断言之前，成功/失败路径同等生效）----
# 每次 launchd 唤起（bootstrap/kickstart/KeepAlive 重拉/StartInterval 探测）
# 先在此处写戳再谈其他；写戳之后任何 die 都已被计入额度。
now=$(date +%s)
cutoff=$((now - WINDOW_SEC))
stamp_dir=""          # 实际生效的戳目录；"" = 连兜底都不可用
using_fallback=0      # 1 = 本次落在兜底戳目录
skip_throttle=0       # 1 = 哨兵文件旁路（跳过计数与上限判定）

# 探测戳目录：主目录可建可写 → 用之；否则退化兜底目录（🟡-1：兜底拒绝符号链接）
if mkdir -p "${STAMP_DIR}" 2>/dev/null && [ -w "${STAMP_DIR}" ]; then
  stamp_dir="${STAMP_DIR}"
elif [ ! -L "${FALLBACK_STAMP_DIR}" ] \
  && mkdir -p "${FALLBACK_STAMP_DIR}" 2>/dev/null && [ -w "${FALLBACK_STAMP_DIR}" ]; then
  stamp_dir="${FALLBACK_STAMP_DIR}"
  using_fallback=1
fi

# DAV-1806 🔴-1（方案 2）：戳**文件**级探测——目录可写不等于文件可写。
# 戳文件已存在但不可读（awk 清窗打不开）或不可写（追加戳失败）时，留在该
# 目录只会让计数段在 fail_closed 处无计数停手；此刻兜底目录通常仍可用，
# 整体降级过去让限流器在兜底继续正常计数，而不是裸奔。兜底同时坏则维持
# stamp_dir=""，由下方 die 的 exit 0 停手兜底（🔴-2）。
if [ -n "${stamp_dir}" ] && [ -f "${stamp_dir}/restart-stamps" ] \
  && { [ ! -r "${stamp_dir}/restart-stamps" ] || [ ! -w "${stamp_dir}/restart-stamps" ]; }; then
  diag "WARN: 戳文件 ${stamp_dir}/restart-stamps 存在但不可读/不可写，" \
       "尝试整体降级兜底戳目录后继续限流（不降级则只能无计数停手）"
  if [ "${stamp_dir}" != "${FALLBACK_STAMP_DIR}" ] \
    && [ ! -L "${FALLBACK_STAMP_DIR}" ] \
    && mkdir -p "${FALLBACK_STAMP_DIR}" 2>/dev/null && [ -w "${FALLBACK_STAMP_DIR}" ]; then
    stamp_dir="${FALLBACK_STAMP_DIR}"
    using_fallback=1
  else
    stamp_dir=""
  fi
fi

if [ -f "${BYPASS_FILE}" ]; then
  # 人工旁路（DAV-1802 🔴-2）：哨兵存在即跳过限流，一次性消费后删除。
  # launchctl kickstart 不能注入 env，故用受控文件；创建即授权，不验内容。
  skip_throttle=1
  rm -f "${BYPASS_FILE}" 2>/dev/null || true
fi

if [ -n "${stamp_dir}" ] && [ "${skip_throttle}" != "1" ]; then
  stamp_file="${stamp_dir}/restart-stamps"
  lock_dir="${stamp_dir}/restart-stamps.lock"
  locked=0

  # mkdir 原子锁（macOS 无 flock）：防 kickstart 与自动重拉并发双写丢戳。
  # 拿不到锁不算"额度内唤起"——持有者必在 5s 内退出（临界区只有文件操作），
  # 等锁失败说明并发卡死或死锁残留，此时**不抢额度**直接 exit 1，由对方戳生效。
  i=0
  while [ "${i}" -lt 50 ]; do
    if mkdir "${lock_dir}" 2>/dev/null; then locked=1; break; fi
    sleep 0.1
    i=$((i + 1))
  done
  if [ "${locked}" != "1" ] && [ -d "${lock_dir}" ]; then
    # >60s 旧锁视为死锁残留（持有者被 kill -9），清除重试一次
    lock_mtime=$(stat -f %m "${lock_dir}" 2>/dev/null || echo 0)
    if [ $((now - lock_mtime)) -gt 60 ] && rmdir "${lock_dir}" 2>/dev/null; then
      mkdir "${lock_dir}" 2>/dev/null && locked=1
    fi
  fi
  if [ "${locked}" != "1" ]; then
    diag "FATAL: 限流锁 ${lock_dir} 5s 内拿不到（疑似并发卡死），exit 1" \
         "（不计额度——持有者戳仍有效；KeepAlive 重拉时锁应已释放）"
    exit 1
  fi
  trap 'release_lock' EXIT

  # 清窗口外戳（锁内单写者，读改写安全）。
  # DAV-1806 🟡-2：awk stderr 收进 diag() 统一格式（不再裸奔到
  # launchd-stderr.log 干扰巡检 grep），失败分支清理残留 .tmp。
  # DAV-1809 🟢：重定向纳入 { } 命令块——.tmp 为目录等重定向失败原本由 bash
  # 直接打 stderr、awk_err 捕获不到只能显示「未知」；现在连重定向错误也收进
  # awk_err，诊断信息完整。
  if [ -f "${stamp_file}" ]; then
    awk_err=$({ awk -v c="${cutoff}" 'NF && $1 >= c' "${stamp_file}" \
      > "${stamp_file}.tmp"; } 2>&1) \
      && mv "${stamp_file}.tmp" "${stamp_file}" \
      || { rm -f "${stamp_file}.tmp" 2>/dev/null; \
           fail_closed "清理/替换过期戳失败 ${stamp_file}（awk: ${awk_err:-未知}）"; }
  else
    : > "${stamp_file}" 2>/dev/null \
      || fail_closed "计数文件不可写 ${stamp_file}"
  fi

  count=$(wc -l < "${stamp_file}" | tr -d ' ')
  case "${count}" in
    ''|*[!0-9]*) fail_closed "戳计数异常 count='${count}'" ;;
  esac

  if [ "${count}" -ge "${MAX_RESTARTS}" ]; then
    oldest=$(head -n1 "${stamp_file}" | tr -d ' ')
    recover_in=$(( oldest + WINDOW_SEC - now ))
    [ "${recover_in}" -lt 0 ] && recover_in=0
    diag "${WINDOW_SEC}s 窗口内已达 ${MAX_RESTARTS} 次拉起上限，exit 0 停手" \
         "（不拉起、不 sleep）。恢复方式：约 ${recover_in}s 后窗口过期，" \
         "StartInterval 周期探测自动重拉；或人工创建旁路哨兵 ${BYPASS_FILE}" \
         "后 launchctl kickstart。请检查 uvicorn 崩溃根因。"
    exit 0
  fi

  # 记一次唤起（含计划内首启动、KeepAlive 重拉、StartInterval 周期探测、
  # 人工 kickstart——额度语义见 LAUNCHD_SWITCH.md；本行之后才允许 die）
  echo "${now}" >> "${stamp_file}" 2>/dev/null \
    || fail_closed "写戳失败 ${stamp_file}"
  count=$((count + 1))
elif [ -n "${stamp_dir}" ]; then
  count=0   # skip_throttle=1：旁路唤起，不报额度
else
  count=0   # 无戳路径：下方 [ -n "${stamp_dir}" ] || die 已阻断，不可能到达 exec
fi

if [ "${using_fallback}" = "1" ]; then
  diag "WARN: ${STAMP_DIR} 不可用，本次唤起计数落入兜底戳 ${FALLBACK_STAMP_DIR}" \
       "（logs/ 恢复可写后自动回主戳；兜底戳同样受 ${MAX_RESTARTS} 次/${WINDOW_SEC}s 限流）"
fi
if [ "${skip_throttle}" = "1" ]; then
  diag "旁路哨兵 ${BYPASS_FILE} 存在：跳过限流计数与上限判定，直接拉起（一次性，哨兵已消费删除）"
fi

# ---- 前置断言（fail-closed；此处及之后所有 die 均已被计数）----
# DAV-1806 🔴-2：无戳目录 = 本次唤起未计数，继续走到 exec 会让进程以 uvicorn
# 退出码退出、被 launchd 无界重拉（实测 75s 内 8 次「第 0/3 次」）——
# 限流设施不可用就不该拉起服务，fail-closed 本来含义，必须 die 阻断。
[ -n "${stamp_dir}" ] || die "主/兜底戳目录均不可用（本次唤起未计数，fail-closed 不拉起）"
[ -d "${RELEASE_DIR}" ] || die "release 目录不存在 ${RELEASE_DIR}"
[ -x "${VENV_PYTHON}" ] || die "解释器不可执行 ${VENV_PYTHON}"
# LOG_DIR 需在 exec 前可写（uvicorn 日志落点）；不可写时兜底戳已在计数
mkdir -p "${LOG_DIR}" 2>/dev/null || die "无法创建 ${LOG_DIR}（检查权限/磁盘）"
[ -w "${LOG_DIR}" ] || die "${LOG_DIR} 不可写"
cd "${RELEASE_DIR}" || die "cd ${RELEASE_DIR} 失败"

diag "拉起 uvicorn (release=${RELEASE_SHA}, 窗口内第 ${count}/${MAX_RESTARTS} 次$([ "${skip_throttle}" = "1" ] && echo ', 旁路'))"

# 即将 exec：显式释放锁并清 trap（EXIT trap 在 exec 上不触发）
release_lock
trap - EXIT

# 环境基线：与 releases/logs/launch-<sha>.txt 比对 16/16 键逐字一致；
# TMPDIR 取字面量（launchd 下 ${TMPDIR:-…} 会取 launchd 私有目录，属环境污染）。
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
