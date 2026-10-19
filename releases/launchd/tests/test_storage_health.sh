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
#   5. 枚举阶段/存在性判定失败（DAV-2239 🟡-1 返修回归点）：预算项②③改用 `find` 枚举 + 退出码
#      判定，路径存在性用三态（present/absent/unknown）。「目录存在但不可读/不可进入」
#      （用 `chmod 000` 等价模拟 TCC 拒读）与「上级目录不可读致 stat EACCES」均不得被静默按 0 计、
#      不得出「0 个文件」、不得写成「目录不存在」、不得判绿；隐藏路径（`.` 开头的
#      目录/文件）也须枚举到；PROJ 下有不可读子目录 → ③枚举失败判红，且大文件清单不得静默成空；
#   6. 符号链接双层口径（DAV-2250 🟡-1 返回）：目录级不进入（`find -P`）、文件级解引用
#      （`-type f -o -type l` + `du -H`）；根目录本身是链接时先 `cd -P` 解析；名字匹配
#      但按普通文件测不了（悬空/指向目录）→ 不可测判红。带真 `du` 一组（不经桩）验证解引用生效；
#  10. ① 目录级链接解引用 + 悬空根链接（DAV-2252 🟡-1/🟡-2）：`du_kb` 带 `-H`（真 `du` 断言
#      ① ≥4096 KiB）、`$REL/backups` / `$SNAP_DIR` 为悬空符号链接 ⇒ 不可测判红，
#      不得写成「目录不存在」/「0 个文件」；
#   7. 🟢-1：已测得部分和已超预算时优先出「红（超预算）」，并附「另有 N 项不可测」；
#   8. 只读纪律静态门：剔除注释/`[[ ]]`/双引号字面量后，不得出现
#      rm/rmdir/unlink/truncate/shred/mv/dd/install/cp、find -delete，
#      也不得有写文件的重定向（只允许 2>/dev/null、>/dev/null、2>&1）；
#   9. 每周基线选样：取日期最大的一份快照、SHA256/字节数与本地 shasum/stat 一致、
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
# **符号链接语义（DAV-2250，对齐真 du 实测）**：macOS/BSD 真 du 对命令行上的符号链接
#   默认**不**解引用（打印 0），带 `-H` 才解引用到目标；桩照此实现——无 `-H` 时符号链接
#   参数一律计 0 KiB，因此「撤掉 -H」的变异会被本门禁抓到。
has_H=0
for a in "$@"; do [ "${a}" = "-H" ] && has_H=1; done
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
  # 真 du 默认不解引用命令行上的符号链接：无 -H 时只算链接自身（≈0 KiB）
  if [ -L "${arg}" ] && [ "${has_H}" = "0" ]; then kb=0; fi
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

# 3j. 🟢-1（DAV-2239）：已测得部分和已超预算 + 另有项不可测 → 仍要出现「红（超预算）」
outov="$(run_daily_mix 120 FAKE_DU_KB_BACKUPS=16000000 FAKE_DU_KB_ZST=471792 FAKE_DU_FAIL_ITEM=proj)"; rc_ov=$?
check_has "${outov}" "**热备副本占用：红（超预算）**" "部分和已超预算 → 出「红（超预算）」（🟢-1）"
check_has "${outov}" "16471792 KiB" "已测得部分和原样可见（16000000 + 471792）"
check_has "${outov}" "另有 1 个测量项不可测，合计不完整" "同时注明合计不完整（诊断串不被盖掉）"
if [ "${rc_ov}" -eq 2 ]; then ok "超预算 + 有不可测项退出码为 2（rc=${rc_ov}）"; else bad "退出码应为 2，实得 ${rc_ov}"; fi

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

# ---- 4b. 枚举阶段失败（DAV-2239 🟡-1 回归点）--------------------------------
echo "[4b] 存在但不可读的根目录 → 枚举失败判红（🟡-1）"
# 夹具：快照目录存在、里面有 *.db.zst 与 current.db，但 chmod 000（等价 TCC 拒读）
NOROOT="${TMP}/snap-noread"; mkdir -p "${NOROOT}"
: > "${NOROOT}/tradingagents-2026-10-17.db.zst"; : > "${NOROOT}/current.db"
chmod 000 "${NOROOT}"
outnr="$(run_daily_mix 120 TA_SNAPSHOT_DIR="${NOROOT}")"; rc_nr=$?
chmod 700 "${NOROOT}"    # 复原，保证 trap 的 rm -rf 能清掉
check_has "${outnr}" "**热备副本占用：红（不可测，fail-closed）**" "存在但不可读的快照目录 → 热备行判红"
check_not "${outnr}" "**热备副本占用：绿**" "枚举失败时绝不出现绿灯（🟡-1 核心断言）"
check_not "${outnr}" "共享快照基线 \`*.db.zst\`：0 个文件" "不可读不得被写成「0 个文件」（不得静默按 0 计）"
check_has "${outnr}" "⚠️ 不可测：${NOROOT}/*.db.zst" "点名不可读的快照目录（②项）"
check_has "${outnr}" "⚠️ 不可测：${NOROOT} 下的 .pre-slim 副本" "点名不可读的快照目录（③项）"
check_has "${outnr}" "Permission denied" "find 的失败原因入报告留证"
# DAV-2250：find 报错必须走「枚举失败」通道（rc 闸门）；若闸门被撤，错误行会被循环当成
# 「枚举到的条目」，报告会写成「条目无法按普通文件测量」——那是诊断通道被降级（M10 变异回归点）。
check_not "${outnr}" "无法按普通文件测量" "find 报错不得被当成「枚举到的条目」（rc 闸门仍在生效）"
if [ "${rc_nr}" -eq 2 ]; then ok "不可枚举时退出码为 2（rc=${rc_nr}）"; else bad "不可枚举时退出码应为 2，实得 ${rc_nr}"; fi

# ---- 4c. 隐藏路径收口（DAV-2239 🟡-1 顺带）----------------------------------
echo "[4c] 隐藏路径也必须枚举到（glob 默认不匹配反引号内的点开头路径段）"
HID="${TMP}/hidden-fixture"
mkdir -p "${HID}/proj/.hidden-dir" "${HID}/TradingAgents-AShare-releases/backups" "${HID}/snap"
: > "${HID}/proj/.hidden-dir/tradingagents.db.pre-slim"    # 隐藏目录下的 .pre-slim 副本
: > "${HID}/snap/.tradingagents-2026-10-15.db.zst"         # 隐藏的 *.db.zst
outhid="$(run_daily_mix 120 STORAGE_PROJ="${HID}/proj" TA_SNAPSHOT_DIR="${HID}/snap" \
                        FAKE_DU_KB_BACKUPS=3000 FAKE_DU_KB_ZST=1000 FAKE_DU_KB_PRESLIM=2000)"
check_has "${outhid}" "共享快照基线 \`*.db.zst\`（1 个文件）" "隐藏的 *.db.zst 也被枚举到"
check_has "${outhid}" "发布期 \`.pre-slim\` 副本（1 个文件）" "隐藏目录下的 .pre-slim 也被枚举到"
check_has "${outhid}" "（6000 KiB；预算 ≤15 GiB = 15728640 KiB）" "隐藏路径计入合计（3000 + 1000 + 2000）"

# ---- 4d. PROJ 内有不可读子目录 → ③判红 + 大文件清单留证（🟢-3）--------------
echo "[4d] 不可读子目录：预算法不静默 + 大文件清单留证（🟢-3）"
SUB="${TMP}/proj-subnoread"; mkdir -p "${SUB}/data" "${SUB}/.locked"
: > "${SUB}/data/tradingagents.db"; : > "${SUB}/.locked/blocked.bin"
chmod 000 "${SUB}/.locked"
outsub="$(run_daily_mix 120 STORAGE_PROJ="${SUB}" FAKE_DU_KB_BACKUPS=1000)"; rc_sub=$?
chmod 700 "${SUB}/.locked"    # 复原，保证 trap 的 rm -rf 能清掉
check_has "${outsub}" "**热备副本占用：红（不可测，fail-closed）**" "PROJ 下有不可读子目录 → ③枚举失败判红"
check_has "${outsub}" "⚠️ 不可测：${SUB} 下的 .pre-slim 副本" "点名枚举失败的根（子目录不可读）"
check_has "${outsub}" "大文件清单不完整" "大文件清单不静默成空（🟢-3 留证）"
check_not "${outsub}" "无法按普通文件测量" "不可读子目录的 find 报错不得被当成「枚举到的条目」（rc 闸门）"
if [ "${rc_sub}" -eq 2 ]; then ok "不可读子目录退出码为 2（rc=${rc_sub}）"; else bad "退出码应为 2，实得 ${rc_sub}"; fi

# ---- 4e. 上级目录不可读：存在性无法判断 → 不可测（不得当作「不存在」跳过）-------------
echo "[4e] 上级目录不可读 → 三态判 unknown，不得静默跳过"
PAR="${TMP}/locked-parent"
mkdir -p "${PAR}/TradingAgents-AShare-releases/backups" "${PAR}/proj/data"
: > "${PAR}/TradingAgents-AShare-releases/backups/bak-x.db.zst"
: > "${PAR}/proj/data/tradingagents.db"
chmod 000 "${PAR}"
outpar="$(run_daily_mix 120 STORAGE_PROJ="${PAR}/proj" TA_SNAPSHOT_DIR="${TMP}/snap")"; rc_par=$?
chmod 700 "${PAR}"    # 复原，保证 trap 的 rm -rf 能清掉
check_has "${outpar}" "**热备副本占用：红（不可测，fail-closed）**" "上级目录不可读 → 热备行判红"
check_not "${outpar}" "**热备副本占用：绿**" "存在性判断不了时绝不出现绿灯"
check_not "${outpar}" "目录不存在" "不得把「判断不了」写成「不存在」（不得静默按 0 计）"
check_has "${outpar}" "路径存在性无法判断" "报告写明确切原因（path_state=unknown）"
if [ "${rc_par}" -eq 2 ]; then ok "存在性未知退出码为 2（rc=${rc_par}）"; else bad "退出码应为 2，实得 ${rc_par}"; fi

# ---- 4f. 符号链接双层口径（DAV-2250 🟡-1 返回）-------------------------------
# 复审 DAV-2247 §4 的逃逸形态：`snap/link-*.db.zst → outside/*.db.zst`（文件级）与
# `proj/link-rel → outside/rel/`（目录级，后方含 *.pre-slim 实体）在候选 72e434b0 上均报
# 「0 个文件」绿灯。本组钉住：目录级不进入 / 文件级解引用 / 根为链接先解析 /
# 名字匹配但测不了 → 不可测。
echo "[4f] 符号链接：目录级不进入 / 文件级解引用（DAV-2250）"
LNK="${TMP}/links"
mkdir -p "${LNK}/outside/rel" "${LNK}/snap-link" "${LNK}/snap-empty" "${LNK}/snap-real" "${LNK}/proj-plain"
: > "${LNK}/outside/other.db.zst"
: > "${LNK}/outside/rel/huge.db.pre-slim"
: > "${LNK}/snap-real/tradingagents-2026-10-18.db.zst"
ln -s "${LNK}/outside/other.db.zst" "${LNK}/snap-link/link-snap.db.zst"   # 文件级链接（→ 快照根之外）
ln -s "${LNK}/snap-real" "${LNK}/snap-rootlink"                          # 根目录本身是符号链接
ln -s "${LNK}/outside/rel" "${LNK}/proj-plain/link-rel"                  # 目录级链接（后方有 .pre-slim）

outfl="$(run_daily_mix 120 STORAGE_PROJ="${LNK}/proj-plain" TA_SNAPSHOT_DIR="${LNK}/snap-link" \
                     FAKE_DU_KB_ZST=1000 FAKE_DU_KB_PRESLIM=2000)"; rc_fl=$?
check_has "${outfl}" "共享快照基线 \`*.db.zst\`（1 个文件）" "②枚举到文件级符号链接（-type l 计入）"
check_has "${outfl}" "（1000 KiB；预算 ≤15 GiB = 15728640 KiB）" "文件级链接经 du -H 解引用计量（撤 -H 则退化为 0）"
if [ "${rc_fl}" -eq 0 ]; then ok "文件级链接全部测得 → 退出码 0（rc=${rc_fl}）"; else bad "退出码应为 0，实得 ${rc_fl}"; fi

outrl="$(run_daily_mix 120 STORAGE_PROJ="${LNK}/proj-plain" TA_SNAPSHOT_DIR="${LNK}/snap-rootlink" \
                      FAKE_DU_KB_ZST=1000)"
check_has "${outrl}" "共享快照基线 \`*.db.zst\`（1 个文件）" "②根目录是符号链接 → 先 cd -P 解析再枚举"
check_not "${outrl}" "共享快照基线 \`*.db.zst\`：0 个文件" "根为链接不得静默报「0 个文件」（find -P 不进入起始链接）"

outdl="$(run_daily_mix 120 STORAGE_PROJ="${LNK}/proj-plain" TA_SNAPSHOT_DIR="${LNK}/snap-empty" \
                      FAKE_DU_KB_PRESLIM=2000)"
check_has "${outdl}" "发布期 \`.pre-slim\` 副本：0 个文件" "③不进入目录级符号链接（link-rel 后方的 .pre-slim 不计）"
check_has "${outdl}" "（0 KiB；预算 ≤15 GiB = 15728640 KiB）" "目录级链接后方的副本不进合计"

# 名字匹配但按普通文件测不了：悬空链接 / 指向目录的链接 → 不可测（宁可误红）
BAD="${TMP}/linkbad"; mkdir -p "${BAD}/snap" "${BAD}/outside/rel"
ln -s "${BAD}/outside/rel" "${BAD}/snap/weird.db.zst"
outbad="$(run_daily_mix 120 STORAGE_PROJ="${TMP}/proj-nopreslim" TA_SNAPSHOT_DIR="${BAD}/snap")"; rc_bad=$?
check_has "${outbad}" "**热备副本占用：红（不可测，fail-closed）**" "指向目录的链接（名字匹配）→ 不可测判红"
check_not "${outbad}" "共享快照基线 \`*.db.zst\`：0 个文件" "不得把「无法测量」写成「0 个文件」"
check_has "${outbad}" "无法按普通文件测量" "报告写明确切原因"
if [ "${rc_bad}" -eq 2 ]; then ok "名字匹配但测不了 → 退出码 2（rc=${rc_bad}）"; else bad "退出码应为 2，实得 ${rc_bad}"; fi

BADL="${TMP}/linkdangling"; mkdir -p "${BADL}/snap"
ln -s "${BADL}/nowhere/gone.db.zst" "${BADL}/snap/dangling.db.zst"
outdl2="$(run_daily_mix 120 STORAGE_PROJ="${TMP}/proj-nopreslim" TA_SNAPSHOT_DIR="${BADL}/snap")"; rc_dl2=$?
check_has "${outdl2}" "**热备副本占用：红（不可测，fail-closed）**" "悬空链接（名字匹配）→ 不可测判红"
check_has "${outdl2}" "dangling.db.zst" "报告点名悬空链接的具体路径"
if [ "${rc_dl2}" -eq 2 ]; then ok "悬空链接 → 退出码 2（rc=${rc_dl2}）"; else bad "退出码应为 2，实得 ${rc_dl2}"; fi

# 真 du / 真 df（不经桩）：用真实文件证明 -H 解引用真实生效、目录链接真实不被递归
REAL="${TMP}/real"
mkdir -p "${REAL}/outside/rel" "${REAL}/proj" "${REAL}/proj-dirlink" "${REAL}/snap-point" "${REAL}/snap-empty"
dd if=/dev/zero of="${REAL}/outside/big.db.zst" bs=1024 count=8192 2>/dev/null
dd if=/dev/zero of="${REAL}/outside/rel/huge.db.pre-slim" bs=1024 count=2048 2>/dev/null
ln -s "${REAL}/outside/big.db.zst" "${REAL}/snap-point/link.db.zst"
ln -s "${REAL}/snap-point" "${REAL}/snap-rootlink"
ln -s "${REAL}/outside/rel" "${REAL}/proj-dirlink/link-rel"
outreal="$(env PATH="${BASE_PATH}" STORAGE_PROJ="${REAL}/proj" TA_SNAPSHOT_DIR="${REAL}/snap-rootlink" \
               "${ZSH_BIN}" "${SCRIPT}" daily 2>&1)"
real_line="$(printf '%s\n' "${outreal}" | grep '共享快照基线' | head -1)"
real_kb="$(printf '%s\n' "${real_line}" | sed -n 's/.*（\([0-9][0-9]*\) KiB）.*/\1/p')"
case "${real_kb}" in
  ''|*[!0-9]*) bad "真 du：未能从报告解析出 ② 的 KiB（line=${real_line}）" ;;
  *) if [ "${real_kb}" -ge 4096 ]; then ok "真 du：根为链接 + 文件级链接 → ②计量 ${real_kb} KiB（≥4096，-H 解引用生效）"
     else bad "真 du：②只计 ${real_kb} KiB（<4096，-H 解引用未生效）"; fi ;;
esac
outreald="$(env PATH="${BASE_PATH}" STORAGE_PROJ="${REAL}/proj-dirlink" TA_SNAPSHOT_DIR="${REAL}/snap-empty" \
                 "${ZSH_BIN}" "${SCRIPT}" daily 2>&1)"
check_has "${outreald}" "发布期 \`.pre-slim\` 副本：0 个文件" "真 du：③不进入目录级链接（后方 2 MiB .pre-slim 不计）"
check_not "${outreald}" "huge.db.pre-slim" "真 du：链接后方的 .pre-slim 实体不进报告"
check_has "${outreald}" "**热备副本占用：绿**" "真 du：目录级链接不进入 → 不得误判「不可测」"

# ---- 4g. ① 目录级链接 + 悬空根链接（DAV-2252 🟡-1/🟡-2 返修）-------------------
# 🟡-1：① 的测量对象 `$REL/backups` 本身就是预算项——它是目录级符号链接时必须按目标实测
#       （`du -sk -H`）；不加 `-H` 实测报 0 KiB 且 rc=0（真 du：链接 0 KiB / -H 51452 KiB）→ 假绿。
# 🟡-2：根（`$REL/backups`、`$SNAP_DIR`）为**悬空**符号链接时，`[ -e ]` 为假，不得走 absent
#       （「目录不存在」/「0 个文件」）静默判绿，必须走不可测判红。
echo "[4g] ① 目录级链接解引用 + 悬空根链接判红（DAV-2252）"
BKL="${TMP}/baklink"
mkdir -p "${BKL}/outside/bak" "${BKL}/proj" "${BKL}/snap-empty" "${BKL}/TradingAgents-AShare-releases"
: > "${BKL}/outside/bak/old.db.zst"
dd if=/dev/zero of="${BKL}/outside/bak/big.db.zst" bs=1024 count=8192 2>/dev/null
ln -s "${BKL}/outside/bak" "${BKL}/TradingAgents-AShare-releases/backups"   # ① 的根是目录级链接
outbl="$(run_daily_mix 120 STORAGE_PROJ="${BKL}/proj" TA_SNAPSHOT_DIR="${BKL}/snap-empty" \
                     FAKE_DU_KB_BACKUPS=1000)"; rc_bl=$?
check_has "${outbl}" "计入预算①发布/回滚备份 \`backups/\`：0.0 GiB（1000 KiB）" "① 为目录级链接 → du -H 解引用计量（撤 -H 则退化为 0）"
check_not "${outbl}" "计入预算①发布/回滚备份 \`backups/\`：目录不存在" "链接根不得写成「目录不存在」"
if [ "${rc_bl}" -eq 0 ]; then ok "① 链接根可测 → 退出码 0（rc=${rc_bl}）"; else bad "退出码应为 0，实得 ${rc_bl}"; fi

# 真 du（不经桩）：① 的链接后方是 8 MiB 实体 → ① 必须 ≥4096 KiB
outbl2="$(env PATH="${BASE_PATH}" STORAGE_PROJ="${BKL}/proj" TA_SNAPSHOT_DIR="${BKL}/snap-empty" \
               "${ZSH_BIN}" "${SCRIPT}" daily 2>&1)"
bak_line="$(printf '%s\n' "${outbl2}" | grep '计入预算①' | head -1)"
bak_kb="$(printf '%s\n' "${bak_line}" | sed -n 's/.*（\([0-9][0-9]*\) KiB）.*/\1/p')"
case "${bak_kb}" in
  ''|*[!0-9]*) bad "真 du：未能从报告解析出 ① 的 KiB（line=${bak_line}）" ;;
  *) if [ "${bak_kb}" -ge 4096 ]; then ok "真 du：① 根为目录级链接 → 计量 ${bak_kb} KiB（≥4096，-H 解引用生效）"
     else bad "真 du：① 只计 ${bak_kb} KiB（<4096，-H 解引用未生效）"; fi ;;
esac

# 悬空根链接：①（$REL/backups）与②（$SNAP_DIR）都必须走不可测判红，不得静默按 0
DGL="${TMP}/danglingroot"
mkdir -p "${DGL}/proj" "${DGL}/snap-empty" "${DGL}/TradingAgents-AShare-releases"
ln -s "${DGL}/nowhere/backups" "${DGL}/TradingAgents-AShare-releases/backups"
outdg="$(run_daily_mix 120 STORAGE_PROJ="${DGL}/proj" TA_SNAPSHOT_DIR="${DGL}/snap-empty")"; rc_dg=$?
check_has "${outdg}" "**热备副本占用：红（不可测，fail-closed）**" "① 根为悬空符号链接 → 不可测判红"
check_not "${outdg}" "计入预算①发布/回滚备份 \`backups/\`：目录不存在" "悬空根不得写成「目录不存在」（不得静默按 0 计）"
check_has "${outdg}" "悬空符号链接" "报告写明确切原因（悬空符号链接）"
if [ "${rc_dg}" -eq 2 ]; then ok "① 悬空根退出码 2（rc=${rc_dg}）"; else bad "退出码应为 2，实得 ${rc_dg}"; fi

ln -s "${DGL}/nowhere-snap" "${DGL}/snap-dangling"
outdg2="$(run_daily_mix 120 STORAGE_PROJ="${TMP}/proj-nopreslim" TA_SNAPSHOT_DIR="${DGL}/snap-dangling")"; rc_dg2=$?
check_has "${outdg2}" "**热备副本占用：红（不可测，fail-closed）**" "② 根为悬空符号链接 → 不可测判红"
check_not "${outdg2}" "共享快照基线 \`*.db.zst\`：0 个文件" "悬空快照根不得写成「0 个文件」（不得静默按 0 计）"
check_has "${outdg2}" "⚠️ 不可测：${DGL}/snap-dangling/*.db.zst" "点名悬空快照根（②项）"
if [ "${rc_dg2}" -eq 2 ]; then ok "② 悬空根退出码 2（rc=${rc_dg2}）"; else bad "退出码应为 2，实得 ${rc_dg2}"; fi

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
