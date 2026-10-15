"""DAV-1308 offline gate recompute — snapshot 近 5 个交易日报告逐份重算。

只读打开生产快照（``file:...?mode=ro&immutable=1``），把每份 result_data 的
report 字段装进最小 state，跑 audit_price_ref_registry + enforce_price_basis_gate，
逐份对比新旧 gate status。

用法：
    env -u PYTHONPATH <venv_py> scripts/offline_recompute_gate_5d.py

输出：
    每份报告一行：report_id | symbol | trade_date | horizon | stored_status | new_status | verdict
    末尾打印汇总（OK / DIFF）。DIFF 逐条列出新旧 violations。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

# 仓库内 import 必须在 sys.path 上
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.agents.utils.price_ref_registry import (  # noqa: E402
    REPORT_FIELDS,
    audit_price_ref_registry,
)
from tradingagents.agents.utils.price_basis_gate import (  # noqa: E402
    enforce_price_basis_gate,
)

SNAPSHOT_DB = "/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db"
TRADE_DATES = ("2026-10-14", "2026-10-13", "2026-10-12", "2026-10-09", "2026-10-08")


def _report_state(symbol: str, trade_date: str, slice_data: dict) -> dict:
    """重建 enforce_price_basis_gate 所需最小 state。

    只灌 report 字段与 trade_date —— pool 不在 result_data 里，无法离线复现
    C5 桥接（影响见 README / 报告说明）。
    """
    state: dict = {"trade_date": trade_date, "company_of_interest": symbol}
    for name in REPORT_FIELDS:
        v = slice_data.get(name)
        if isinstance(v, str):
            state[name] = v
    return state


def _collect_slices(report: dict) -> list[tuple[str, dict]]:
    """取出该报告的 (horizon, slice) 列表：short_term / medium_term / 顶层（单 horizon）。"""
    out: list[tuple[str, dict]] = []
    for h in ("short_term", "medium_term"):
        sub = report.get(h)
        if isinstance(sub, dict) and sub:
            out.append((h, sub))
    if not out:
        # 单 horizon：顶层即 slice
        out.append(("top", report))
    return out


def main() -> int:
    conn = sqlite3.connect(
        f"file:{quote(SNAPSHOT_DB)}?mode=ro&immutable=1", uri=True
    )
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in TRADE_DATES)
    cur.execute(
        f"SELECT id, symbol, trade_date, result_data FROM reports "
        f"WHERE trade_date IN ({placeholders}) AND result_data IS NOT NULL "
        f"ORDER BY trade_date DESC, symbol",
        TRADE_DATES,
    )
    rows = cur.fetchall()

    total = 0
    ok = 0
    diffs: list[dict] = []
    for rid, symbol, tdate, rdata in rows:
        try:
            report = json.loads(rdata) if isinstance(rdata, str) else rdata
        except Exception:
            continue
        if not isinstance(report, dict):
            continue

        for horizon, slice_data in _collect_slices(report):
            stored_gate = slice_data.get("price_basis_gate") or {}
            stored_status = stored_gate.get("status") or "absent"

            state = _report_state(symbol, tdate, slice_data)
            audit_price_ref_registry(state)
            new_gate = enforce_price_basis_gate(state)
            new_status = new_gate.get("status")

            total += 1
            if new_status == stored_status:
                ok += 1
                verdict = "OK"
            else:
                verdict = "DIFF"
                diffs.append(
                    {
                        "report_id": rid,
                        "symbol": symbol,
                        "trade_date": tdate,
                        "horizon": horizon,
                        "stored_status": stored_status,
                        "new_status": new_status,
                        "stored_violations": stored_gate.get("violations"),
                        "new_violations": new_gate.get("violations"),
                    }
                )
            print(
                f"{rid[:12]} | {symbol:10s} | {tdate} | {horizon:12s} | "
                f"stored={stored_status:8s} | new={new_status:8s} | {verdict}"
            )

    print("=" * 78)
    print(f"TOTAL: {total}  OK: {ok}  DIFF: {len(diffs)}")
    for d in diffs:
        print("-" * 78)
        print(
            f"DIFF {d['report_id']} {d['symbol']} {d['trade_date']} {d['horizon']}: "
            f"stored={d['stored_status']} new={d['new_status']}"
        )
        print("  stored_violations:", json.dumps(d["stored_violations"], ensure_ascii=False)[:400])
        print("  new_violations   :", json.dumps(d["new_violations"], ensure_ascii=False)[:400])
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
