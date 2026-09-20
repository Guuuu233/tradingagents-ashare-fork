#!/bin/bash
# 检测测试进程是否踩中 vendor 连接缺陷（DAV-979 / DAV-995）
#
# 病根：对端关闭连接后客户端不挂断。有两个形态完全相反的变体：
#   ① 忙循环  %CPU≈100%   baostock socketutil.py:55 recv() 无 EOF 检查，空转
#   ② 阻塞    %CPU≈0%     卡在 syscall 等一个已死的连接
# 两者都不会自愈，等多久都没用。CLOSE_WAIT 是确诊依据。
#
# 用法：bash work/check-stuck-tests.sh [卡住判定阈值秒数，默认 300]

THRESHOLD=${1:-300}
found=0

printf "%-8s %-7s %-10s %-9s %s\n" PID %CPU 存活 形态 判定
printf -- "------------------------------------------------------------------------\n"

# 注意：必须同时匹配 `python -m pytest` 与 `venv310/bin/pytest` 两种启动形态。
# 2026-09-16 教训：DAV-1007 的 RT-FULL 用 bin/pytest 启动，旧模式漏判，带 3 条 CLOSE_WAIT 却报「无异常」。
for p in $(pgrep -f "venv310/bin/python -m pytest|venv310/bin/pytest" 2>/dev/null); do
    cpu=$(ps -o %cpu= -p "$p" 2>/dev/null | tr -d ' ')
    et=$(ps -o etime= -p "$p" 2>/dev/null | tr -d ' ')
    [ -z "$cpu" ] && continue

    # etime → 秒
    secs=$(echo "$et" | awk -F'[-:]' '{
        if (NF==4) print $1*86400+$2*3600+$3*60+$4;
        else if (NF==3) print $1*3600+$2*60+$3;
        else if (NF==2) print $1*60+$2;
        else print $1 }')

    cw=$(lsof -p "$p" -a -i 2>/dev/null | grep -c CLOSE_WAIT)
    busy=$(echo "$cpu" | awk '{print ($1>50)?1:0}')

    if [ "$busy" = "1" ]; then form="忙循环"; else form="阻塞/正常"; fi

    verdict="正常"
    if [ "$cw" -gt 0 ]; then
        verdict="🔴 确诊（CLOSE_WAIT×$cw）不会自愈，立即中止"
        found=1
    elif [ "$secs" -gt "$THRESHOLD" ] && [ "$busy" = "1" ]; then
        verdict="🟠 疑似忙循环，超 ${THRESHOLD}s 仍满速"
        found=1
    elif [ "$secs" -gt "$THRESHOLD" ]; then
        verdict="🟡 超 ${THRESHOLD}s，查 lsof 确认是否阻塞在死连接"
    fi

    printf "%-8s %-7s %-10s %-9s %s\n" "$p" "$cpu" "$et" "$form" "$verdict"

    if [ "$cw" -gt 0 ]; then
        lsof -p "$p" -a -i 2>/dev/null | grep CLOSE_WAIT | awk '{print "         └─",$8,$9,$10}'
        cwd=$(lsof -p "$p" 2>/dev/null | awk '$4=="cwd"{print $NF}')
        echo "         └─ cwd: $cwd"
    fi
done

[ "$found" = "0" ] && echo "(无异常)"
echo
echo "提示：确诊后 kill -9 <PID>。勿用宽泛 pkill -f pytest——会误杀他人门禁跑。"
