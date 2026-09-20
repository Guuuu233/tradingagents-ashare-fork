#!/bin/bash
# 第 2 项 · 74 个本项目孤儿目录
# 生成 2026-09-20 · 预期回收 972.52 MiB
#
# 不受台账 §6 约束：无 .git，也不在任何仓库的 worktree 登记中，
#   §6.4 的「只能用 git worktree remove」对它们无对象可用。
# dav990-* 的日志与探针源码已归档至 work/dav990-diagnostic-logs/（26 文件），
#   并已 git add -f 入库；其余 284 MiB 巨型 *.sample.log 随目录删除。
# dav-985-review / dav978-git-30aa0020 共 493 MiB 是完整源码树（体积几乎全在
#   frontend/node_modules），内容可从 git 重建，2026-09-16 后无源码级改动。
# 不含 dav941-base-r6（multica fork 克隆的 worktree，须去该仓库 git worktree remove）。
# 不含 h1b-full-run（148 KiB，内有三份 RT-FULL 对照日志，刻意保留）。
#
# 逐路径枚举，可执行行无通配符。/private/tmp 下另有 20 个非本项目目录不得触碰，
# 其中 cc-socks / codex-browser-use / com.openai.sky.CUAService 三个目录里有 22 个
# 活 unix socket，按模式删会打断正在运行的工具。

set -euo pipefail
BEFORE=$(df -k / | tail -1 | awk '{print $4}')
# 守卫 A：归档必须已入库，否则 dav990 证据会随目录消失
[ "$(cd "/Users/davidliu/Documents/TradingAgents-AShare" && git ls-files work/dav990-diagnostic-logs | wc -l | tr -d ' ')" -ge 26 ] \
  || { echo "dav990 归档未入库(<26 文件)，中止" >&2; exit 1; }

# 守卫 B：逐目录存在性 + 防「同名任务重跑生成活 worktree」
[ -d "/private/tmp/dav990-probe-current" ] || { echo "缺失中止: dav990-probe-current" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-probe-current/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-probe-current" >&2; exit 1; }
rm -rf "/private/tmp/dav990-probe-current"
[ -d "/private/tmp/dav987-api-bisection-20260916-0220" ] || { echo "缺失中止: dav987-api-bisection-20260916-0220" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0220/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0220" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0220"
[ -d "/private/tmp/dav986-parent.ene4ys" ] || { echo "缺失中止: dav986-parent.ene4ys" >&2; exit 1; }
[ ! -e "/private/tmp/dav986-parent.ene4ys/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav986-parent.ene4ys" >&2; exit 1; }
rm -rf "/private/tmp/dav986-parent.ene4ys"
[ -d "/private/tmp/dav946-r3-candidate-split" ] || { echo "缺失中止: dav946-r3-candidate-split" >&2; exit 1; }
[ ! -e "/private/tmp/dav946-r3-candidate-split/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav946-r3-candidate-split" >&2; exit 1; }
rm -rf "/private/tmp/dav946-r3-candidate-split"
[ -d "/private/tmp/dav987-api-bisection-20260916-0810" ] || { echo "缺失中止: dav987-api-bisection-20260916-0810" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0810/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0810" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0810"
[ -d "/private/tmp/ta-stack" ] || { echo "缺失中止: ta-stack" >&2; exit 1; }
[ ! -e "/private/tmp/ta-stack/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: ta-stack" >&2; exit 1; }
rm -rf "/private/tmp/ta-stack"
[ -d "/private/tmp/dav987-api-bisection-20260916-0210" ] || { echo "缺失中止: dav987-api-bisection-20260916-0210" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0210/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0210" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0210"
[ -d "/private/tmp/dav-941-r2-worktree" ] || { echo "缺失中止: dav-941-r2-worktree" >&2; exit 1; }
[ ! -e "/private/tmp/dav-941-r2-worktree/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav-941-r2-worktree" >&2; exit 1; }
rm -rf "/private/tmp/dav-941-r2-worktree"
[ -d "/private/tmp/dav946-parent-8854853" ] || { echo "缺失中止: dav946-parent-8854853" >&2; exit 1; }
[ ! -e "/private/tmp/dav946-parent-8854853/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav946-parent-8854853" >&2; exit 1; }
rm -rf "/private/tmp/dav946-parent-8854853"
[ -d "/private/tmp/dav990-single-consumption" ] || { echo "缺失中止: dav990-single-consumption" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-single-consumption/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-single-consumption" >&2; exit 1; }
rm -rf "/private/tmp/dav990-single-consumption"
[ -d "/private/tmp/dav987-api-bisection-20260916-0300" ] || { echo "缺失中止: dav987-api-bisection-20260916-0300" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0300/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0300" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0300"
[ -d "/private/tmp/ta-base" ] || { echo "缺失中止: ta-base" >&2; exit 1; }
[ ! -e "/private/tmp/ta-base/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: ta-base" >&2; exit 1; }
rm -rf "/private/tmp/ta-base"
[ -d "/private/tmp/dav990-no-history-run" ] || { echo "缺失中止: dav990-no-history-run" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-no-history-run/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-no-history-run" >&2; exit 1; }
rm -rf "/private/tmp/dav990-no-history-run"
[ -d "/private/tmp/dav990-selected-run" ] || { echo "缺失中止: dav990-selected-run" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-selected-run/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-selected-run" >&2; exit 1; }
rm -rf "/private/tmp/dav990-selected-run"
[ -d "/private/tmp/dav987-api-bisection-20260916-011046" ] || { echo "缺失中止: dav987-api-bisection-20260916-011046" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-011046/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-011046" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-011046"
[ -d "/private/tmp/dav987-api-bisection-20260916-0730" ] || { echo "缺失中止: dav987-api-bisection-20260916-0730" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0730/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0730" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0730"
[ -d "/private/tmp/dav990-no-history-plugin" ] || { echo "缺失中止: dav990-no-history-plugin" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-no-history-plugin/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-no-history-plugin" >&2; exit 1; }
rm -rf "/private/tmp/dav990-no-history-plugin"
[ -d "/private/tmp/dav-941-parent-885" ] || { echo "缺失中止: dav-941-parent-885" >&2; exit 1; }
[ ! -e "/private/tmp/dav-941-parent-885/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav-941-parent-885" >&2; exit 1; }
rm -rf "/private/tmp/dav-941-parent-885"
[ -d "/private/tmp/dav990-rag-instrument-run" ] || { echo "缺失中止: dav990-rag-instrument-run" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-rag-instrument-run/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-rag-instrument-run" >&2; exit 1; }
rm -rf "/private/tmp/dav990-rag-instrument-run"
[ -d "/private/tmp/dav990-current-targeted" ] || { echo "缺失中止: dav990-current-targeted" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-current-targeted/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-current-targeted" >&2; exit 1; }
rm -rf "/private/tmp/dav990-current-targeted"
[ -d "/private/tmp/dav-941-parent-full" ] || { echo "缺失中止: dav-941-parent-full" >&2; exit 1; }
[ ! -e "/private/tmp/dav-941-parent-full/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav-941-parent-full" >&2; exit 1; }
rm -rf "/private/tmp/dav-941-parent-full"
[ -d "/private/tmp/dav1040" ] || { echo "缺失中止: dav1040" >&2; exit 1; }
[ ! -e "/private/tmp/dav1040/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1040" >&2; exit 1; }
rm -rf "/private/tmp/dav1040"
[ -d "/private/tmp/dav987-api-bisection-20260916-0145" ] || { echo "缺失中止: dav987-api-bisection-20260916-0145" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0145/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0145" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0145"
[ -d "/private/tmp/dav1071" ] || { echo "缺失中止: dav1071" >&2; exit 1; }
[ ! -e "/private/tmp/dav1071/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1071" >&2; exit 1; }
rm -rf "/private/tmp/dav1071"
[ -d "/private/tmp/dav977-rtfull-20260916-r2" ] || { echo "缺失中止: dav977-rtfull-20260916-r2" >&2; exit 1; }
[ ! -e "/private/tmp/dav977-rtfull-20260916-r2/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav977-rtfull-20260916-r2" >&2; exit 1; }
rm -rf "/private/tmp/dav977-rtfull-20260916-r2"
[ -d "/private/tmp/dav987-api-bisection-20260916-0120" ] || { echo "缺失中止: dav987-api-bisection-20260916-0120" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0120/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0120" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0120"
[ -d "/private/tmp/dav987-api-bisection-20260916-0520" ] || { echo "缺失中止: dav987-api-bisection-20260916-0520" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0520/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0520" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0520"
[ -d "/private/tmp/dav987-api-bisection-20260916-0340" ] || { echo "缺失中止: dav987-api-bisection-20260916-0340" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0340/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0340" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0340"
[ -d "/private/tmp/dav978-review-30aa0020" ] || { echo "缺失中止: dav978-review-30aa0020" >&2; exit 1; }
[ ! -e "/private/tmp/dav978-review-30aa0020/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav978-review-30aa0020" >&2; exit 1; }
rm -rf "/private/tmp/dav978-review-30aa0020"
[ -d "/private/tmp/dav987-api-bisection-20260916-0250" ] || { echo "缺失中止: dav987-api-bisection-20260916-0250" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0250/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0250" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0250"
[ -d "/private/tmp/dav986-parent.wUwXhE" ] || { echo "缺失中止: dav986-parent.wUwXhE" >&2; exit 1; }
[ ! -e "/private/tmp/dav986-parent.wUwXhE/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav986-parent.wUwXhE" >&2; exit 1; }
rm -rf "/private/tmp/dav986-parent.wUwXhE"
[ -d "/private/tmp/tradingagents-merged-db-f3uRR2" ] || { echo "缺失中止: tradingagents-merged-db-f3uRR2" >&2; exit 1; }
[ ! -e "/private/tmp/tradingagents-merged-db-f3uRR2/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: tradingagents-merged-db-f3uRR2" >&2; exit 1; }
rm -rf "/private/tmp/tradingagents-merged-db-f3uRR2"
[ -d "/private/tmp/dav977-review-ae54" ] || { echo "缺失中止: dav977-review-ae54" >&2; exit 1; }
[ ! -e "/private/tmp/dav977-review-ae54/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav977-review-ae54" >&2; exit 1; }
rm -rf "/private/tmp/dav977-review-ae54"
[ -d "/private/tmp/dav1028db" ] || { echo "缺失中止: dav1028db" >&2; exit 1; }
[ ! -e "/private/tmp/dav1028db/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1028db" >&2; exit 1; }
rm -rf "/private/tmp/dav1028db"
[ -d "/private/tmp/dav990-rag-run" ] || { echo "缺失中止: dav990-rag-run" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-rag-run/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-rag-run" >&2; exit 1; }
rm -rf "/private/tmp/dav990-rag-run"
[ -d "/private/tmp/dav946-r3-parent-split" ] || { echo "缺失中止: dav946-r3-parent-split" >&2; exit 1; }
[ ! -e "/private/tmp/dav946-r3-parent-split/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav946-r3-parent-split" >&2; exit 1; }
rm -rf "/private/tmp/dav946-r3-parent-split"
[ -d "/private/tmp/ta-df753841-parent-run.PlR5IW" ] || { echo "缺失中止: ta-df753841-parent-run.PlR5IW" >&2; exit 1; }
[ ! -e "/private/tmp/ta-df753841-parent-run.PlR5IW/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: ta-df753841-parent-run.PlR5IW" >&2; exit 1; }
rm -rf "/private/tmp/ta-df753841-parent-run.PlR5IW"
[ -d "/private/tmp/dav990-probe-current3" ] || { echo "缺失中止: dav990-probe-current3" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-probe-current3/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-probe-current3" >&2; exit 1; }
rm -rf "/private/tmp/dav990-probe-current3"
[ -d "/private/tmp/ta-pytest-5ztusn_r" ] || { echo "缺失中止: ta-pytest-5ztusn_r" >&2; exit 1; }
[ ! -e "/private/tmp/ta-pytest-5ztusn_r/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: ta-pytest-5ztusn_r" >&2; exit 1; }
rm -rf "/private/tmp/ta-pytest-5ztusn_r"
[ -d "/private/tmp/dav987-api-bisection-20260916-0410" ] || { echo "缺失中止: dav987-api-bisection-20260916-0410" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0410/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0410" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0410"
[ -d "/private/tmp/tradingagents-merged-db-kX4mO0" ] || { echo "缺失中止: tradingagents-merged-db-kX4mO0" >&2; exit 1; }
[ ! -e "/private/tmp/tradingagents-merged-db-kX4mO0/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: tradingagents-merged-db-kX4mO0" >&2; exit 1; }
rm -rf "/private/tmp/tradingagents-merged-db-kX4mO0"
[ -d "/private/tmp/dav1054_rtfull" ] || { echo "缺失中止: dav1054_rtfull" >&2; exit 1; }
[ ! -e "/private/tmp/dav1054_rtfull/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1054_rtfull" >&2; exit 1; }
rm -rf "/private/tmp/dav1054_rtfull"
[ -d "/private/tmp/dav1079db" ] || { echo "缺失中止: dav1079db" >&2; exit 1; }
[ ! -e "/private/tmp/dav1079db/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1079db" >&2; exit 1; }
rm -rf "/private/tmp/dav1079db"
[ -d "/private/tmp/dav990-probe-current2" ] || { echo "缺失中止: dav990-probe-current2" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-probe-current2/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-probe-current2" >&2; exit 1; }
rm -rf "/private/tmp/dav990-probe-current2"
[ -d "/private/tmp/dav987-api-bisection-20260916-0240" ] || { echo "缺失中止: dav987-api-bisection-20260916-0240" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0240/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0240" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0240"
[ -d "/private/tmp/tradingagents-merged-db-POn4JM" ] || { echo "缺失中止: tradingagents-merged-db-POn4JM" >&2; exit 1; }
[ ! -e "/private/tmp/tradingagents-merged-db-POn4JM/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: tradingagents-merged-db-POn4JM" >&2; exit 1; }
rm -rf "/private/tmp/tradingagents-merged-db-POn4JM"
[ -d "/private/tmp/dav946-r3-parent-8854853" ] || { echo "缺失中止: dav946-r3-parent-8854853" >&2; exit 1; }
[ ! -e "/private/tmp/dav946-r3-parent-8854853/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav946-r3-parent-8854853" >&2; exit 1; }
rm -rf "/private/tmp/dav946-r3-parent-8854853"
[ -d "/private/tmp/dav1009_probe_pkg" ] || { echo "缺失中止: dav1009_probe_pkg" >&2; exit 1; }
[ ! -e "/private/tmp/dav1009_probe_pkg/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1009_probe_pkg" >&2; exit 1; }
rm -rf "/private/tmp/dav1009_probe_pkg"
[ -d "/private/tmp/dav977-rtfull-split-20260916-000857" ] || { echo "缺失中止: dav977-rtfull-split-20260916-000857" >&2; exit 1; }
[ ! -e "/private/tmp/dav977-rtfull-split-20260916-000857/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav977-rtfull-split-20260916-000857" >&2; exit 1; }
rm -rf "/private/tmp/dav977-rtfull-split-20260916-000857"
[ -d "/private/tmp/dav978-git-30aa0020" ] || { echo "缺失中止: dav978-git-30aa0020" >&2; exit 1; }
[ ! -e "/private/tmp/dav978-git-30aa0020/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav978-git-30aa0020" >&2; exit 1; }
rm -rf "/private/tmp/dav978-git-30aa0020"
[ -d "/private/tmp/dav1051" ] || { echo "缺失中止: dav1051" >&2; exit 1; }
[ ! -e "/private/tmp/dav1051/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1051" >&2; exit 1; }
rm -rf "/private/tmp/dav1051"
[ -d "/private/tmp/dav986-parent.mcfS4D" ] || { echo "缺失中止: dav986-parent.mcfS4D" >&2; exit 1; }
[ ! -e "/private/tmp/dav986-parent.mcfS4D/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav986-parent.mcfS4D" >&2; exit 1; }
rm -rf "/private/tmp/dav986-parent.mcfS4D"
[ -d "/private/tmp/dav941-rtfull-base-r6" ] || { echo "缺失中止: dav941-rtfull-base-r6" >&2; exit 1; }
[ ! -e "/private/tmp/dav941-rtfull-base-r6/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav941-rtfull-base-r6" >&2; exit 1; }
rm -rf "/private/tmp/dav941-rtfull-base-r6"
[ -d "/private/tmp/dav1050" ] || { echo "缺失中止: dav1050" >&2; exit 1; }
[ ! -e "/private/tmp/dav1050/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1050" >&2; exit 1; }
rm -rf "/private/tmp/dav1050"
[ -d "/private/tmp/dav987-api-bisection-20260916-0500" ] || { echo "缺失中止: dav987-api-bisection-20260916-0500" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0500/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0500" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0500"
[ -d "/private/tmp/dav987-api-bisection-20260916-0155" ] || { echo "缺失中止: dav987-api-bisection-20260916-0155" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0155/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0155" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0155"
[ -d "/private/tmp/ta-rtfull-e30f312.amuIa6" ] || { echo "缺失中止: ta-rtfull-e30f312.amuIa6" >&2; exit 1; }
[ ! -e "/private/tmp/ta-rtfull-e30f312.amuIa6/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: ta-rtfull-e30f312.amuIa6" >&2; exit 1; }
rm -rf "/private/tmp/ta-rtfull-e30f312.amuIa6"
[ -d "/private/tmp/dav987-api-bisection-20260916-0750" ] || { echo "缺失中止: dav987-api-bisection-20260916-0750" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0750/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0750" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0750"
[ -d "/private/tmp/dav987-api-bisection-20260916-0130" ] || { echo "缺失中止: dav987-api-bisection-20260916-0130" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0130/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0130" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0130"
[ -d "/private/tmp/dav987-api-bisection-20260916-0320" ] || { echo "缺失中止: dav987-api-bisection-20260916-0320" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0320/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0320" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0320"
[ -d "/private/tmp/dav977-rtfull-20260916" ] || { echo "缺失中止: dav977-rtfull-20260916" >&2; exit 1; }
[ ! -e "/private/tmp/dav977-rtfull-20260916/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav977-rtfull-20260916" >&2; exit 1; }
rm -rf "/private/tmp/dav977-rtfull-20260916"
[ -d "/private/tmp/dav990-no-api-control" ] || { echo "缺失中止: dav990-no-api-control" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-no-api-control/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-no-api-control" >&2; exit 1; }
rm -rf "/private/tmp/dav990-no-api-control"
[ -d "/private/tmp/dav987-api-bisection-20260916-0540" ] || { echo "缺失中止: dav987-api-bisection-20260916-0540" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0540/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0540" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0540"
[ -d "/private/tmp/rtfull-dav1092" ] || { echo "缺失中止: rtfull-dav1092" >&2; exit 1; }
[ ! -e "/private/tmp/rtfull-dav1092/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: rtfull-dav1092" >&2; exit 1; }
rm -rf "/private/tmp/rtfull-dav1092"
[ -d "/private/tmp/dav987-api-bisection-20260916-0710" ] || { echo "缺失中止: dav987-api-bisection-20260916-0710" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0710/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0710" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0710"
[ -d "/private/tmp/dav990-rt16-control" ] || { echo "缺失中止: dav990-rt16-control" >&2; exit 1; }
[ ! -e "/private/tmp/dav990-rt16-control/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav990-rt16-control" >&2; exit 1; }
rm -rf "/private/tmp/dav990-rt16-control"
[ -d "/private/tmp/dav987-diag.XCjwH0" ] || { echo "缺失中止: dav987-diag.XCjwH0" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-diag.XCjwH0/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-diag.XCjwH0" >&2; exit 1; }
rm -rf "/private/tmp/dav987-diag.XCjwH0"
[ -d "/private/tmp/dav1009_selftest" ] || { echo "缺失中止: dav1009_selftest" >&2; exit 1; }
[ ! -e "/private/tmp/dav1009_selftest/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav1009_selftest" >&2; exit 1; }
rm -rf "/private/tmp/dav1009_selftest"
[ -d "/private/tmp/dav987-api-bisection-20260916-0630" ] || { echo "缺失中止: dav987-api-bisection-20260916-0630" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0630/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0630" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0630"
[ -d "/private/tmp/dav987-api-bisection-20260916-0830" ] || { echo "缺失中止: dav987-api-bisection-20260916-0830" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0830/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0830" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0830"
[ -d "/private/tmp/dav941-rtfull-candidate-r6" ] || { echo "缺失中止: dav941-rtfull-candidate-r6" >&2; exit 1; }
[ ! -e "/private/tmp/dav941-rtfull-candidate-r6/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav941-rtfull-candidate-r6" >&2; exit 1; }
rm -rf "/private/tmp/dav941-rtfull-candidate-r6"
[ -d "/private/tmp/dav987-api-bisection-20260916-0230" ] || { echo "缺失中止: dav987-api-bisection-20260916-0230" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0230/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0230" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0230"
[ -d "/private/tmp/dav987-api-bisection-20260916-0600" ] || { echo "缺失中止: dav987-api-bisection-20260916-0600" >&2; exit 1; }
[ ! -e "/private/tmp/dav987-api-bisection-20260916-0600/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav987-api-bisection-20260916-0600" >&2; exit 1; }
rm -rf "/private/tmp/dav987-api-bisection-20260916-0600"
[ -d "/private/tmp/dav-985-review" ] || { echo "缺失中止: dav-985-review" >&2; exit 1; }
[ ! -e "/private/tmp/dav-985-review/.git" ] || { echo "出现 .git，疑为重生成的活 worktree，中止: dav-985-review" >&2; exit 1; }
rm -rf "/private/tmp/dav-985-review"

AFTER=$(df -k / | tail -1 | awk '{print $4}')
echo "孤儿目录 完成，根卷可用空间变化 $(( (AFTER - BEFORE) / 1024 )) MiB"
# APFS 写时复制会让实际释放小于 du 汇总值，以 df 前后差为准
