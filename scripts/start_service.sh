#!/usr/bin/env bash
# TradingAgents-AShare 服务启动脚本（2026-09-20 固化）
# 背景：连续三次手工补环境变量重启（PID 17940 → 47354 → 53717），无环境快照证据。
# 本脚本把启动环境固化，禁止再手工补。
#
# 环境铁律（tradingagents-ashare-ops SKILL.md）：
#   1. env -u PYTHONPATH —— Hermes shell 的 PYTHONPATH 会污染项目 venv
#   2. 必须显式 DATABASE_URL —— 否则连错库
#   3. 必须带完整 no_proxy —— 否则国内数据源全走代理挂掉（2026-08-06 实锤）
#   4. no_proxy 必须含 Tailscale CGNAT 段 —— .env 的 TA_BASE_URL 与用户 backend_url
#      均为 Tailscale 字面量 IP（100.67.61.23 / 100.65.130.33），缺则 DAV-777
#      proxy guard fail-closed 拦截全部 LLM 调用（2026-09-20 DAV-1102 实锤）
set -euo pipefail

cd /Users/davidliu/Documents/TradingAgents-AShare

# 若已有监听则先停（等待端口释放）
OLD_PID=$(lsof -nP -iTCP:8000 -sTCP:LISTEN -t 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
  echo "停止旧进程: $OLD_PID"
  kill "$OLD_PID" || true
  for _ in $(seq 1 15); do
    lsof -nP -iTCP:8000 -sTCP:LISTEN -t >/dev/null 2>&1 || break
    sleep 1
  done
fi

NO_PROXY_LIST="localhost,127.0.0.1,100.65.130.33,100.67.61.23,100.64.0.0/10,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn"

nohup env -u PYTHONPATH \
  DATABASE_URL="sqlite:///./data/tradingagents.db" \
  http_proxy=http://127.0.0.1:7897 \
  https_proxy=http://127.0.0.1:7897 \
  all_proxy=socks5://127.0.0.1:7897 \
  no_proxy="$NO_PROXY_LIST" \
  NO_PROXY="$NO_PROXY_LIST" \
  .venv310/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 \
  > /tmp/ta-service.log 2>&1 &

echo "启动中 pid=$!，日志 /tmp/ta-service.log"
for _ in $(seq 1 30); do
  if curl -s -m 3 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "healthz OK:"
    curl -s -m 3 http://127.0.0.1:8000/healthz
    echo
    exit 0
  fi
  sleep 1
done
echo "ERROR: 30s 内 healthz 未就绪，查 /tmp/ta-service.log" >&2
exit 1
