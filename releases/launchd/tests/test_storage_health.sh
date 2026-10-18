#!/usr/bin/env bash
# DAV-2215 storage_health.sh 行为门禁（全程临时目录 + PATH 桩，不碰真实磁盘统计、
# 不碰生产数据、不安装 launchd）
# 用法：bash releases/launchd/tests/test_storage_health.sh
#
# 覆盖：
#   1. 语法门：zsh -n 脚本；脚本必须带可执行位（plist 以 exec 调用，644 会 rc=126）；
#      plutil -lint 模板（占位符替换后）+ 占位符存在性；
#   2. 水位三色边界（≥100 绿 / 60–99 黄 / <60 红）：用 PATH 里的假 df 注入
#      「可用 GiB」，因此可稳定覆盖 150/120/100/99/80/60/59 七个点；
#   3. 热备副本预算（DAV-2219 🟢-2 口径：只算 releases + 快照，PROJ 活库不计）：
#      精确 KiB 判定 15 GiB = 15728640 KiB，覆盖「正好 15 GiB 绿」「15 GiB + 1 KiB 红」
#      「(15,16) GiB 窗口红且不截断成 15 GiB」三处；
#   4. du 失败/无输出/非数值（DAV-2219 🟡-1）：不得判绿 —— 热备行须为「未知」、
#      失败原因进报告、退出码非零（本实现为 2）；部分目录不可测同样不判绿；
#   5. 只读纪律静态门：剔除注释/`[[ ]]`/双引号字面量后，不得出现
#      rm/rmdir/unlink/truncate/shred/mv/dd/install/cp、find -delete，
#      也不得有写文件的重定向（只允许 2>/dev/null、>/dev/null、2>&1）；
#   6. 每周基线选样：取日期最大的一份快照、SHA256/字节数与本地 shasum/stat 一致、
#      快照目录缺失或没有匹配文件时报「无可上传基线」且仍以 0 退出（不静默失败）。
#
# 不覆盖（诚实声明）：
#   - 周三自动追加（依赖系统时钟，未引入 faketime）；
#   - quark 上传成功分支（本机无 quark CLI，只验证「无 CLI → 提示人工上传」）；
#   - 真实磁盘水位（不伪造生产磁盘状态，阈值语义靠假 df 覆盖）；
#   - du 的真实 TCC 拒读（用桩 du 模拟 stderr + 空 stdout + 退出 1）。
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHD_DIR="$(dirname "${SCRIPT_DIR}")"
SCRIPT="${LAUNCHD_DIR}/storage_health.sh"
PLIST="${LAUNCHD_DIR}/com.tradingagents.storage-health.plist"
ZSH_BIN="$(command -v zsh || true)"
BASE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

pass=0; fail=0
ok()   { echo "  PASS: $*"; pass=$((pass+1)); }
bad()  { echo "  FAIL: $*"; fail=$((fail+1)); }
check_has()  { if printf '%s' "$1" | grep -qF -- "$2"; then ok "$3"; else bad "$3（未找到：$2）"; fi; }
check_not()  { if printf '%s' "$1" | grep -qF -- "$2"; then bad "$3（不该出现：$2）"; else ok "$3"; fi; }

[ -n "${ZSH_BIN}" ] || { echo "zsh not found; this suite targets the host macOS zsh"; exit 1; }
[ -f "${SCRIPT}" ] || { echo "missing ${SCRIPT}"; exit 1; }
[ -f "${PLIST}" ] || { echo "missing ${PLIST}"; exit 1; }

# ---- 1. 语法门 -------------------------------------------------------------
echo "[1] 语法门"
if "${ZSH_BIN}" -n "${SCRIPT}" 2>"${TMP}/syntax.err"; then ok "zsh -n storage_health.sh"
else bad "zsh -n storage_health.sh: $(cat "${TMP}/syntax.err")"; fi

# 🟢-1：plist 是以 `zsh <script> auto` 直接 exec 脚本体，不是 `zsh -c` 读源码，
# 所以 644 会被拒（zsh: permission denied，rc=126），门禁必须断言可执行位。
if [ -x "${SCRIPT}" ]; then ok "脚本带可执行位（plist 以可执行文件调用）"
else bad "脚本缺可执行位（644 时 plist 调用会 permission denied）"; fi

if command -v plutil >/dev/null 2>&1; then
  # 模板含 {{REPO_DIR}}/{{LOG_DIR}} 占位符：先替换成安全值再 lint，验证 XML 结构；
  # 再显式要求占位符存在（否则装到生产上会静默指向错目录）。
  sed -e "s|{{REPO_DIR}}|${TMP}/repo|g" -e "s|{{LOG_DIR}}|${TMP}/logs|g" "${PLIST}" > "${TMP}/job.plist"
  if plutil -lint "${TMP}/job.plist" >/dev/null 2>&1; then ok "plutil -lint 模板（占位符替换后结构合法）"
  else bad "plutil -lint 模板: $(plutil -lint "${TMP}/job.plist" 2>&1)"; fi
  n_ph=$(grep -oE '\{\{[A-Z_]+\}\}' "${PLIST}" | sort -u | wc -l | tr -d ' ')
  if [ "${n_ph}" -ge 2 ]; then ok "模板保留 ${n_ph} 个安装期占位符（REPO_DIR/LOG_DIR）"
  else bad "模板占位符数量异常：${n_ph}"; fi
  if grep -q '/Users/davidliu' "${PLIST}"; then bad "模板内硬编码了宿主机绝对路径 /Users/davidliu"
  else ok "模板无硬编码宿主机绝对路径（装前必须替换占位符）"; fi
else
  echo "  SKIP: plutil 不存在，跳过 plist 门"
fi

# ---- 桩：df / du -----------------------------------------------------------
SHIM="${TMP}/shim"; mkdir -p "${SHIM}"
cat > "${SHIM}/df" <<'SH'
#!/bin/sh
# 假 df：可用量由 FAKE_AVAIL_GIB 控制，其余列填占位值
avail="${FAKE_AVAIL_GIB:-120}"
if [ "${1:-}" = "-g" ]; then
  printf 'Filesystem     1G-blocks  Used Available Capacity  Mounted on\n'
  printf '/dev/disk3s5         460   320   %s      70%%   /System/Volumes/Data\n' "${avail}"
else
  printf 'Filesystem      Size   Used  Avail Capacity Mounted on\n'
  printf '/dev/disk3s5    460G   320G   %sG    70%%   /System/Volumes/Data\n' "${avail}"
fi
SH
cat > "${SHIM}/du" <<'SH'
#!/bin/sh
# 假 du：目录占用（KiB）由 FAKE_DU_KB（同值默认）与 FAKE_DU_KB_{PROJ,REL,SNAP} 覆盖控制；
# FAKE_DU_FAIL=1 → 所有目录报 TCC 式失败（stderr + 空 stdout + 退出 1）；
# FAKE_DU_FAIL_DIR=<basename> → 只让指定目录失败（部分不可测）。
dir="${2:-.}"
base="$(basename "${dir}")"
fail=0
[ "${FAKE_DU_FAIL:-0}" = "1" ] && fail=1
if [ -n "${FAKE_DU_FAIL_DIR:-}" ] && [ "${FAKE_DU_FAIL_DIR}" = "${base}" ]; then fail=1; fi
if [ "${fail}" = "1" ]; then
  printf 'du: %s: Operation not permitted\n' "${dir}" >&2
  exit 1
fi
kb="${FAKE_DU_KB:-0}"
case "${base}" in
  proj) kb="${FAKE_DU_KB_PROJ:-${kb}}" ;;
  TradingAgents-AShare-releases) kb="${FAKE_DU_KB_REL:-${kb}}" ;;
  snap) kb="${FAKE_DU_KB_SNAP:-${kb}}" ;;
esac
case "${1:-}" in
  -sk) printf '%s\t%s\n' "${kb}" "${dir}" ;;
  -sh|-h) printf '%s\t%s\n' "${FAKE_DU_H:-1K}" "${dir}" ;;
esac
SH
chmod +x "${SHIM}/df" "${SHIM}/du"

# 脚本会把热备计到 PROJ / 同级 releases / SNAP_DIR 三处，三者都造出来
mkdir -p "${TMP}/proj" "${TMP}/snap" "${TMP}/TradingAgents-AShare-releases"

run_daily() {  # $1=avail_gib $2=每目录 du_kb
  env PATH="${SHIM}:${BASE_PATH}" FAKE_AVAIL_GIB="$1" FAKE_DU_KB="$2" FAKE_DU_H="1K" \
      STORAGE_PROJ="${TMP}/proj" TA_SNAPSHOT_DIR="${TMP}/snap" \
      "${ZSH_BIN}" "${SCRIPT}" daily 2>&1
}

run_daily_mix() {  # $1=avail_gib $2=PROJ_KiB $3=REL_KiB $4=SNAP_KiB；$5..=额外 KEY=VAL（可选）
  local a="$1" p="$2" r="$3" s="$4"; shift 4
  env PATH="${SHIM}:${BASE_PATH}" FAKE_AVAIL_GIB="${a}" FAKE_DU_H="1K" \
      FAKE_DU_KB_PROJ="${p}" FAKE_DU_KB_REL="${r}" FAKE_DU_KB_SNAP="${s}" ${1+"$@"} \
      STORAGE_PROJ="${TMP}/proj" TA_SNAPSHOT_DIR="${TMP}/snap" \
      "${ZSH_BIN}" "${SCRIPT}" daily 2>&1
}

# ---- 2. 水位三色（总控 10-17 令：≥100 绿 / 60–99 黄 / <60 红）----------------
# 旧口径是 ≥150 绿 / 120–149 黄 / <120 红；120 与 80 两个点正好区分新旧两套。
echo "[2] 水位三色边界"
for spec in "150:绿" "120:绿" "100:绿" "99:黄" "80:黄" "60:黄" "59:红"; do
  gib="${spec%%:*}"; want="${spec##*:}"
  out="$(run_daily "${gib}" 0)"
  check_has "${out}" "**磁盘水位：${want}**" "可用 ${gib} GiB → ${want}"
done
# 旧阈值（150/120）必须彻底消失：120 GiB 现在是绿而不是黄，80 GiB 现在是黄而不是红
out120="$(run_daily 120 0)"
check_not "${out120}" "**磁盘水位：黄**" "120 GiB 不再被判为黄（旧 150/120 口径已移除）"
out80="$(run_daily 80 0)"
check_not "${out80}" "**磁盘水位：红**" "80 GiB 不再被判为红（旧 120 口径已移除）"

# ---- 3. 热备预算（DAV-2219 🟡-2 精确 KiB；🟢-2 副本口径）-------------------
echo "[3] 热备预算（副本口径 + 精确 KiB）"
# 3a. 边界：releases 7864320 + 快照 7864320 = 15728640 KiB = 正好 15 GiB → 绿
out15="$(run_daily_mix 120 0 7864320 7864320)"
check_has "${out15}" "**热备副本占用：绿**" "releases+快照 = 正好 15 GiB → 绿（含等号边界）"
check_has "${out15}" "15728640 KiB" "报告给出精确 KiB（15 GiB = 15728640 KiB）"
# 3b. 🟡-2 回归点：(15,16) GiB 窗口必须判红，且数字不得再被截断成「15 GiB」
#     8126464×2 = 16252928 KiB = 15.5 GiB；旧实现 16252928/1048576 = 15 → 假绿
out155="$(run_daily_mix 120 0 8126464 8126464)"
check_has "${out155}" "**热备副本占用：红（超预算）**" "(15,16) GiB 窗口的 15.5 GiB → 红"
check_has "${out155}" "15.5 GiB" "15.5 GiB 不再被整数截断成 15"
check_not "${out155}" "合计约 15 GiB" "不再出现「合计约 15 GiB」截断误报"
# 3c. 边界 +1 KiB：7864320 + 7864321 = 15728641 KiB = 15 GiB + 1 KiB → 必须红
out15p="$(run_daily_mix 120 0 7864320 7864321)"
check_has "${out15p}" "**热备副本占用：红（超预算）**" "15 GiB + 1 KiB → 红（不多算也不放过）"
check_has "${out15p}" "15728641 KiB" "越界 1 KiB 在报告中原样可见"
# 3d. 明显超预算仍红：9000000×2 = 18000000 KiB ≈ 17.2 GiB
out17="$(run_daily_mix 120 0 9000000 9000000)"
check_has "${out17}" "**热备副本占用：红（超预算）**" "≈17.2 GiB → 红（超预算）"
# 3e. 🟢-2：PROJ（工作树/活库）不计入预算 —— 28.6 GiB 活库 + 极小副本 → 仍绿，且单列展示
outproj="$(run_daily_mix 120 30000000 1000 1000)"
check_has "${outproj}" "**热备副本占用：绿**" "PROJ 28.6 GiB 不计预算 → 副本仍绿（不再长期假红）"
check_has "${outproj}" "工作树/活库（含 data/tradingagents.db，不计入热备预算）：28.6 GiB" "PROJ 单列并标注不计预算"

# ---- 4. du 失败/不可测（DAV-2219 🟡-1：不得判绿）----------------------------
echo "[4] du 失败不得判绿（🟡-1）"
outfail="$(run_daily_mix 120 0 0 0 FAKE_DU_FAIL=1)"; rc_fail=$?
check_has "${outfail}" "**热备副本占用：未知 ⚠️**" "三个目录全不可测 → 热备行判「未知」"
check_not "${outfail}" "**热备副本占用：绿**" "不可测时绝不出现热备绿灯"
check_has "${outfail}" "Operation not permitted" "du 的失败原因（stderr）被收进报告留证"
check_has "${outfail}" "**磁盘水位：绿**" "测量失败时报告仍然产出（不是静默失败）"
check_has "${outfail}" "3 个目录不可测" "报告注明不可测目录数量"
if [ "${rc_fail}" -ne 0 ]; then ok "不可测时退出码非零（rc=${rc_fail}）"; else bad "不可测时退出码仍为 0"; fi

# 部分不可测（仅快照目录失败）同样不得判绿
outpart="$(run_daily_mix 120 1000 1000 0 FAKE_DU_FAIL_DIR=snap)"; rc_part=$?
check_has "${outpart}" "**热备副本占用：未知 ⚠️**" "仅快照目录不可测 → 预算判定不成立（不判绿）"
check_has "${outpart}" "⚠️ 不可测：${TMP}/snap" "点名不可测的具体目录"
check_has "${outpart}" "工作树/活库" "其余目录仍照常给出测量值"
if [ "${rc_part}" -eq 2 ]; then ok "不可测时退出码为专门的 2（rc=${rc_part}）"; else bad "不可测退出码应为 2，实得 ${rc_part}"; fi

# ---- 5. 只读纪律静态门 -----------------------------------------------------
# 先剔行首注释，再剔 `[[ ... ]]` 比较与双引号字面量（它们不是重定向），
# 余下才按真实 shell 语法判危险命令 / 写文件。
echo "[5] 只读纪律静态门"
CODE="${TMP}/code.sh"
grep -vE '^[[:space:]]*#' "${SCRIPT}" | sed -E 's/\[\[[^]]*\]\]//g; s/"[^"]*"//g' > "${CODE}"

if hits="$(grep -nE '(^|[[:space:];&|(])(rm|rmdir|unlink|truncate|shred|mv|dd|install|cp)([[:space:]]|$)' "${CODE}")"; then
  bad "出现删除/移动/安装类命令：$(printf '%s' "${hits}" | head -3)"
else ok "无 rm/rmdir/unlink/truncate/shred/mv/dd/install/cp"; fi

if hits="$(grep -n -- '-delete' "${CODE}")"; then
  bad "find 出现 -delete：$(printf '%s' "${hits}" | head -3)"
else ok "无 find -delete"; fi

# 只允许 /dev/null 丢弃与 2>&1 合并；其余 > >> 视为脚本在写文件
stripped="$(sed -E 's#[0-9]?>>?[[:space:]]*/dev/null##g; s#2>&1##g' "${CODE}")"
if hits="$(printf '%s\n' "${stripped}" | grep -n '>')"; then
  bad "非注释行存在写文件重定向：$(printf '%s' "${hits}" | head -3)"
else ok "无非 /dev/null 的文件重定向（脚本自身不落盘）"; fi

# 脚本不得自带 SQL/HTTP/通知通道（否则「只读」可被绕过）
if hits="$(grep -nE '(^|[[:space:];&|(])(sqlite3|curl|wget|tee|osascript|python|python3)([[:space:]]|$)' "${CODE}")"; then
  bad "出现 SQL/HTTP/通知类命令：$(printf '%s' "${hits}" | head -3)"
else ok "无 sqlite3/curl/wget/tee/osascript/python"; fi

# ---- 6. 每周基线选样 -------------------------------------------------------
echo "[6] 每周基线选样"
SNAPDIR="${TMP}/snap-weekly"; mkdir -p "${SNAPDIR}"
for d in 2026-10-12 2026-10-13 2026-10-14; do
  printf 'fake-snapshot-%s\n' "${d}" > "${SNAPDIR}/tradingagents-${d}.db.zst"
done
printf 'not-a-date\n' > "${SNAPDIR}/tradingagents-notadate.db.zst"
printf 'other\n' > "${SNAPDIR}/unrelated.txt"

run_weekly() {  # $1=快照目录
  env PATH="${SHIM}:${BASE_PATH}" TA_SNAPSHOT_DIR="$1" STORAGE_PROJ="${TMP}/proj" \
      "${ZSH_BIN}" "${SCRIPT}" weekly 2>&1
}
outw="$(run_weekly "${SNAPDIR}")"
want_sha="$(shasum -a 256 "${SNAPDIR}/tradingagents-2026-10-14.db.zst" | awk '{print $1}')"
want_bytes="$(stat -f %z "${SNAPDIR}/tradingagents-2026-10-14.db.zst")"
check_has "${outw}" "当周最后一份：\`${SNAPDIR}/tradingagents-2026-10-14.db.zst\`" "取日期最大的一份（10-14）"
check_not "${outw}" "tradingagents-2026-10-13.db.zst\`" "未误选较早的 10-13 快照"
check_not "${outw}" "tradingagents-notadate.db.zst\`" "跳过不符合日期格式的文件"
check_has "${outw}" "SHA256：${want_sha}" "SHA256 与本地 shasum 一致"
check_has "${outw}" "（${want_bytes} 字节）" "字节数与本地 stat 一致"
check_has "${outw}" "本机无 quark CLI，请人工上传" "无 quark CLI 时提示人工上传（不静默失败）"
check_has "${outw}" "云端超限清理：脚本不动云端" "云端清理仍是人工动作（脚本不删）"

EMPTY="${TMP}/snap-empty"; mkdir -p "${EMPTY}"
oute="$(run_weekly "${EMPTY}")"; rc_e=$?
check_has "${oute}" "本周无可上传基线" "空快照目录 → 明确报「无可上传」"
if [ "${rc_e}" -eq 0 ]; then ok "空快照目录退出码 0（缺数据不算故障）"; else bad "空快照目录退出码 ${rc_e}"; fi

outm="$(run_weekly "${TMP}/no-such-snap")"; rc_m=$?
check_has "${outm}" "快照目录不存在" "快照目录缺失 → 明确报「目录不存在」"
if [ "${rc_m}" -eq 0 ]; then ok "快照目录缺失退出码 0"; else bad "快照目录缺失退出码 ${rc_m}"; fi

# ---- 收尾 ------------------------------------------------------------------
echo
echo "pass=${pass} fail=${fail}"
[ "${fail}" -eq 0 ] || exit 1
