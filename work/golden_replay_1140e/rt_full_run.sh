#!/usr/bin/env bash
# DAV-1179 RT-FULL 门禁执行器：同一环境对 parent/candidate 各跑一次全量回归。
#
# 用法:
#   rt_full_run.sh <label> <repo_root> <out_dir>
#     label     —— 例如 parent / candidate
#     repo_root —— 被测 checkout（candidate 为工作区，parent 建议 git worktree）
#     out_dir   —— 产物目录（junit xml + meta.json + tail 日志）
#
# 铁律落实：
#   - 锁定解释器 .venv310/bin/python 且 env -u PYTHONPATH；
#   - 清空代理，避免请求走本机代理挂死；
#   - DATABASE_URL 指向隔离临时库，绝不写 data/tradingagents.db；
#   - 追加主干既有死锁用例的 deselect（DAV-979），不计新增失败；
#   - 绝对 passed/failed 只作环境旁证，门禁字段由 rt_full_compare.py 计算
#     new_failures_relative_to_parent。
set -uo pipefail

LABEL="${1:?label required (parent|candidate)}"
ROOT="${2:?repo_root required}"
OUT="${3:?out_dir required}"
TARGET="${4:-tests}"   # 默认全量 tests；可传单测文件做冒烟
PY="/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"
DESELECT="tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields"

mkdir -p "$OUT"
DB="$OUT/${LABEL}_rtfull.db"
rm -f "$DB"

unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY || true

env -u PYTHONPATH "$PY" -V > "$OUT/${LABEL}.python_version.txt" 2>&1
git -C "$ROOT" rev-parse HEAD > "$OUT/${LABEL}.head_sha.txt"
git -C "$ROOT" status --porcelain > "$OUT/${LABEL}.worktree_status.txt" || true

cd "$ROOT"
env -u PYTHONPATH DATABASE_URL="sqlite:///$DB" \
  "$PY" -m pytest "$TARGET" -q -p no:randomly \
  --deselect "$DESELECT" \
  --junitxml="$OUT/${LABEL}.junit.xml" \
  > "$OUT/${LABEL}.pytest.log" 2>&1
rc=$?

tail -25 "$OUT/${LABEL}.pytest.log" > "$OUT/${LABEL}.tail.txt"
echo "exit=$rc  junit=$OUT/${LABEL}.junit.xml"
exit $rc
