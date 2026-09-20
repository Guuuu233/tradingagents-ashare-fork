#!/bin/bash
# 取证有效性前置校验（主控自用，非派工要求）
#
# 【判断顺序纪律】先核对这次运行【能不能】产出四项证据，再看运行时长/进程状态。
# 顺序反了会犯两类错：
#   ① 对无效运行做长时间等待（已发生：PID 81005 等了 22 分钟）；
#   ② 先下归因结论再验证（已发生：误判 harness 背压，实测 fd1/2 是普通文件）。
#
# 【为什么不 grep argv 判断环境变量】
#   环境变量可由【父进程继承】，根本不会出现在 pytest 的 argv 文本里。
#   靠 grep 命令行会把【合规运行误判为无效】。
#   正确做法：以【探针自己写出的 PROBE_READY 标记】为准 —— 该标记只有在
#   插件真被加载、且 PROBE_OUT 真生效时才会出现。
#
# 【凭据安全】本脚本【绝不打印进程完整环境】（ps eww / environ 一律不用），
#   其中可能含 DATABASE_URL、API key 等凭据。只读 PID、argv 中的非敏感开关、
#   探针日志首行标记、以及 fd 指向。
#
# 用法：
#   bash work/dav1009-check-run-validity.sh <pid> [probe_out_log]
#   bash work/dav1009-check-run-validity.sh            # 自动扫描 pytest 进程
#
# 退出码： 0=有效取证  1=无效取证  2=无 pytest 运行

set -u
GLOB_DIR="${PROBE_LOG_DIR:-/tmp}"

find_probe_log() {
    # 按 PID 在候选日志中反查 PROBE_READY 标记；不读环境变量。
    local pid="$1" f
    for f in "$GLOB_DIR"/dav1009_fdprobe*.log "$GLOB_DIR"/dav1009_probe*.log; do
        [ -f "$f" ] || continue
        if grep -q "^PROBE_READY pid=$pid " "$f" 2>/dev/null; then
            echo "$f"
            return 0
        fi
    done
    return 1
}

check_one() {
    local pid="$1" explicit_log="${2:-}"
    local cmd
    cmd=$(ps -ww -o command= -p "$pid" 2>/dev/null)
    [ -z "$cmd" ] && return 2

    echo "──────────────────────────────────────────────"
    echo "PID $pid"
    local bad=0

    # ── ⓪ 先确认这真是 python 进程 ─────────────────────────────
    # pgrep -f 会匹配到【命令行文本里含 "python -m pytest" 的 shell 包装进程】
    # （实测：主控自己的 bash -c 工具进程就被误匹配，导致查错对象）。
    local comm
    comm=$(ps -o comm= -p "$pid" 2>/dev/null)
    case "$comm" in
        *python*) : ;;
        *)
            echo "  ⏭️ 跳过：comm=$comm 不是 python 进程（shell 包装进程，非被测对象）"
            return 3
            ;;
    esac

    # ── ① 探针启动标记（正控核心）────────────────────────────
    local log=""
    if [ -n "$explicit_log" ] && grep -q "^PROBE_READY pid=$pid " "$explicit_log" 2>/dev/null; then
        log="$explicit_log"
    else
        log=$(find_probe_log "$pid") || log=""
    fi

    if [ -n "$log" ]; then
        local marker
        marker=$(grep -m1 "^PROBE_READY pid=$pid " "$log")
        echo "  ✅ 探针已加载   $(basename "$log")"
        echo "     $marker"
        # PID 匹配已由 grep 模式保证（^PROBE_READY pid=<pid> 空格结尾）
        echo "  ✅ PID 匹配     标记内 pid 与被检进程一致"
        local stall
        stall=$(echo "$marker" | sed -n 's/.*stall_seconds=\([0-9.]*\).*/\1/p')
        echo "  ✅ 阈值已生效   stall_seconds=${stall:-<解析失败>}"
    else
        echo "  ❌ 探针启动标记 未找到 PROBE_READY pid=$pid"
        echo "     → 插件未加载，或 PROBE_OUT 未生效 ⇒ 产不出 sock_id/fd/台账"
        bad=$((bad + 1))
    fi

    # ── ② faulthandler（argv 开关，非敏感，可直接看）──────────
    if echo "$cmd" | grep -q -- "faulthandler_timeout"; then
        echo "  ✅ 栈 dump      faulthandler_timeout 已设"
    else
        echo "  ❌ 栈 dump      缺 faulthandler_timeout"
        bad=$((bad + 1))
    fi

    # ── ③ stdout/stderr 实际去向（查 fd，不靠 argv 文本）──────
    local ok_fd=1
    for fd in 1 2; do
        local info type
        info=$(lsof -a -p "$pid" -d "$fd" -nP 2>/dev/null | tail -1)
        type=$(echo "$info" | awk '{print $5}')
        local name
        name=$(echo "$info" | awk '{print $NF}')
        case "$type" in
            REG)
                echo "  ✅ fd$fd 去向    REG $name"
                ;;
            unix|PIPE|FIFO)
                echo "  ⚠️ fd$fd 去向    $type $name  ← 非普通文件，有背压风险"
                ok_fd=0
                ;;
            *)
                echo "  ⚠️ fd$fd 去向    ${type:-<未知>} $name"
                ok_fd=0
                ;;
        esac
    done
    [ "$ok_fd" -eq 0 ] && bad=$((bad + 1))

    # ── ④ 结论 ───────────────────────────────────────────────
    if [ "$bad" -gt 0 ]; then
        echo "  🔴 判定：【无效取证】$bad 项不合格 —— 立即中止，不必等待运行时长"
        return 1
    fi
    echo "  ✅ 判定：【有效取证】命令构成合格，可继续观察运行状态"
    ps -o pid,stat,pcpu,etime -p "$pid" | tail -1 | sed 's/^/     /'
    return 0
}

rc=2
if [ "$#" -ge 1 ]; then
    check_one "$1" "${2:-}"; rc=$?
else
    pids=$(pgrep -f "venv310/bin/python -m pytest|venv310/bin/pytest" 2>/dev/null)
    if [ -z "$pids" ]; then
        echo "当前无 pytest 运行"
        exit 2
    fi
    rc=2
    for p in $pids; do
        check_one "$p"
        case $? in
            0) [ $rc -ne 1 ] && rc=0 ;;
            1) rc=1 ;;
            3) : ;;   # 非 python 包装进程，不计入判定
        esac
    done
fi
echo "──────────────────────────────────────────────"
exit $rc
