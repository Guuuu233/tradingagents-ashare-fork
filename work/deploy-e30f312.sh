#!/usr/bin/env bash
# Deploy the reviewed DAV-1048 integration to local 8000.
# Preserves the already-built frontend bundle and the host production DB.
set -euo pipefail

REPO="/Users/davidliu/Documents/TradingAgents-AShare"
SHA="6ee148699339efefc2f7f7548eb286be485524e1"
SERVE="/private/tmp/ta-serve-6ee1486"
OLD_SERVE="/private/tmp/ta-serve-e30f312"
PY="$REPO/.venv310/bin/python"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="/private/tmp/ta-serve-8000-${TS}-6ee1486.log"
DB="$REPO/data/tradingagents.db"
BAK="$REPO/work/tradingagents.db.bak-${TS}-deploy-6ee1486"
FRONT_DIST="$OLD_SERVE/frontend/dist"
LLM_HOST="100.65.130.33"
NO_PROXY_HOSTS="${LLM_HOST},127.0.0.1,localhost,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn"

fail(){ echo "ABORT: $*" >&2; exit 1; }

cd "$REPO"
[ -f "$REPO/.env" ] || fail ".env missing"
[ -x "$PY" ] || fail "Python 3.10 environment missing"
[ -f "$DB" ] || fail "production DB missing"
[ ! -e "$BAK" ] || fail "backup target already exists"
[ ! -e "$SERVE" ] || fail "release directory already exists: $SERVE"
[ -d "$FRONT_DIST" ] || fail "existing frontend dist missing: $FRONT_DIST"
lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1 && fail "port 8000 is still in use"

git fetch origin -q
REMOTE=$(git ls-remote origin refs/heads/codex/dav-4-p2a-trunk | awk '{print $1}')
[ "$REMOTE" = "$SHA" ] || fail "remote trunk=$REMOTE != $SHA"

"$PY" - "$DB" "$BAK" <<'PYEOF'
import hashlib, sqlite3, sys
src, dst = sys.argv[1:]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
s.backup(d)
source = (s.execute("PRAGMA quick_check").fetchone()[0],
          s.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
          s.execute("SELECT COUNT(*) FROM reports WHERE status='completed'").fetchone()[0],
          s.execute("SELECT COUNT(*) FROM reports WHERE status='failed'").fetchone()[0])
backup = (d.execute("PRAGMA quick_check").fetchone()[0],
          d.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
          d.execute("SELECT COUNT(*) FROM reports WHERE status='completed'").fetchone()[0],
          d.execute("SELECT COUNT(*) FROM reports WHERE status='failed'").fetchone()[0])
if source != backup:
    raise SystemExit(f"backup mismatch: source={source} backup={backup}")
print("backup_ok", source)
print("backup_sha256", hashlib.sha256(open(dst, "rb").read()).hexdigest())
d.close(); s.close()
PYEOF

git worktree add --detach "$SERVE" "$SHA"
cp "$REPO/.env" "$SERVE/.env"
mkdir -p "$SERVE/data" "$SERVE/frontend"
ln -sfn "$DB" "$SERVE/data/tradingagents.db"
cp -a "$FRONT_DIST" "$SERVE/frontend/dist"

cd "$SERVE"
env -u PYTHONPATH \
  DATABASE_URL="sqlite:///./data/tradingagents.db" \
  no_proxy="$NO_PROXY_HOSTS" NO_PROXY="$NO_PROXY_HOSTS" \
  nohup perl -e 'use POSIX qw(setsid); setsid(); exec @ARGV' -- \
  "$PY" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 \
  --log-config api/logging_config.yaml > "$LOG" 2>&1 < /dev/null &
SPID=$!
echo "started pid=$SPID log=$LOG"

RESP=""
for ((attempt=1; attempt<=60; attempt++)); do
  kill -0 "$SPID" 2>/dev/null || { tail -60 "$LOG"; fail "uvicorn died"; }
  if RESP=$(curl -fsS --max-time 3 http://127.0.0.1:8000/healthz 2>/dev/null); then
    echo "healthz_ready_after=${attempt}s"
    break
  fi
  sleep 1
done
[ -n "$RESP" ] || { tail -60 "$LOG"; fail "healthz not ready after 60s"; }
echo "$RESP"
"$PY" - "$SHA" "$RESP" <<'PYEOF'
import json, sys
expected, raw = sys.argv[1:]
payload = json.loads(raw)
if payload.get("status") != "ok" or payload.get("commit_sha") != expected:
    raise SystemExit(f"healthz identity mismatch: {payload}")
print("healthz_identity_ok", payload["commit_sha"])
PYEOF

ROOT_CODE=$(curl -sS -o /tmp/ta-6ee-root-response --max-time 10 -w '%{http_code}' http://127.0.0.1:8000/)
API_CODE=$(curl -sS -o /tmp/ta-6ee-api-response --max-time 10 -w '%{http_code}' http://127.0.0.1:8000/v1/__release_probe__)
[ "$ROOT_CODE" = "200" ] || fail "frontend root returned $ROOT_CODE"
[ "$API_CODE" = "404" ] || fail "API 404 protection returned $API_CODE"
echo "frontend_root_ok status=$ROOT_CODE"
echo "api_404_guard_ok status=$API_CODE"
echo "deployed_sha=$SHA pid=$SPID"
