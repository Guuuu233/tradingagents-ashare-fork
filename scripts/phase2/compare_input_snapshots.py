#!/usr/bin/env python3
"""DAV-1882 — 两臂输入快照逐字段比对（总控 10-12 令：查清是否含必然变化字段）。

`input_snapshot.hash` 是「把整个输入池做 canonical JSON 再 sha256」（DAV-1430
`_compute_input_snapshot`）。它对任何逐次变化的字段都敏感——时间戳、耗时、
请求 ID、报告 ID 这类。因此两臂 hash 不同**本身不说明问题**：要么是处理臂
真的改了提示词，要么只是采集侧的时间戳抖动。

本脚本对两份快照做**逐字段 diff**，把差异分成三类：

* `prompt` —— 提示词/输入文本类差异，这才是实验自变量的证据；
* `volatile` —— 时间戳、耗时、报告/请求 ID 等每次运行必然不同的字段
  （登记在 `VOLATILE_KEY_HINTS`），差异可由「同臂重跑两次」对照排除；
* `other` —— 其余未分类差异，需要人判断。

用法::

    python scripts/phase2/compare_input_snapshots.py O.json N.json
    python scripts/phase2/compare_input_snapshots.py O.json N.json \\
        --rerun-O O2.json          # 提供同臂重跑对照，佐证 volatile
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# 每次运行必然不同（或高概率不同）的字段名片段
VOLATILE_KEY_HINTS: Tuple[str, ...] = (
    "timestamp", "time", "_at", "date_retrieved", "retrieved", "fetched",
    "elapsed", "duration", "latency", "report_id", "job_id", "run_id",
    "request_id", "created", "updated", "generated", "now", "uuid", "id",
    "cache", "ttl", "expires", "latency_ms", "epoch",
)


def _is_volatile(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in VOLATILE_KEY_HINTS)


def flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """把嵌套结构拍平成 ``a.b.c`` -> value，便于逐字段比对。"""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        # 必须收集递归结果：丢弃它会让嵌套字段凭空消失、diff 恒为空。
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        out[prefix] = f"<list len={len(obj)}>"
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def diff_snapshots(a: Any, b: Any) -> List[Dict[str, Any]]:
    fa, fb = flatten(a or {}), flatten(b or {})
    rows = []
    for key in sorted(set(fa) | set(fb)):
        va, vb = fa.get(key, "<absent>"), fb.get(key, "<absent>")
        if va == vb:
            continue
        rows.append({
            "field": key,
            "A": va if not isinstance(va, (dict, list)) else json.dumps(va, ensure_ascii=False),
            "B": vb if not isinstance(vb, (dict, list)) else json.dumps(vb, ensure_ascii=False),
            "kind": "volatile" if _is_volatile(key) else "other",
        })
    return rows


def classify(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"volatile": [], "other": []}
    for r in rows:
        out[r["kind"]].append(r)
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="两臂输入快照逐字段比对")
    ap.add_argument("A", help="臂 A 的 result_data.input_snapshot（或整个 result）JSON")
    ap.add_argument("B")
    ap.add_argument("--rerun-A", help="同臂重跑的快照，用于佐证 volatile 字段")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    def load(p: str) -> Any:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        # 允许直接喂整个 result 或 result_data
        return data.get("input_snapshot", data)

    a, b = load(args.A), load(args.B)
    rows = diff_snapshots(a, b)
    buckets = classify(rows)

    report = {
        "A_hash": (a or {}).get("hash"),
        "B_hash": (b or {}).get("hash"),
        "identical": not rows,
        "volatile_diff_count": len(buckets["volatile"]),
        "other_diff_count": len(buckets["other"]),
        "volatile_fields": [r["field"] for r in buckets["volatile"]],
        "other_fields": [r["field"] for r in buckets["other"]],
        "diffs": rows,
    }
    if args.rerun_A:
        rr = diff_snapshots(a, load(args.rerun_A))
        rr_buckets = classify(rr)
        report["same_arm_rerun"] = {
            "identical": not rr,
            "volatile_diff_count": len(rr_buckets["volatile"]),
            "volatile_fields": [r["field"] for r in rr_buckets["volatile"]],
        }

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
