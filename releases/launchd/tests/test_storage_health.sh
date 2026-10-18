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
#   3. 热备预算（DAV-2238 口径，按 DAV-2237 §10 签字）：预算项恰为三项单列，
#      `backups/` + `~/ta-snapshot/*.db.zst`（多文件累加）+ 发布期 `.pre-slim` 副本；
#      活库（PROJ 内 data/tradingagents.db）与快照目录里的 current.db 只列示、不进合计；
#      精确 KiB 判定 15 GiB = 15728640 KiB，覆盖「正好 15 GiB 绿」「15 GiB + 1 KiB 红」
#      「(15,16) GiB 窗口红且不截断成 15 GiB」三处；
#   4. du 失败/无输出/非数值（DAV-2238 fail-closed）：不得判绿 —— 热备行须为「红
#      （不可测，fail-closed）」、失败原因进报告、退出码非零（本实现为 2）；
#      部分测量项不可测（含非预算项的 PROJ）同样判红；
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
# 假 du：占用（KiB）按参数 basename 归类，取 FAKE_DU_KB_<类别>（未设则用 FAKE_DU_KB，默认 0）；
#   类别：proj / backups / zst(*.db.zst) / preslim(*.pre-slim*) / current(current.db) / other；
# 每个参数各打印一行（与真 du 一致）；文件集合类项因此会被调用方累加。
# FAKE_DU_FAIL=1 → 全部报 TCC 式失败（stderr + 空 stdout + 退出 1）；
# FAKE_DU_FAIL_ITEM=<类别|other> → 只让该类别失败；FAKE_DU_FAIL_DIR=<basename> 同效（按名字）。
# FAKE_DU_EMPTY=1 → 静默：输出为空但退出码 0（另一类「测不到」）。
rc=0
for arg in "$@"; do
  case "${arg}" in -*) continue ;; esac   # 跳过选项（-sk / -h），只对路径做归类
  base="$(basename "${arg}")"
  case "${base}" in
    proj) cat=proj ;;
    backups) cat=backups ;;
    current.db) cat=current ;;
    *.pre-slim*) cat=preslim ;;
    *.db.zst) cat=zst ;;
    *) cat=other ;;
  esac
  case "${cat}" in
    proj) kb="${FAKE_DU_KB_PROJ:-${FAKE_DU_KB:-0}}" ;;
    backups) kb="${FAKE_DU_KB_BACKUPS:-${FAKE_DU_KB:-0}}" ;;
    zst) kb="${FAKE_DU_KB_ZST:-${FAKE_DU_KB:-0}}" ;;
    preslim) kb="${FAKE_DU_KB_PRESLIM:-${FAKE_DU_KB:-0}}" ;;
    current) kb="${FAKE_DU_KB_CURRENT:-${FAKE_DU_KB:-0}}" ;;
    *) kb="${FAKE_DU_KB:-0}" ;;
  esac
  fail=0
  [ "${FAKE_DU_FAIL:-0}" = "1" ] && fail=1
  if [ -n "${FAKE_DU_FAIL_ITEM:-}" ] && [ "${FAKE_DU_FAIL_ITEM}" = "${cat}" ]; then fail=1; fi
  if [ -n "${FAKE_DU_FAIL_DIR:-}" ] && [ "${FAKE_DU_FAIL_DIR}" = "${base}" ]; then fail=1; fi
  if [ "${fail}" = "1" ]; then
    printf 'du: %s: Operation not permitted\n' "${arg}" >&2
    rc=1
    continue
  fi
  [ "${FAKE_DU_EMPTY:-0}" = "1" ] && continue
  case "${1:-}" in
    -sk) printf '%s\t%s\n' "${kb}" "${arg}" ;;
    -sh|-h) printf '%s\t%s\n' "${FAKE_DU_H:-1K}" "${arg}" ;;
  esac
done
exit "${rc}"
SH
chmod +x "${SHIM}/df" "${SHIM}/du"

# 夹具：热备预算项三项（backups/ + *.db.zst + .pre-slim）与非预算项（活库、current.db）
# 都造成真实存在的对象（*.db.zst 与 .pre-slim 用真文件，脚本的 zsh 通配才能命中）。
mkdir -p "${TMP}/proj/data" "${TMP}/proj-nopreslim" "${TMP}/snap" \
         "${TMP}/TradingAgents-AShare-releases/backups" \
         "${TMP}/ta-work/dav-2013"
: > "${TMP}/proj/data/tradingagents.db"                        # 活库（不计预算）
: > "${TMP}/proj/data/tradingagents.db.pre-slim"               # 发布期 .pre-slim 副本（计预算）
: > "${TMP}/snap/tradingagents-2026-10-16.db.zst"              # 快照基线（计预算，1 个文件）
: > "${TMP}/snap/current.db"                                   # 未压缩工作副本（不计预算）
: > "${TMP}/ta-work/dav-2013/expr-whole.db"                    # 整库实验副本（不在任何测量根下）
: > "${TMP}/ta-work/dav-2013/expr.db.pre-slim"                 # 同上（名带 pre-slim 也不得被扫到）
# 多文件累加用夹具：3 个 *.db.zst（快照目录另造一份）
mkdir -p "${TMP}/snap-multi"
for d in 2026-10-14 2026-10-15 2026-10-16; do : > "${TMP}/snap-multi/tradingagents-${d}.db.zst"; done

run_daily() {  # $1=avail_gib $2=每项 du_kb（所有类别同一值）
  env PATH="${SHIM}:${BASE_PATH}" FAKE_AVAIL_GIB="$1" FAKE_DU_KB="$2" FAKE_DU_H="1K" \
      STORAGE_PROJ="${TMP}/proj" TA_SNAPSHOT_DIR="${TMP}/snap" \
      "${ZSH_BIN}" "${SCRIPT}" daily 2>&1
}

run_daily_mix() {  # $1=avail_gib；其余为 KEY=VAL（FAKE_DU_KB_* / FAKE_DU_FAIL* / 路径覆盖）
  local a="$1"; shift 1
  env PATH="${SHIM}:${BASE_PATH}" FAKE_AVAIL_GIB="${a}" FAKE_DU_H="1K" \
      STORAGE_PROJ="${TMP}/proj" TA_SNAPSHOT_DIR="${TMP}/snap" "$@" \
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

# ---- 3. 热备预算（DAV-2238 三项口径，精确 KiB）------------------------------
echo "[3] 热备预算（DAV-2238：backups + *.db.zst + .pre-slim）"
# 3a. 边界：backups 7864320 + *.db.zst 7864320（夹具 1 个文件）= 15728640 KiB = 正好 15 GiB → 绿
out15="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=7864320 FAKE_DU_KB_ZST=7864320)"
check_has "${out15}" "**热备副本占用：绿**" "backups + *.db.zst = 正好 15 GiB → 绿（含等号边界）"
check_has "${out15}" "15728640 KiB" "报告给出精确 KiB（15 GiB = 15728640 KiB）"
check_has "${out15}" "计入预算①发布/回滚备份" "计入项① backups/ 单列"
check_has "${out15}" "计入预算②共享快照基线" "计入项② 快照 *.db.zst 单列"
check_has "${out15}" "计入预算③发布期 \`.pre-slim\` 副本" "计入项③ .pre-slim 副本单列"
# 3b. 🟡-2 回归点：(15,16) GiB 窗口必须判红，且数字不得再被截断成「15 GiB」
#     16252928 KiB = 15.5 GiB；旧实现 16252928/1048576 = 15 → 假绿
out155="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=16252928)"
check_has "${out155}" "**热备副本占用：红（超预算）**" "(15,16) GiB 窗口的 15.5 GiB → 红"
check_has "${out155}" "15.5 GiB" "15.5 GiB 不再被整数截断成 15"
check_not "${out155}" "合计约 15 GiB" "不再出现「合计约 15 GiB」截断误报"
# 3c. 边界 +1 KiB：15728641 KiB = 15 GiB + 1 KiB → 必须红
out15p="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=7864320 FAKE_DU_KB_ZST=7864321)"
check_has "${out15p}" "**热备副本占用：红（超预算）**" "15 GiB + 1 KiB → 红（不多算也不放过）"
check_has "${out15p}" "15728641 KiB" "越界 1 KiB 在报告中原样可见"
# 3d. 明显超预算仍红：18000000 KiB ≈ 17.2 GiB
out17="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=9000000 FAKE_DU_KB_ZST=9000000)"
check_has "${out17}" "**热备副本占用：红（超预算）**" "≈17.2 GiB → 红（超预算）"
# 3e. .pre-slim 确实进预算：它单独就能把合计推过 15 GiB
outpre="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=7864320 FAKE_DU_KB_PRESLIM=7864321)"
check_has "${outpre}" "**热备副本占用：红（超预算）**" ".pre-slim 计入合计（7864320+7864321 越界 → 红）"
check_has "${outpre}" "15728641 KiB" "越界值由 backups + .pre-slim 相加得到"
# 3f. 多文件累加：backups 3000 + *.db.zst 1000×3（snap-multi 夹具）+ .pre-slim 2000 = 8000 KiB
outsum="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=3000 FAKE_DU_KB_ZST=1000 FAKE_DU_KB_PRESLIM=2000 \
                              TA_SNAPSHOT_DIR="${TMP}/snap-multi")"
check_has "${outsum}" "（8000 KiB；预算 ≤15 GiB = 15728640 KiB）" "文件集合按文件累加（3 个 *.db.zst 各 1000）"
check_has "${outsum}" "共享快照基线 \`*.db.zst\`（3 个文件）" "报告给出 *.db.zst 文件数"
# 3g. 排除项：活库 28.6 GiB（PROJ，含 data/tradingagents.db）与 current.db 6.0 GiB 都不进预算
outex="$(run_daily_mix 120 FAKE_DU_KB_PROJ=30000000 FAKE_DU_KB_CURRENT=6000000 \
                        FAKE_DU_KB_BACKUPS=1000 FAKE_DU_KB_ZST=1000)"
check_has "${outex}" "（2000 KiB；预算 ≤15 GiB = 15728640 KiB）" "预算合计只含 backups 1000 + *.db.zst 1000"
check_has "${outex}" "共享快照基线 \`*.db.zst\`（1 个文件）：0.0 GiB（1000 KiB）" "快照项只计 *.db.zst（不含 current.db 的 6000000 KiB）"
check_has "${outex}" "不计入预算：工作树/活库（含 data/tradingagents.db）：28.6 GiB" "活库单列并标注不计入预算"
check_has "${outex}" "不计入预算：快照目录未压缩副本 current.db" "current.db 单列并标注不计入预算"
# 3h. ta-work/dav-2013 实验副本（含 .pre-slim 命名者）不得被扫进预算
dav2013="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=1000 FAKE_DU_KB_ZST=1000 FAKE_DU_KB_PRESLIM=0)"
check_has "${dav2013}" "（2000 KiB；预算 ≤15 GiB = 15728640 KiB）" "ta-work/dav-2013 实验副本不进合计"
check_not "${dav2013}" "ta-work" "报告不出现 ta-work 路径（脚本不扫它）"
# 3i. 无 *.db.zst / 无 .pre-slim 时按 0 计，不算「不可测」
outzero="$(run_daily_mix 120 STORAGE_PROJ="${TMP}/proj-nopreslim" TA_SNAPSHOT_DIR="${TMP}/snap-empty")"; rc_zero=$?
check_has "${outzero}" "共享快照基线 \`*.db.zst\`：0 个文件" "无 *.db.zst → 报 0 个文件"
check_has "${outzero}" "发布期 \`.pre-slim\` 副本：0 个文件" "无 .pre-slim → 报 0 个文件"
check_has "${outzero}" "**热备副本占用：绿**" "空集合按 0 计，不判不可测"
if [ "${rc_zero}" -eq 0 ]; then ok "空集合仍以退出码 0 结束（rc=0）"; else bad "空集合退出码应为 0，实得 ${rc_zero}"; fi

# ---- 4. du 失败/不可测（DAV-2238：fail-closed，热备行判「红」）---------------
echo "[4] du 失败判「红」（DAV-2238 fail-closed）"
outfail="$(run_daily_mix 120 FAKE_DU_FAIL=1)"; rc_fail=$?
check_has "${outfail}" "**热备副本占用：红（不可测，fail-closed）**" "全部测量项不可测 → 热备行判「红」"
check_not "${outfail}" "**热备副本占用：绿**" "不可测时绝不出现热备绿灯"
check_not "${outfail}" "**热备副本占用：未知" "不再是旧口径的「未知」（DAV-2238 要求判红）"
check_has "${outfail}" "Operation not permitted" "du 的失败原因（stderr）被收进报告留证"
check_has "${outfail}" "**磁盘水位：绿**" "测量失败时报告仍然产出（不是静默失败）"
check_has "${outfail}" "5 个测量项不可测" "报告注明不可测项数（backups/*.db.zst/.pre-slim/PROJ/current.db = 5）"
if [ "${rc_fail}" -eq 2 ]; then ok "不可测时退出码为 2（rc=${rc_fail}）"; else bad "不可测退出码应为 2，实得 ${rc_fail}"; fi

# 仅预算项之一（*.db.zst）不可测 → 同样判红并给非零退出
outp1="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=1000 FAKE_DU_KB_ZST=1000 FAKE_DU_FAIL_ITEM=zst)"; rc_p1=$?
check_has "${outp1}" "**热备副本占用：红（不可测，fail-closed）**" "仅 *.db.zst 不可测 → 判红"
check_has "${outp1}" "1 个测量项不可测" "只计 1 个不可测项（其余项照常测量）"
if [ "${rc_p1}" -eq 2 ]; then ok "部分不可测退出码为 2（rc=${rc_p1}）"; else bad "部分不可测退出码应为 2，实得 ${rc_p1}"; fi

# 非预算项（PROJ 活库）单独不可测 → 仍判红（fail-closed 不许出现「测得全绿」）
outp2="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=1000 FAKE_DU_KB_ZST=1000 FAKE_DU_FAIL_ITEM=proj)"; rc_p2=$?
check_has "${outp2}" "**热备副本占用：红（不可测，fail-closed）**" "仅 PROJ（非预算项）不可测 → 同样判红"
check_has "${outp2}" "⚠️ 不可测：${TMP}/proj" "点名不可测的具体对象"
if [ "${rc_p2}" -eq 2 ]; then ok "非预算项不可测退出码为 2（rc=${rc_p2}）"; else bad "非预算项不可测退出码应为 2，实得 ${rc_p2}"; fi

# 静默型「测不到」：du 退出码 0 但输出为空 → 同样判红（不得按 0 累加）
outp3="$(run_daily_mix 120 FAKE_DU_EMPTY=1)"; rc_p3=$?
check_has "${outp3}" "**热备副本占用：红（不可测，fail-closed）**" "du 输出为空（rc=0）→ 判红"
check_has "${outp3}" "du 无输出" "报告写明「du 无输出」原因"
if [ "${rc_p3}" -eq 2 ]; then ok "空输出退出码为 2（rc=${rc_p3}）"; else bad "空输出退出码应为 2，实得 ${rc_p3}"; fi

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
