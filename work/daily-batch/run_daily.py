#!/usr/bin/env python3
"""每日测量批（总控 2026-10-02，David 批准常态化；D-065 后续 / D-066 A2）。

每个交易日收盘后跑 24 份双档，固定账户；股票按交易日轮换 D-065 的 A/B 两组（各 24 只）。
目标日 = 最近一个已收盘（16:00 后）的交易日，且不早于 2026-10-08；同一目标日只跑一次，可断点续跑。
由 launchd（com.davidliu.ta-daily-batch）每个工作日 16:37 触发；非交易日直接退出。
用法：run_daily.py [--date YYYY-MM-DD] [--dry-run] [--base URL] [--root DIR]

--base / 环境变量 TA_DAILY_BASE 决定被调用的服务地址（默认仍为生产 127.0.0.1:8000）；
--root / TA_DAILY_ROOT 决定代码根目录（数据库、交易日历、账户文件均在其下）。
两者都可在运行时覆盖，使本脚本可在指向桩服务时被测试，而不会触达生产。

登录账户：优先 --account / 环境变量 TA_DAILY_ACCOUNT；未设置时才回退到旧路径
$ROOT/work/hist-bench-20260926/run.py 里的 EMAIL 赋值——该文件未入版本库，
不可靠，且取不到时不再抛 KeyError，而是明确报错并以 3 退出。launchd 未配置该
变量，故生产仍走旧路径（行为不变）；建议后续在 plist 中显式配置 TA_DAILY_ACCOUNT。
退出码：0=正常或按设计跳过；2=参数错误；3=健康检查/登录校验失败（批次未开始）；4=批次执行中出现失败。
"""
import argparse
import fcntl, json, os, re, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import requests

ROOT = os.environ.get("TA_DAILY_ROOT", "/Users/davidliu/Documents/TradingAgents-AShare")
BASE = os.environ.get("TA_DAILY_BASE", "http://127.0.0.1:8000")
# 登录账户的显式配置；未设置时 resolve_account() 回退到旧 bench 脚本。
ACCOUNT_EMAIL = os.environ.get("TA_DAILY_ACCOUNT")
DB = f"{ROOT}/data/tradingagents.db"
UID = "429163f7-50b6-4982-8bdf-96ae99506843"
CAL = f"{ROOT}/.venv310/lib/python3.10/site-packages/akshare/file_fold/calendar.json"
HERE = os.path.dirname(os.path.abspath(__file__))
START = "2026-10-08"
CLOSE_HOUR = 16
MAXC = int(os.environ.get("MAXC", "4"))
STAGGER = int(os.environ.get("STAGGER", "60"))
GROUP_A = ["600036.SH", "600519.SH", "300750.SZ", "601899.SH", "002594.SZ", "601318.SH", "000858.SZ", "600900.SH",
           "688981.SH", "600276.SH", "000333.SZ", "601012.SH", "600941.SH", "601857.SH", "600030.SH", "002475.SZ",
           "601088.SH", "600887.SH", "600309.SH", "600031.SH", "002415.SZ", "601888.SH", "603259.SH", "000725.SZ"]
GROUP_B = ["601398.SH", "601628.SH", "600809.SH", "000651.SZ", "300274.SZ", "601127.SH", "603993.SH", "600905.SH",
           "600196.SH", "002371.SZ", "600028.SH", "000063.SZ", "601225.SH", "600690.SH", "600585.SH", "601919.SH",
           "600104.SH", "002304.SZ", "600438.SH", "300760.SZ", "603501.SH", "601390.SH", "600048.SH", "000002.SZ"]


def trading_days():
    return sorted(f"{s[:4]}-{s[4:6]}-{s[6:8]}" for s in (str(x).replace("-", "") for x in json.load(open(CAL))))


def target_date(days, now):
    today = now.strftime("%Y-%m-%d")
    done = [d for d in days if d < today or (d == today and now.hour >= CLOSE_HOUR)]
    return done[-1] if done else None


EXIT_OK, EXIT_USAGE, EXIT_UNHEALTHY, EXIT_BATCH = 0, 2, 3, 4
LEGACY_ACCOUNT_FILE = "{root}/work/hist-bench-20260926/run.py"


def parse_args(argv):
    """--base/--root make the service and code root overridable at runtime."""
    parser = argparse.ArgumentParser(
        description="每日测量批：固定账户、每交易日收盘后跑一轮。")
    parser.add_argument("--date", help="目标交易日 YYYY-MM-DD，默认取最近已收盘交易日")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不发起任何请求")
    parser.add_argument("--base", default=BASE, help=f"分析服务地址（默认 {BASE}）")
    parser.add_argument("--root", default=ROOT, help=f"代码根目录（默认 {ROOT}）")
    parser.add_argument("--account", default=ACCOUNT_EMAIL,
                        help=f"登录账户邮箱（默认取 $TA_DAILY_ACCOUNT，再回退到 {LEGACY_ACCOUNT_FILE}）")
    return parser.parse_args(argv)


def resolve_account(explicit, root):
    """返回登录账户；解析不出来时抛 ValueError（调用方转为 3 退出）。

    旧路径从 hist-bench 脚本源码里切 EMAIL 赋值：文件未入版本库、不稳定，
    只能作为兼容回退，且切不出时必须有明确报错，不能退化成静默空值。
    """
    if explicit:
        return explicit.strip()
    try:
        source = Path(LEGACY_ACCOUNT_FILE.format(root=root)).read_text(encoding="utf-8")
        match = re.search(r'EMAIL\s*=\s*"([^"]+)"', source)
    except OSError as e:
        raise ValueError(
            f"cannot read the fallback account file "
            f"{LEGACY_ACCOUNT_FILE.format(root=root)}: {e}. "
            f"Set TA_DAILY_ACCOUNT or pass --account."
        ) from e
    if not match:
        raise ValueError(
            f"no EMAIL = \"...\" assignment found in "
            f"{LEGACY_ACCOUNT_FILE.format(root=root)}. "
            f"Set TA_DAILY_ACCOUNT or pass --account."
        )
    return match.group(1)


def main():
    opts = parse_args(sys.argv[1:])
    global BASE, ROOT, DB, CAL
    BASE, ROOT = opts.base, opts.root
    DB = f"{ROOT}/data/tradingagents.db"
    CAL = f"{ROOT}/.venv310/lib/python3.10/site-packages/akshare/file_fold/calendar.json"
    dry = opts.dry_run
    days = trading_days()
    date = opts.date or target_date(days, datetime.now())
    stamp = datetime.now().isoformat(timespec="seconds")
    if not date or date < START or date not in days:
        print(f"{stamp} skip: target={date} (not a trading day on/after {START})", flush=True)
        return EXIT_OK
    idx = [d for d in days if d >= START].index(date)
    group, syms = ("A", GROUP_A) if idx % 2 == 0 else ("B", GROUP_B)
    outdir = os.path.join(HERE, date)
    if not dry:
        os.makedirs(outdir, exist_ok=True)
    manifest = os.path.join(outdir, "manifest.log")
    lockf = open(os.path.join(HERE, ".lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"{stamp} skip: another run holds the lock", flush=True)
        return EXIT_OK
    if os.path.exists(os.path.join(outdir, "summary.json")):
        print(f"{stamp} skip: {date} already finished", flush=True)
        return EXIT_OK
    submitted = set()
    if os.path.exists(manifest):
        for line in open(manifest):
            p = line.split()
            if len(p) >= 4 and p[1] == "submitted":
                submitted.add(p[2])
    todo = [s for s in syms if s not in submitted]

    lock = threading.Lock()

    def log(m):
        with lock:
            line = f"{datetime.now().isoformat(timespec='seconds')} {m}"
            print(line, flush=True)
            with open(manifest, "a") as f:
                f.write(line + "\n")

    if dry:
        print(f"{stamp} dry-run: date={date} group={group} todo={len(todo)} first={todo[:3]}", flush=True)
        return EXIT_OK
    s = requests.Session()
    s.trust_env = False
    try:
        hz = s.get(f"{BASE}/healthz", timeout=10).json()
    except Exception as e:
        log(f"ABORT healthz unreachable at {BASE}: {type(e).__name__}: {e}")
        return EXIT_UNHEALTHY
    if hz.get("status") != "ok":
        log(f"ABORT healthz not ok: {hz}")
        return EXIT_UNHEALTHY
    try:
        account = resolve_account(opts.account, ROOT)
        code = s.post(f"{BASE}/v1/auth/request-code", json={"email": account}, timeout=30).json()["dev_code"]
        d = s.post(f"{BASE}/v1/auth/verify-code", json={"email": account, "code": code}, timeout=30).json()
    except Exception as e:
        log(f"ABORT login failed: {type(e).__name__}: {e}")
        return EXIT_UNHEALTHY
    if d["user"]["id"] != UID:
        log(f"ABORT logged-in account is not the fixed analysis account: {d['user']['id']}")
        return EXIT_UNHEALTHY
    headers = {"Authorization": f"Bearer {d['access_token']}", "Connection": "close"}
    log(f"start date={date} group={group} todo={len(todo)} sha={hz.get('commit_sha')} MAXC={MAXC}")

    gate = threading.Lock()
    last = [0.0]
    results = {}

    def busy():
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = con.execute("select count(*) from reports where status in ('running','pending','queued')").fetchone()[0]
        con.close()
        return n

    def one(sym):
        try:
            with gate:
                while busy() >= MAXC or time.time() - last[0] < STAGGER:
                    time.sleep(10)
                last[0] = time.time()
            r = requests.post(f"{BASE}/v1/analyze", headers=headers,
                              json={"symbol": sym, "trade_date": date, "horizons": ["short", "medium"]},
                              timeout=180, proxies={"http": None, "https": None})
            r.raise_for_status()
            jid = r.json()["job_id"]
            t0 = time.time()
            log(f"submitted {sym} {jid}")
            st = None
            while time.time() - t0 < 7200:
                time.sleep(30)
                con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
                row = con.execute("select status from reports where id=?", (jid,)).fetchone()
                con.close()
                st = row[0] if row else None
                if st in ("completed", "failed"):
                    break
            results[sym] = {"job_id": jid, "status": st, "seconds": int(time.time() - t0)}
            log(f"finished {sym} {jid} {st} {int(time.time() - t0)}s")
        except Exception as e:
            results[sym] = {"error": f"{type(e).__name__}: {e}"}
            log(f"ERROR {sym} {type(e).__name__} {e}")

    with ThreadPoolExecutor(MAXC) as ex:
        for sym in todo:
            ex.submit(one, sym)
            time.sleep(5)
    counts = {}
    for v in results.values():
        k = v.get("status") or ("error" if "error" in v else "unknown")
        counts[k] = counts.get(k, 0) + 1
    json.dump({"date": date, "group": group, "sha": hz.get("commit_sha"), "counts": counts, "results": results,
               "finished_at": datetime.now().isoformat(timespec="seconds")},
              open(os.path.join(outdir, "summary.json"), "w"), ensure_ascii=False, indent=1)
    failed = counts.get("failed", 0) + counts.get("error", 0)
    log(f"all done {counts}")
    return EXIT_BATCH if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
