#!/usr/bin/env python3
"""DAV-1641 wrapper: run DAV-1528 ta_snapshot.py ONLY after the daily batch.

Fired by launchd com.tradingagents.ta-snapshot every 10 min + 19:30 kick.
Waits for work/daily-batch/<trade-date>/summary.json (run_daily.py's last
write), then runs scripts/ta_snapshot.py once per date (done-<date>.ok).

总控三条附加条件（全部在包装层实现，主干脚本 618b97d 未改）:
  1. df <60GB → 不建快照 + 写 ALERT-disk-<date>.log 报警 + 非零退出
     （总控 10-16 令：阈值与 60 GiB 空间底线统一，DAV-2162）。
  2. 首跑报耗时与内存；若 >30min，删除当日 .zst 后按 19→15→12→9 降级重压
     （shim 强制 zstd 级别，不改脚本），实际级别写入 JSON zstd_level_used。
  3. 输出 ~/ta-snapshot（DAV-2017 迁移：自 /private/tmp/ta-snapshot 迁出，
     避免 macOS /tmp 清理丢文件）。current.db 每交易日被刷新+读取、.zst 仅
     3 份且 retention 在写入后立即执行。持久基线由 DAV-1508 周任务上传夸克（2 份）。
"""
import json, os, re, resource, shutil, subprocess, sys, tempfile, time
from datetime import datetime, timedelta

ROOT = "/Users/davidliu/Documents/TradingAgents-AShare"
PY = os.path.join(ROOT, ".venv310/bin/python")
SCRIPT = "/Users/davidliu/ta-work/dav-snap-8571/scripts/ta_snapshot.py"
BATCH_DIR = os.path.join(ROOT, "work/daily-batch")
OUT = os.path.expanduser("~/ta-snapshot")
HERE = "/Users/davidliu/ta-work/dav-snap-8571/work/ta-snapshot"
DB = os.path.join(ROOT, "data", "tradingagents.db")  # live DB stays in main checkout
MIN_FREE_GB = 60  # DAV-2162: aligned with the 60 GiB disk floor (was 100)
LEVELS = [19, 15, 12, 9]
TIME_LIMIT = 1800
GIVEUP_HOUR = 3

os.makedirs(OUT, exist_ok=True)
os.makedirs(HERE, exist_ok=True)
os.chdir(ROOT)


def log(m):
    print(f"{datetime.now().isoformat(timespec='seconds')} {m}", flush=True)


def alert(name, msg):
    path = os.path.join(HERE, f"ALERT-{name}-{datetime.now():%F}.log")
    with open(path, "w") as f:
        f.write(msg + "\n")
    log(msg)


def find_batch_summary():
    for d in (datetime.now().date(),
              datetime.now().date() - timedelta(days=1)):
        s = os.path.join(BATCH_DIR, d.isoformat(), "summary.json")
        if os.path.exists(s):
            return s, d.isoformat()
    return None, None


def main():
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin"
    os.environ.pop("PYTHONPATH", None)
    now = datetime.now()
    summary, batch_date = find_batch_summary()
    if not summary:
        if now.hour >= GIVEUP_HOUR and now.hour < 12:
            a = os.path.join(HERE, f"ALERT-batch-missing-{now:%F}.log")
            if not os.path.exists(a):
                alert("batch-missing", f"no daily-batch summary for {now:%F} or yesterday by {GIVEUP_HOUR}:00")
        return 0                       # batch still running / non-batch day
    done = os.path.join(HERE, f"done-{batch_date}.ok")
    if os.path.exists(done):
        return 0

    free_gb = shutil.disk_usage(OUT).free / 2**30
    if free_gb < MIN_FREE_GB:
        alert("disk", f"RED-ZONE {free_gb:.0f}GB free (<{MIN_FREE_GB}GB) — snapshot skipped")
        return 3
    log(f"batch done ({summary}); disk {free_gb:.0f}GB free; snapshot date={batch_date}")

    # zstd shim: force level via TA_SNAPSHOT_LEVEL, keeping signed script byte-identical
    shim = os.path.join(HERE, "zstd-shim")
    os.makedirs(shim, exist_ok=True)
    with open(os.path.join(shim, "zstd"), "w") as f:
        f.write('#!/bin/bash\nlvl=${TA_SNAPSHOT_LEVEL:-19}\nargs=()\n'
                'for a in "$@"; do case "$a" in -[0-9]*) args+=("-${lvl}");; *) args+=("$a");; esac; done\n'
                'exec /opt/homebrew/bin/zstd "${args[@]}"\n')
    os.chmod(os.path.join(shim, "zstd"), 0o755)

    env = dict(os.environ, PATH=f"{shim}:/opt/homebrew/bin:/usr/bin:/bin")
    snap = os.path.join(OUT, f"tradingagents-{batch_date}.db.zst")
    jpath = os.path.join(HERE, "last_run.json")
    used, elapsed, report = None, 0, {}
    for lvl in LEVELS:
        t0 = time.time()
        r = subprocess.run(
            [PY, SCRIPT, "--date", batch_date, "--out-dir", OUT, "--db", DB],
            env={**env, "TA_SNAPSHOT_LEVEL": str(lvl)},
            capture_output=True, text=True, cwd=ROOT)
        elapsed = int(time.time() - t0)
        with open(jpath, "w") as f: f.write(r.stdout)
        with open(os.path.join(HERE, "last_run.err"), "w") as f: f.write(r.stderr)
        if r.returncode != 0:
            log(f"ta_snapshot.py rc={r.returncode} at level={lvl}: {r.stderr.strip()[-300:]}")
            return r.returncode
        report = json.loads(r.stdout)
        used = lvl
        log(f"level={lvl} rc=0 elapsed={elapsed}s")
        if report.get("skipped") == "already_exists":
            break
        if elapsed <= TIME_LIMIT:
            break
        log(f"level={lvl} took {elapsed}s (>30min) — degrading")
        if os.path.exists(snap):
            os.remove(snap)

    report["zstd_level_used"] = used
    report["wrapper_elapsed_seconds"] = elapsed
    report["wrapper_max_rss_bytes"] = resource.getrusage(
        resource.RUSAGE_CHILDREN).ru_maxrss
    with open(jpath, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    open(done, "w").write(f"level={used} elapsed={elapsed}s\n")
    log(f"DONE level={used} elapsed={elapsed}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
