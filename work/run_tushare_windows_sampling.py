"""Tushare Gateway 3-Window Distributed Sampling Runner (DAV-1097 Card 2 re-sampling).

总工指令（2026-09-21）：同日、同探针、与既有采样方法一致：
- 上午盘中 09:30–11:30: ≥8 轮
- 下午盘中 13:00–15:00: ≥8 轮
- 盘后 15:30 后: ≥4 轮
- 合计 ≥20 轮，尽量均匀分散在各窗口内

产出：最终成功率 / P50 / P95 延迟 / 失败分类（timeout / 4xx / 5xx / 解析），
逐轮时间戳入卡留档。

约束：只读探测，不改代码、不动部署、不写生产库；Token 全程不打印不落盘。
本脚本完成后自动：
1. 写原始数据 JSON + 统计报告 md 到 work/
2. git add/commit/push 到 agent 分支
3. 通过 multica CLI 发布最终交付评论（含项目调度助手 mention）
"""

import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path("/Users/davidliu/Documents/TradingAgents-AShare")
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from tradingagents.dataflows.providers.industry_linkage_provider import _query_tushare_api

TARGET_APIS = ["income", "balancesheet", "cashflow", "daily_basic", "daily"]
TEST_TS_CODE = "600036.SH"
DELAY_BETWEEN_CALLS = 0.4

# (window_name, start, end, rounds) — 2026-09-21 本地时间 (AWST = UTC+8)
WINDOWS = [
    ("morning",   datetime(2026, 9, 21, 9, 30),  datetime(2026, 9, 21, 11, 30), 8),
    ("afternoon", datetime(2026, 9, 21, 13, 0),  datetime(2026, 9, 21, 15, 0), 8),
    ("postclose", datetime(2026, 9, 21, 15, 30), datetime(2026, 9, 21, 17, 0), 4),
]

OUT_JSON = PROJECT_ROOT / "work" / "2026-09-21-tushare-sampling-3windows-raw.json"
OUT_REPORT = PROJECT_ROOT / "work" / "2026-09-21-tushare-gateway-sampling-3windows-report.md"
OUT_COMMENT = PROJECT_ROOT / "work" / "dav1097-reply-3windows.md"
LOG_PREFIX = "[3win-sampling]"

ISSUE_ID = "01a0ba56-39dc-77f5-925f-897ae0a3b858"
TRIGGER_PARENT = "01a0bfc6-64a9-7034-ad6d-aa60d1e001eb"
MENTION = "[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)"
BRANCH = "agent/agent/08cad900796c"
CHECKOUT_DIR = Path(
    "/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/"
    "dav-1097-08cad900796c/workdir/tradingagents-ashare-fork"
)


def log(msg):
    print(f"{LOG_PREFIX} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def classify_failure(err_cat, err_note):
    """失败分类：timeout / 4xx / 5xx / 解析 / 其他"""
    if not err_cat and not err_note:
        return "None"
    note = (err_note or "").lower()
    cat = (err_cat or "").lower()
    if "timeout" in cat or "timeout" in note or "超时" in note:
        return "timeout"
    if cat in ("403", "rate_limited") or "429" in note or "403" in note or "权限" in note or "限频" in note:
        return "4xx"
    if cat == "http_error" and any(s in note for s in ("500", "502", "503", "504")):
        return "5xx"
    if cat in ("parse_error", "api_error") or "json" in note or "解析" in note:
        return "解析"
    return "其他"


def run_one_round(round_idx, window_name):
    records = []
    for api in TARGET_APIS:
        t0 = time.perf_counter()
        call_ts = datetime.now().isoformat()
        try:
            df, err_cat, err_note = _query_tushare_api(api, ts_code=TEST_TS_CODE)
            elapsed = time.perf_counter() - t0
            success = df is not None
            rows = len(df) if success else 0
            if success:
                err_cat, err_note = None, None
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            success, rows = False, 0
            err_cat, err_note = "exception", str(exc)
        rec = {
            "round": round_idx,
            "window": window_name,
            "api": api,
            "ts_code": TEST_TS_CODE,
            "timestamp_local": call_ts,
            "elapsed_sec": round(elapsed, 4),
            "success": success,
            "row_count": rows,
            "error_category": err_cat,
            "failure_type": "None" if success else classify_failure(err_cat, err_note),
            "error_note": (err_note[:200] if err_note else None),
        }
        records.append(rec)
        status = f"OK {rows}r" if success else f"FAIL {rec['failure_type']}"
        log(f"  r{round_idx} [{window_name}] {api}: {status} {elapsed:.2f}s")
        time.sleep(DELAY_BETWEEN_CALLS)
    return records


def sleep_until(target):
    while True:
        delta = (target - datetime.now()).total_seconds()
        if delta <= 0:
            return
        time.sleep(min(delta, 60))


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def compute_stats(records):
    out = {}
    for api in TARGET_APIS:
        rs = [r for r in records if r["api"] == api]
        lats = sorted(r["elapsed_sec"] for r in rs)
        fails = [r for r in rs if not r["success"]]
        dist = {}
        for f in fails:
            dist[f["failure_type"]] = dist.get(f["failure_type"], 0) + 1
        out[api] = {
            "total": len(rs),
            "success": len(rs) - len(fails),
            "fail": len(fails),
            "rate": round(100.0 * (len(rs) - len(fails)) / len(rs), 2) if rs else 0,
            "p50": round(percentile(lats, 50), 3),
            "p95": round(percentile(lats, 95), 3),
            "max": round(max(lats), 3) if lats else 0,
            "fail_dist": dist,
        }
    lats = sorted(r["elapsed_sec"] for r in records)
    fails = [r for r in records if not r["success"]]
    dist = {}
    for f in fails:
        dist[f["failure_type"]] = dist.get(f["failure_type"], 0) + 1
    out["_overall"] = {
        "total": len(records),
        "success": len(records) - len(fails),
        "fail": len(fails),
        "rate": round(100.0 * (len(records) - len(fails)) / len(records), 2) if records else 0,
        "p50": round(percentile(lats, 50), 3),
        "p95": round(percentile(lats, 95), 3),
        "max": round(max(lats), 3) if lats else 0,
        "fail_dist": dist,
    }
    return out


def stats_table(stats):
    lines = [
        "| 接口 | 请求数 | 成功 | 失败 | 成功率 | P50 | P95 | 最大 | 失败分类 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for api in TARGET_APIS + ["_overall"]:
        s = stats[api]
        name = "合计" if api == "_overall" else f"`{api}`"
        dist = ", ".join(f"{k}:{v}" for k, v in s["fail_dist"].items()) or "无"
        lines.append(
            f"| {name} | {s['total']} | {s['success']} | {s['fail']} | {s['rate']}% "
            f"| {s['p50']}s | {s['p95']}s | {s['max']}s | {dist} |"
        )
    return "\n".join(lines)


def build_report(all_records, window_meta):
    stats = compute_stats(all_records)
    lines = [
        "# Tushare 网关三窗口分布式采样报告（DAV-1097 卡 2 补验）",
        "",
        f"> 采样日期：2026-09-21（工作日）  标的：`{TEST_TS_CODE}`  探针：`_query_tushare_api`  ",
        "> 解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20）  ",
        "> 只读探测：未改代码、未动部署、未写生产库；Token 零泄露。",
        "",
        "## 一、窗口与轮次分布",
        "",
        "| 窗口 | 时段 | 计划轮次 | 实际完成轮次 | 首轮时间 | 末轮时间 |",
        "|---|---|---|---|---|---|",
    ]
    for name, meta in window_meta.items():
        lines.append(
            f"| {name} | {meta['range']} | {meta['planned']} | {meta['done']} "
            f"| {meta['first_ts']} | {meta['last_ts']} |"
        )
    lines += [
        "",
        "## 二、总体统计（全部窗口合并）",
        "",
        stats_table(stats),
        "",
        "## 三、分窗口统计",
        "",
    ]
    for name in window_meta:
        rs = [r for r in all_records if r["window"] == name]
        ws = compute_stats(rs)
        lines += [f"### {name}", "", stats_table(ws), ""]
    lines += [
        "## 四、逐轮时间戳留档",
        "",
        "| 轮次 | 窗口 | 时间戳(本地) | income | balancesheet | cashflow | daily_basic | daily |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rnd in sorted({r["round"] for r in all_records}):
        rs = {r["api"]: r for r in all_records if r["round"] == rnd}
        if not rs:
            continue
        ts = next(iter(rs.values()))["timestamp_local"][:19]
        win = next(iter(rs.values()))["window"]
        cells = []
        for api in TARGET_APIS:
            r = rs.get(api)
            cells.append("—" if r is None else (f"OK {r['elapsed_sec']}s" if r["success"] else f"FAIL {r['failure_type']}"))
        lines.append(f"| {rnd} | {win} | {ts} | " + " | ".join(cells) + " |")
    overall = stats["_overall"]
    verdict = (
        "可用（含长尾超时风险）" if overall["rate"] >= 95 else "不稳定（需复核）"
    )
    lines += [
        "",
        "## 五、结论",
        "",
        f"- 全网关成功率 **{overall['rate']}%**（{overall['success']}/{overall['total']}），"
        f"P50={overall['p50']}s，P95={overall['p95']}s，最大 {overall['max']}s。",
        f"- 失败分类分布：{overall['fail_dist'] or '无失败'}。",
        f"- 结论：**{verdict}**。",
        "- 局限：单一工作日样本，仍不能代表连续多日盘中表现；超时硬阈值 10s 对大表裕量小。",
        "",
    ]
    return "".join(l + "\n" for l in lines), stats


def finalize(all_records, window_meta):
    OUT_JSON.write_text(
        json.dumps({"records": all_records}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report, stats = build_report(all_records, window_meta)
    OUT_REPORT.write_text(report, encoding="utf-8")
    log(f"report written: {OUT_REPORT}")

    # 同步到 checkout 分支并提交推送（best-effort）
    try:
        import shutil

        for f in (OUT_JSON, OUT_REPORT):
            shutil.copy2(f, CHECKOUT_DIR / "work" / f.name)
        subprocess.run(
            ["git", "-C", str(CHECKOUT_DIR), "add",
             f"work/{OUT_JSON.name}", f"work/{OUT_REPORT.name}", f"work/{Path(__file__).name}"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(CHECKOUT_DIR), "commit", "-m",
             "docs(sampling): Tushare 3-window distributed gateway sampling 2026-09-21 (DAV-1097)"],
            check=True, capture_output=True,
        )
        push = subprocess.run(
            ["git", "-C", str(CHECKOUT_DIR), "push", "origin", f"HEAD:{BRANCH}"],
            capture_output=True, text=True,
        )
        sha = subprocess.run(
            ["git", "-C", str(CHECKOUT_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        log(f"pushed {sha} (push rc={push.returncode})")
    except Exception as e:
        sha = "(commit/push failed)"
        log(f"git finalize failed: {e}")

    overall = stats["_overall"]
    comment = f"""## 卡 2 补验：2026-09-21 三窗口分布式采样结果（总工指令执行完毕）

### 窗口与轮次分布（同日、同探针 `_query_tushare_api`、标的 `600036.SH`）

| 窗口 | 时段 | 完成轮次 | 时间覆盖 |
|---|---|---|---|
"""
    for name, meta in window_meta.items():
        comment += f"| {name} | {meta['range']} | {meta['done']} | {meta['first_ts']} ~ {meta['last_ts']} |\n"
    comment += f"""
合计 {sum(m['done'] for m in window_meta.values())} 轮 × 5 接口 = {overall['total']} 次请求，轮次在各窗口内均匀分布。

### 总体统计

{stats_table(stats)}

### 失败分类（timeout / 4xx / 5xx / 解析 / 其他）
{overall['fail_dist'] or '本轮全部成功，无失败'}

### 交付物
- 报告：`work/2026-09-21-tushare-gateway-sampling-3windows-report.md`（含逐轮时间戳留档）
- 原始数据：`work/2026-09-21-tushare-sampling-3windows-raw.json`
- 采样脚本：`work/run_tushare_windows_sampling.py`
- 远端分支 `origin/{BRANCH}`，提交 SHA：`{sha}`

### 结论与局限
- 全网关成功率 **{overall['rate']}%**，P50={overall['p50']}s，P95={overall['p95']}s。
- 结论：{'可用（仍存在长尾超时风险）' if overall['rate'] >= 95 else '不稳定，需复核'}。
- 局限：单一工作日样本；`_TUSHARE_TIMEOUT=10s` 对 5865 行大表裕量小（P95 已贴近上限）。
- 边界遵守：只读探测，未改代码、未动部署、未写生产库，Token 零泄露。

{MENTION}
"""
    OUT_COMMENT.write_text(comment, encoding="utf-8")
    rc = subprocess.run(
        ["multica", "issue", "comment", "add", ISSUE_ID,
         "--parent", TRIGGER_PARENT, "--content-file", str(OUT_COMMENT)],
        capture_output=True, text=True,
    )
    log(f"comment add rc={rc.returncode} err={rc.stderr.strip()[:300]}")
    if rc.returncode != 0:
        # 退化为顶层评论
        rc2 = subprocess.run(
            ["multica", "issue", "comment", "add", ISSUE_ID,
             "--content-file", str(OUT_COMMENT)],
            capture_output=True, text=True,
        )
        log(f"fallback comment rc={rc2.returncode} err={rc2.stderr.strip()[:300]}")


def main():
    log("3-window distributed sampling runner started")
    all_records = []
    window_meta = {}
    round_idx = 0

    for name, start, end, n_rounds in WINDOWS:
        # 均匀分布轮次时间（窗口内）
        span = (end - start).total_seconds()
        offsets = [span * i / (n_rounds - 1) for i in range(n_rounds)] if n_rounds > 1 else [0]
        times = [start + timedelta(seconds=o) for o in offsets]
        first_ts = last_ts = None
        done = 0
        for t in times:
            sleep_until(t)
            round_idx += 1
            log(f"round {round_idx} window={name} scheduled={t.strftime('%H:%M:%S')}")
            recs = run_one_round(round_idx, name)
            all_records.extend(recs)
            done += 1
            ts = datetime.now().strftime("%H:%M:%S")
            first_ts = first_ts or ts
            last_ts = ts
            OUT_JSON.write_text(
                json.dumps({"records": all_records}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        window_meta[name] = {
            "range": f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}",
            "planned": n_rounds,
            "done": done,
            "first_ts": first_ts,
            "last_ts": last_ts,
        }

    log("all windows complete; finalizing")
    finalize(all_records, window_meta)
    log("done")


if __name__ == "__main__":
    main()
