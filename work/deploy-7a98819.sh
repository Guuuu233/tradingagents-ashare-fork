#!/usr/bin/env bash
# Deploy reason_codes machine-readability fix 7a98819 to local 8000.
# Evidence-first: exact trunk SHA, unique DB backup, clean worktree, exact healthz.
set -euo pipefail

REPO="/Users/davidliu/Documents/TradingAgents-AShare"
SHA="7a988197ef982fae95b6c9669234348e0632216a"
SERVE="/private/tmp/ta-serve-7a98819"
PY="$REPO/.venv310/bin/python"
DB="$REPO/data/tradingagents.db"
FRONT_DIST="$REPO/frontend/dist"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="/private/tmp/ta-serve-8000-${TS}-7a98819.log"
BAK="$REPO/work/tradingagents.db.bak-${TS}-deploy-7a98819"
NO_PROXY_HOSTS="100.65.130.33,100.67.61.23,92.119.124.146,127.0.0.1,localhost,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn"
SOCIAL_ENV_SNAPSHOT="/tmp/ta-social-env-${TS}.env"

fail(){ echo "ABORT: $*" >&2; exit 1; }

cd "$REPO"

echo "== 0. preconditions =="
[ -f "$REPO/.env" ] || fail ".env missing"
[ -x "$PY" ] || fail "Python 3.10 environment missing: $PY"
[ -f "$DB" ] || fail "production DB missing: $DB"
[ -d "$FRONT_DIST" ] || fail "frontend dist missing: $FRONT_DIST"
[ ! -e "$SERVE" ] || fail "serve directory already exists: $SERVE"
[ ! -e "$BAK" ] || fail "backup target already exists: $BAK"
# Social rollout is a runtime contract, not an implicit default. Preserve the
# existing deployed values across releases and fail closed on missing/drift.
# 服务当前停机，无活进程可继承环境；按上次部署既定值显式固定（shadow/xhs,dy/归档库路径）。
SOCIAL_MODE="shadow"
SOCIAL_PLATFORMS="xhs,dy"
SOCIAL_ARCHIVE_DB="/Users/davidliu/Documents/TradingAgents-AShare/data/social_archive.db"
[ "$SOCIAL_MODE" = "shadow" ] || fail "TA_SOCIAL_MODE must remain shadow; got '${SOCIAL_MODE:-missing}'"
[ "$SOCIAL_PLATFORMS" = "xhs,dy" ] || fail "TA_SOCIAL_PLATFORMS must remain xhs,dy; got '${SOCIAL_PLATFORMS:-missing}'"
[ -n "$SOCIAL_ARCHIVE_DB" ] || fail "TA_SOCIAL_ARCHIVE_DB missing"
[ -f "$SOCIAL_ARCHIVE_DB" ] || fail "social archive DB missing: $SOCIAL_ARCHIVE_DB"
SOCIAL_ARCHIVE_DB="$SOCIAL_ARCHIVE_DB"
ACTIVE=$("$PY" - "$DB" <<'PYEOF'
import sqlite3, sys
s=sqlite3.connect(sys.argv[1])
print(s.execute("SELECT COUNT(*) FROM reports WHERE status IN ('pending','running')").fetchone()[0])
PYEOF
)
[ "$ACTIVE" = "0" ] || fail "active reports present: $ACTIVE"


echo "== 1. verify remote trunk =="
REMOTE=$(git ls-remote origin refs/heads/codex/dav-4-p2a-trunk | awk '{print $1}')
[ "$REMOTE" = "$SHA" ] || fail "remote trunk=$REMOTE != $SHA"
echo "remote trunk OK: $REMOTE"


echo "== 2. backup production DB =="
"$PY" - "$DB" "$BAK" <<'PYEOF'
import hashlib, sqlite3, sys
src, dst = sys.argv[1:]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
s.backup(d)
def snap(db):
    return (
        db.execute("PRAGMA quick_check").fetchone()[0],
        db.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
        db.execute("SELECT COUNT(*) FROM reports WHERE status='completed'").fetchone()[0],
        db.execute("SELECT COUNT(*) FROM reports WHERE status='failed'").fetchone()[0],
    )
a, b = snap(s), snap(d)
if a != b:
    raise SystemExit(f"backup mismatch: source={a} backup={b}")
print("backup_ok", a)
print("backup_sha256", hashlib.sha256(open(dst, "rb").read()).hexdigest())
d.close(); s.close()
PYEOF


echo "== 3. stop old listener and verify port free =="
OLD_PIDS=$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
  echo "stopping old listener(s): $OLD_PIDS"
  kill -9 $OLD_PIDS 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi
lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1 && fail "port 8000 still in use"


echo "== 4. create clean serve worktree =="
git worktree add --detach "$SERVE" "$SHA"
cp "$REPO/.env" "$SERVE/.env"
# Preserve the three live social rollout settings even when .env has no keys.
{
  printf '\nTA_SOCIAL_MODE=%s\n' "$SOCIAL_MODE"
  printf 'TA_SOCIAL_ARCHIVE_DB=%s\n' "$SOCIAL_ARCHIVE_DB"
  printf 'TA_SOCIAL_PLATFORMS=%s\n' "$SOCIAL_PLATFORMS"
} >> "$SERVE/.env"
mkdir -p "$SERVE/data" "$SERVE/frontend"
ln -sfn "$DB" "$SERVE/data/tradingagents.db"
cp -a "$FRONT_DIST" "$SERVE/frontend/dist"
[ -f "$SERVE/frontend/dist/index.html" ] || fail "frontend dist copy incomplete"


echo "== 5. start detached service =="
cd "$SERVE"
env -u PYTHONPATH \
  DATABASE_URL="sqlite:///./data/tradingagents.db" \
  no_proxy="$NO_PROXY_HOSTS" NO_PROXY="$NO_PROXY_HOSTS" \
  nohup perl -e 'use POSIX qw(setsid); setsid(); exec @ARGV' -- \
  "$PY" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 \
  --log-config api/logging_config.yaml > "$LOG" 2>&1 < /dev/null &
SPID=$!
echo "started pid=$SPID log=$LOG"


echo "== 6. verify exact health identity =="
RESP=""
for attempt in $(seq 1 60); do
  kill -0 "$SPID" 2>/dev/null || { tail -80 "$LOG"; fail "uvicorn died during startup"; }
  if RESP=$(curl -fsS --max-time 3 http://127.0.0.1:8000/healthz 2>/dev/null); then
    echo "healthz_ready_after=${attempt}s"
    break
  fi
  sleep 1
done
[ -n "$RESP" ] || { tail -80 "$LOG"; fail "healthz not ready after 60s"; }
echo "$RESP"
"$PY" - "$SHA" "$RESP" <<'PYEOF'
import json, sys
expected, raw = sys.argv[1:]
payload = json.loads(raw)
if payload.get("status") != "ok" or payload.get("commit_sha") != expected:
    raise SystemExit(f"healthz identity mismatch: {payload}")
print("healthz_identity_ok", payload["commit_sha"])
PYEOF

ROOT_CODE=$(curl -sS -o /dev/null --max-time 10 -w '%{http_code}' http://127.0.0.1:8000/)
REPORTS_CODE=$(curl -sS -o /dev/null --max-time 10 -w '%{http_code}' http://127.0.0.1:8000/v1/reports)
PROBE_CODE=$(curl -sS -o /dev/null --max-time 10 -w '%{http_code}' http://127.0.0.1:8000/v1/__release_probe__)
[ "$ROOT_CODE" = "200" ] || fail "frontend root returned $ROOT_CODE"
[ "$REPORTS_CODE" = "200" ] || fail "reports endpoint returned $REPORTS_CODE"
[ "$PROBE_CODE" = "404" ] || fail "API 404 guard returned $PROBE_CODE"
# TA_SOCIAL vars are loaded from .env via dotenv — they do NOT appear in the
# process environment, so `ps eww` is a false-negative check (proven 2026-09-19).
# Gate on the .env file contents instead; the /v1/social-data/status check below
# remains the authoritative runtime verification.
grep -q '^TA_SOCIAL_MODE=shadow$' "$SERVE/.env" || fail "serve .env TA_SOCIAL_MODE is not shadow"
grep -q '^TA_SOCIAL_PLATFORMS=xhs,dy$' "$SERVE/.env" || fail "serve .env TA_SOCIAL_PLATFORMS is not xhs,dy"
grep -q '^TA_SOCIAL_ARCHIVE_DB=' "$SERVE/.env" || fail "serve .env TA_SOCIAL_ARCHIVE_DB missing"
SOCIAL_STATUS=$(curl -fsS --max-time 10 http://127.0.0.1:8000/v1/social-data/status) || fail "social status endpoint unavailable"
"$PY" - "$SOCIAL_STATUS" <<'PYEOF'
import json, sys

payload = json.loads(sys.argv[1])
if payload.get("mode") != "shadow":
    raise SystemExit(f"social mode mismatch: {payload}")
if payload.get("status") != "operational":
    raise SystemExit(f"social status not operational: {payload}")
coverage = payload.get("platform_coverage") or {}
for platform in ("xhs", "dy"):
    if (coverage.get(platform) or {}).get("status") != "operational":
        raise SystemExit(f"social platform not operational: {platform}: {payload}")
availability = payload.get("analysis_availability") or {}
if availability.get("mode") != "shadow" or availability.get("available") is not True:
    raise SystemExit(f"social analysis availability mismatch: {payload}")
print("social_status_ok", payload["mode"], payload["status"])
PYEOF
"$PY" - "$SOCIAL_ARCHIVE_DB" <<'PYEOF'
import sqlite3, sys

db = sqlite3.connect(sys.argv[1])
try:
    quick = db.execute("PRAGMA quick_check").fetchone()[0]
    if quick != "ok":
        raise SystemExit(f"social archive quick_check failed: {quick}")
    snapshots = db.execute("SELECT COUNT(*) FROM social_record_snapshots").fetchone()[0]
    print("social_archive_ok", quick, "snapshots", snapshots)
finally:
    db.close()
PYEOF
echo "frontend_root_ok=$ROOT_CODE reports_ok=$REPORTS_CODE api_404_guard=$PROBE_CODE social_shadow_preserved=true"
echo "DEPLOYED_SHA=$SHA PID=$SPID LOG=$LOG"
