#!/usr/bin/env bash
# Deploy df753841 to local 8000 — RUN ONLY AFTER David lifts deploy freeze.
# Evidence-first: unique backup, clean worktree, detached start, exact SHA healthz check.
# Fails fast: any step error aborts before service start.
set -euo pipefail

REPO="/Users/davidliu/Documents/TradingAgents-AShare"
SHA="df7538413ba7bb55593b1757feaf90b0bd514d1c"
SERVE="/private/tmp/ta-serve-df753841"
PY="$REPO/.venv310/bin/python"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="/private/tmp/ta-serve-8000-${TS}.log"
DB="$REPO/data/tradingagents.db"
BAK="$REPO/work/tradingagents.db.bak-${TS}-deploy-df753841"   # unique per-run, no overwrite
LLM_HOST="100.65.130.33"   # real LLM backend — must bypass proxy
NO_PROXY_HOSTS="${LLM_HOST},127.0.0.1,localhost,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,.eastmoney.com,.sina.com.cn,.sinaimg.cn,.tencent.com,.qq.com,.gtimg.cn,.baostock.com,.akshare.xyz,.10jqka.com.cn,.cninfo.com.cn,.csindex.com.cn,.fuyao.aicubes.cn,.cnstock.com,.xinhuanet.com,.people.com.cn,.gov.cn"

fail(){ echo "ABORT: $*" >&2; exit 1; }

cd "$REPO"

echo "== 0. preconditions =="
[ -f "$REPO/.env" ] || fail ".env missing — cannot start without config"
[ -x "$PY" ] || fail "venv python missing: $PY"
[ -f "$DB" ] || fail "production DB missing: $DB"
[ ! -e "$BAK" ] || fail "backup target already exists: $BAK"
lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1 && fail "port 8000 already in use"

echo "== 1. verify remote trunk pinned to $SHA =="
git fetch origin -q
REMOTE=$(git ls-remote origin refs/heads/codex/dav-4-p2a-trunk | awk '{print $1}')
[ "$REMOTE" = "$SHA" ] || fail "remote trunk=$REMOTE != $SHA"
echo "remote trunk OK: $REMOTE"

echo "== 2. backup production DB (unique) =="
"$PY" - "$DB" "$BAK" <<'PYEOF'
import sqlite3,sys,hashlib
src,dst=sys.argv[1],sys.argv[2]
s=sqlite3.connect(src);d=sqlite3.connect(dst)
s.backup(d)
qc_src=s.execute("PRAGMA quick_check").fetchone()[0]
n_src=s.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
c_src=s.execute("SELECT COUNT(*) FROM reports WHERE status='completed'").fetchone()[0]
f_src=s.execute("SELECT COUNT(*) FROM reports WHERE status='failed'").fetchone()[0]
qc_dst=d.execute("PRAGMA quick_check").fetchone()[0]
n_dst=d.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
c_dst=d.execute("SELECT COUNT(*) FROM reports WHERE status='completed'").fetchone()[0]
f_dst=d.execute("SELECT COUNT(*) FROM reports WHERE status='failed'").fetchone()[0]
if (qc_src, n_src, c_src, f_src) != (qc_dst, n_dst, c_dst, f_dst):
    raise SystemExit(
        f"backup mismatch: source={(qc_src, n_src, c_src, f_src)} "
        f"backup={(qc_dst, n_dst, c_dst, f_dst)}"
    )
print("source_and_backup_ok", "quick_check",qc_src,"reports",n_src,"completed",c_src,"failed",f_src)
d.close()
s.close()
print("bak_sha256",hashlib.sha256(open(dst,'rb').read()).hexdigest())
PYEOF
[ -f "$BAK" ] || fail "backup not written"

echo "== 3. build clean serve worktree =="
[ -d "$SERVE" ] && fail "serve dir exists: $SERVE (remove manually to redeploy)"
git worktree add --detach "$SERVE" "$SHA"
"$PY" - "$SERVE" "$SHA" <<'PYEOF'
import subprocess,sys
serve,expected=sys.argv[1:]
actual=subprocess.check_output(["git","-C",serve,"rev-parse","HEAD"],text=True).strip()
if actual != expected:
    raise SystemExit(f"worktree SHA mismatch: {actual} != {expected}")
print("worktree_sha_ok", actual)
PYEOF
cp "$REPO/.env" "$SERVE/.env"
mkdir -p "$SERVE/data"
ln -sfn "$DB" "$SERVE/data/tradingagents.db"   # serve uses host DB via symlink (prior-deploy pattern)

echo "== 4. start detached uvicorn =="
cd "$SERVE"
env -u PYTHONPATH \
  DATABASE_URL="sqlite:///./data/tradingagents.db" \
  no_proxy="$NO_PROXY_HOSTS" \
  NO_PROXY="$NO_PROXY_HOSTS" \
  nohup perl -e 'use POSIX qw(setsid); setsid(); exec @ARGV' -- \
  "$PY" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 \
  --log-config api/logging_config.yaml > "$LOG" 2>&1 < /dev/null &
SPID=$!
echo "started pid=$SPID log=$LOG"

echo "== 5. verify /healthz returns EXACT $SHA =="
RESP=""
for ((attempt=1; attempt<=60; attempt++)); do
  kill -0 "$SPID" 2>/dev/null || { echo "== startup log =="; tail -60 "$LOG"; fail "uvicorn died at startup"; }
  if RESP=$(curl -fsS --max-time 3 http://127.0.0.1:8000/healthz 2>/dev/null); then
    echo "healthz ready after ${attempt}s"
    break
  fi
  sleep 1
done
[ -n "$RESP" ] || { tail -60 "$LOG"; fail "healthz not ready after 60s"; }
echo "$RESP"
"$PY" - "$SHA" "$RESP" <<'PYEOF'
import json,sys
expected,raw=sys.argv[1:]
try:
    payload=json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"healthz is not JSON: {exc}")
if payload.get("status") != "ok":
    raise SystemExit(f"healthz status is not ok: {payload}")
if payload.get("commit_sha") != expected:
    raise SystemExit(f"healthz SHA mismatch: {payload.get('commit_sha')} != {expected}")
print("healthz_identity_ok", payload["commit_sha"])
PYEOF
echo "== DEPLOYED $SHA pid=$SPID =="
