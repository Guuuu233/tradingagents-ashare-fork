#!/usr/bin/env python3

# 【压缩存储前适用】(DAV-1773 B-6d)：本脚本直读 reports.result_data 明文列，
# 仅适用于未启用压缩存储（REPORT_STORAGE_MODE=compressed）的数据库。
# 压缩库请改用 tradingagents.storage.compressed_json.decode_result_data，
# 或在 SQL 中取 COALESCE(result_data_zst, result_data) 后交给它解码；
# 参考实现：scripts/backfill_tplus5_shadow.py 的 _rd_text/_rd_col 模式。
"""DAV-1705 管线试跑，非成绩 — 分块流式账本封存（M-05）.

背景: 共享快照中 1600 个 completed 报告的 result_data 合计约 5.46GB
（单行最大约 13MB）。主干 `daily_snapshot_ledger.py cmd_run` 一次
`fetchall()` 把全部 blob 常驻内存，实测 peak footprint 越过 6 GiB 上限。
白名单不允许改生产脚本，故本驱动复用生产模块的封存语义
（`build_record` / hash 链 / HEAD 锚 / state 文件，一一对应），仅把
“全量 fetchall”改为“先取 id 排序、再逐行取 blob 即封即释”，把常驻从
5.46GB 降到单行级。

与生产的语义等价点（逐项对照）:
  - 行顺序: `ORDER BY created_at`，新行判定 = 不在已封存 id 集合中；
  - `sealed_at` 为整轮统一时钟（与生产 `cmd_run` 同口径）；
  - 链: `chain_hash = sha256(prev_hash + record[record_sha256])`，创世
    `GENESIS`，尾部不可读时 fail-close（exit 4）；
  - 日历不可用 fail-close（exit 3）；有 blocking 状态且无 --force 时
    延期退出（exit 2）；
  - 落盘后更新 state + HEAD 锚（bootstrapped=false）。
差异仅两处（均为降内存手段，不影响账本语义）:
  1. blob 逐行获取、每行处理完即 del + 定期 gc；
  2. 连接执行 `PRAGMA mmap_size=0`（快照本就 mmap=0，显式锁定防回退）。

用法:
    python work/dav1705_pipeline/build_ledger_chunked.py \
        --db /private/tmp/ta-snapshot/current.db \
        --ledger-dir work/dav1705_pipeline/ledger --force
"""

from __future__ import annotations

import argparse
import gc
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "phase2"))
import daily_snapshot_ledger as dsl  # noqa: E402  (scripts/phase2)
from tradingagents.dataflows.trade_calendar import cn_today_str  # noqa: E402

TAG = "管线试跑，非成绩"
BATCH_GC_EVERY = 50

COLS = ("id, user_id, symbol, industry, trade_date, status,"
        " analysis_status, decision, direction, probability,"
        " trade_action, risk_status, final_trade_decision,"
        " created_at, updated_at, result_data")


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA mmap_size=0")
    con.execute("PRAGMA temp_store=MEMORY")
    return con


def _default_snapshot_date() -> str:
    """默认 snapshot_date：与生产 `cmd_run` 同口径的本地日.

    直接复用生产侧同一函数 `cn_today_str()`（Asia/Shanghai 口径），
    在任何机器时区下都与生产 `date.today().isoformat()` 在 CN 机器上的
    取值一致（F-01）。"""
    return cn_today_str()


def cmd_build(args: argparse.Namespace) -> int:
    ledger_dir = Path(args.ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / dsl.LEDGER_FILE
    snapshot_date = args.date or _default_snapshot_date()
    sealed_at = datetime.now(timezone.utc)

    con = _connect_ro(Path(args.db))
    try:
        n_block = con.execute(
            f"SELECT COUNT(*) FROM reports WHERE status IN "
            f"({','.join('?' * len(dsl.BLOCKING_STATUSES))})",
            dsl.BLOCKING_STATUSES,
        ).fetchone()[0]
        if n_block and not args.force:
            print(f"DEFERRED: {n_block} report(s) still in "
                  f"{dsl.BLOCKING_STATUSES}", file=sys.stderr)
            return 2

        state = dsl._load_state(ledger_dir)
        sealed_ids = dsl._load_sealed_ids(ledger_dir, state)

        # 只取 id + created_at 排序（KB 级），blob 逐行取。
        id_rows = con.execute(
            "SELECT id, created_at FROM reports WHERE status='completed'"
            " ORDER BY created_at").fetchall()
        new_ids = [r["id"] for r in id_rows if r["id"] not in sealed_ids]
        del id_rows
        if not new_ids:
            print("OK: no new completed reports (all already sealed)")
            return 0

        try:
            trade_dates = dsl._load_trade_dates()
        except Exception as exc:
            print(f"FAILED: trade calendar unavailable: {exc}",
                  file=sys.stderr)
            return 3

        try:
            tail_obj = dsl._read_last_line(ledger_path)
        except ValueError as exc:
            print(f"FAILED: ledger tail unreadable: {exc}; refusing to "
                  f"append (would detach a new genesis segment).",
                  file=sys.stderr)
            return 4
        prev_hash = tail_obj["chain_hash"] if tail_obj else dsl.GENESIS_PREV

        appended = 0
        with open(ledger_path, "a", encoding="utf-8") as out:
            for i, rid in enumerate(new_ids):
                row = con.execute(
                    f"SELECT {COLS} FROM reports WHERE id=?", (rid,)).fetchone()
                rec = dsl.build_record(row, row["result_data"], sealed_at,
                                       snapshot_date, trade_dates)
                raw_blob = row["result_data"]
                del row
                chain_hash = dsl._sha256_bytes(
                    (prev_hash + rec[dsl.CHAIN_FIELD]).encode("utf-8"))
                out.write(json.dumps(
                    {"record": rec, "prev_hash": prev_hash,
                     "chain_hash": chain_hash},
                    ensure_ascii=False, separators=(",", ":")) + "\n")
                prev_hash = chain_hash
                sealed_ids.add(rec["report_id"])
                appended += 1
                del rec, raw_blob
                if appended % BATCH_GC_EVERY == 0:
                    gc.collect()

        state["sealed_report_ids"] = sorted(sealed_ids)
        state["last_run_at"] = sealed_at.isoformat()
        state["last_appended"] = appended
        dsl._save_state(ledger_dir, state)
        dsl._write_head_anchor(ledger_dir, prev_hash,
                               dsl._count_ledger_lines(ledger_path),
                               bootstrapped=False, reason="chunked append")
        print(f"OK: appended {appended} record(s) [{TAG}]; "
              f"ledger={ledger_path}")
        return 0
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--ledger-dir", required=True)
    ap.add_argument("--date", default=None)
    ap.add_argument("--force", action="store_true")
    return cmd_build(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
