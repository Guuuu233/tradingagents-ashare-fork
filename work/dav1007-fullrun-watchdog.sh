#!/bin/bash
# DAV-1007 全量跑批看门狗
#
# 背景：2026-09-16 裁定「卡 A 冻结于 8589e65，复审完成前不得启动全量」。
# agent 曾在主控中止后【自动重启】新一轮全量，证明该约束必须是执行状态约束，
# 而不能只是文字要求。本脚本负责将其落地为实际拦截。
#
# 匹配策略（精确，避免误杀）：
#   仅匹配跑批脚本 /tmp/run_rtfull_dav1007.py 及其直接派生的 pytest 子进程。
#   不使用宽泛 pkill -f pytest。定向测试（带路径/-k 参数）不受影响。
#
# ⚠️ 误杀防护（实测教训）：单用 `pgrep -f` 会匹配到【命令行里包含该字符串的
#   任意进程】——例如 `pgrep -fl 'run_rtfull_dav1007'` 自身、`grep run_rtfull_dav1007.py`
#   等诊断命令，也包括 agent 的 shell。无人值守的 kill 工具出现误杀即事故。
#   故必须二次校验：匹配到的进程必须是【python 解释器直接执行该脚本】，
#   即 command 形式为 `<...python...> /tmp/run_rtfull_dav1007.py`，且排除本身与 shell。
#
# 中止顺序：先 TERM pytest 子进程 → 留时间给父进程写 summary → 再 TERM 父进程。
#
# 用法：  nohup bash work/dav1007-fullrun-watchdog.sh > /dev/null 2>&1 &
# 停止：  kill <本脚本PID>   （PID 见 $PIDFILE）

LOG=/tmp/dav1007_watchdog_$(date +%Y%m%d_%H%M%S).log
PIDFILE=/tmp/dav1007_watchdog.pid
INTERVAL=20
MAX_HOURS=6

echo $$ > "$PIDFILE"
echo "[$(date '+%F %T')] watchdog 启动 pid=$$ 间隔=${INTERVAL}s 上限=${MAX_HOURS}h" >> "$LOG"
echo "[$(date '+%F %T')] 拦截目标: /tmp/run_rtfull_dav1007.py 及其 pytest 子进程" >> "$LOG"

END=$(( $(date +%s) + MAX_HOURS * 3600 ))

while [ "$(date +%s)" -lt "$END" ]; do
    # 精确定位跑批父进程
    for parent in $(pgrep -f 'run_rtfull_dav1007\.py' 2>/dev/null); do
        # —— 误杀防护：排除自身，并二次校验必须是 python 直接执行该脚本的进程
        [ "$parent" = "$$" ] && continue
        pcmd=$(ps -o command= -p "$parent" 2>/dev/null)
        case "$pcmd" in
            *[Pp]ython*\ /tmp/run_rtfull_dav1007.py*) : ;;   # 合格：真正的跑批进程
            *)
                echo "[$(date '+%F %T')] ℹ️  跳过非跑批进程 PID=$parent（仅命令行含该字符串）: ${pcmd:0:100}" >> "$LOG"
                continue
                ;;
        esac
        echo "[$(date '+%F %T')] 🔴 检测到全量跑批父进程 PID=$parent —— 违反冻结裁定，执行中止" >> "$LOG"
        ps -ww -o pid,ppid,etime,command -p "$parent" >> "$LOG" 2>&1

        # ① 先 TERM 其 pytest 子进程
        for child in $(pgrep -P "$parent" 2>/dev/null); do
            cmd=$(ps -o command= -p "$child" 2>/dev/null)
            case "$cmd" in
                *pytest*)
                    echo "[$(date '+%F %T')]   ① TERM pytest 子进程 $child" >> "$LOG"
                    kill -TERM "$child" 2>/dev/null
                    ;;
            esac
        done

        # ② 留 10s 给父进程写出 summary
        for _ in $(seq 1 10); do
            sleep 1
            ps -p "$parent" >/dev/null 2>&1 || break
        done

        # ③ 父进程若未自行退出，单独 TERM
        if ps -p "$parent" >/dev/null 2>&1; then
            echo "[$(date '+%F %T')]   ③ 父进程未自行退出，TERM $parent" >> "$LOG"
            kill -TERM "$parent" 2>/dev/null
            sleep 3
        else
            echo "[$(date '+%F %T')]   ② 父进程已自行退出（summary 应已写出）" >> "$LOG"
        fi

        echo "[$(date '+%F %T')] ✅ 中止完成，该轮不赋予任何证据效力" >> "$LOG"
    done
    sleep "$INTERVAL"
done

echo "[$(date '+%F %T')] watchdog 到达 ${MAX_HOURS}h 上限，退出" >> "$LOG"
rm -f "$PIDFILE"
