#!/bin/bash
# DAV-1009 诊断前置门禁（主动中止版）
#
# 用途：在任何诊断复现之前执行。不满足条件【立即退出】，不再往下跑，
#      避免又一次在脏工作树上取证、事后才发现证据作废。
#
# 用法：
#   bash work/dav1009-preflight-gate.sh <诊断日志绝对路径> <工作树路径>
#   门禁通过后，把【同一份日志】继续用于诊断输出：
#     ... pytest ... >> "$LOG" 2>&1
#
# 设计要点：
#   - 门禁输出、时间戳、诊断过程【写入同一份唯一日志】，证据自带自证
#   - 用 `git diff --stat HEAD` 同时覆盖【已暂存 + 未暂存】改动
#     （只用 `git diff --stat` 会漏掉已 git add 的改动）

set -u

LOG="${1:?用法: $0 <日志绝对路径> <工作树路径>}"
TREE="${2:?用法: $0 <日志绝对路径> <工作树路径>}"
EXPECTED_HEAD="8589e6526e470d6da056a865f21ffa1532952b8e"

cd "$TREE" || { echo "工作树不存在: $TREE" | tee -a "$LOG"; exit 2; }

{
  echo "==================== PREFLIGHT GATE ===================="
  echo "时间戳    : $(date '+%F %T %z')"
  echo "工作树    : $TREE"
  echo "期望 HEAD : $EXPECTED_HEAD"
} | tee -a "$LOG"

HEAD_NOW=$(git rev-parse HEAD)
echo "实测 HEAD : $HEAD_NOW" | tee -a "$LOG"

# ① HEAD 必须完全等于期望的完整 SHA
if [ "$HEAD_NOW" != "$EXPECTED_HEAD" ]; then
    echo "🔴 GATE FAIL: HEAD 不等于 $EXPECTED_HEAD —— 立即退出，不进行诊断" | tee -a "$LOG"
    exit 1
fi

# ② 工作树必须完全干净（含未跟踪文件）
STATUS=$(git status --porcelain --untracked-files=all)
if [ -n "$STATUS" ]; then
    echo "🔴 GATE FAIL: 工作树非空 —— 立即退出，不进行诊断" | tee -a "$LOG"
    echo "$STATUS" | tee -a "$LOG"
    exit 1
fi
echo "status    : 空 ✅" | tee -a "$LOG"

# ③ diff --stat HEAD：同时覆盖已暂存与未暂存
DIFFSTAT=$(git diff --stat HEAD)
if [ -n "$DIFFSTAT" ]; then
    echo "🔴 GATE FAIL: git diff --stat HEAD 非空（含已暂存改动）—— 立即退出" | tee -a "$LOG"
    echo "$DIFFSTAT" | tee -a "$LOG"
    exit 1
fi

{
  echo "diff HEAD : 空 ✅（已覆盖 staged + unstaged）"
  echo "白名单    : $(git diff --name-only 5a0320f..HEAD | tr '\n' ' ')"
  echo "✅ GATE PASS —— 以下为诊断输出，与本门禁同属一份日志"
  echo "========================================================"
} | tee -a "$LOG"

exit 0
