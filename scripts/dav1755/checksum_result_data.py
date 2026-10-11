#!/usr/bin/env python3
"""DAV-1755 派工② → DAV-1616 用: 指定报告集合的流式 sha256 汇总核对。

对一组指定的报告 id 集合，流式计算:
  count(*)                       — 命中行数
  sha256-agg                     — 每行 result_data 的 sha256 聚合汇总

聚合方式（确定性、可复现、对缺失行敏感）:
  1. 按报告 id 排序后逐行取 result_data；
  2. 对每行 result_data 原始字节（sqlite3 存储值原样，BLOB/TEXT 按原始 bytes）算 sha256: h_i；
  3. 汇总 = sha256( concat( h_i 的二进制摘要, 按排序后顺序 ) )；result_data 为 NULL 的行
     用 sha256("") 占位但计入 count，并单独报告 null_count；
  4. 另有缺失 id（请求了但查不到）单独列出, 不进汇总。

内存纪律: 每次只持有一行 result_data（sqlite3 默认已整行载入内存；避免 fetchall），
4 GiB footprint 宽裕。10-12 单报告最大 ~5 MB 实测无压力。

用法:
    /usr/bin/time -l python3 checksum_result_data.py <db> --ids-file <ids.txt>
    /usr/bin/time -l python3 checksum_result_data.py <db> --ids-file -  < ids.txt
    ids.txt: 每行一个报告 id（UUID 32-hex 或带横线形式, 自动规范化），# 开头注释行忽略。

输出 JSON 到 stdout: {db, ids_requested, rows_hit, missing_ids, null_result_data,
                     sha256_aggregate, elapsed_seconds}
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time

HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def canon(s: str) -> str:
    """Normalize a report id to its canonical form (no dashes, lower hex)."""
    s = s.strip()
    if not s or s.startswith("#"):
        return ""
    if UUID.match(s):
        return s.replace("-", "").lower()
    if HEX32.match(s):
        return s.lower()
    raise ValueError(f"not a report id: {s!r}")


def load_ids(path: str) -> list[str]:
    fh = sys.stdin if path == "-" else open(path, "r", encoding="utf-8")
    ids, seen = [], set()
    with fh:
        for line in fh:
            rid = canon(line)
            if rid and rid not in seen:
                seen.add(rid)
                ids.append(rid)
    return sorted(ids)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="SQLite db path (read-only)")
    ap.add_argument("--ids-file", required=True, help="report id list file, '-' for stdin")
    args = ap.parse_args()

    ids = load_ids(args.ids_file)
    if not ids:
        print(json.dumps({"error": "empty id set"}, ensure_ascii=False))
        return 2

    t0 = time.monotonic()
    uri = f"file:{args.db}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    cur = conn.cursor()

    # 用 rowid 顺序全表扫（O(n) 一次流式扫描），dict 查 id 命中集合。
    # 注意：聚合顺序必须是规范 id 排序序，而不是扫描序——VACUUM INTO / 重写会改 rowid，
    # 若按扫描序聚合，同一内容前后两库的汇总值会不同。
    wanted = set(ids)  # canonical (no-dash lower) form

    rows_hit = 0
    null_result = 0
    digests: dict[str, bytes] = {}  # canonical rid -> sha256(result_data bytes)

    cur.execute("PRAGMA query_only=ON")

    q = "SELECT id, result_data FROM reports ORDER BY rowid"
    for rid_raw, rd in cur.execute(q):
        try:
            rid = canon(rid_raw)
        except ValueError:
            continue  # 防御：表里若出现非规范 id，跳过
        if rid not in wanted or rid in digests:
            continue
        rows_hit += 1
        if rd is None:
            null_result += 1
            digests[rid] = hashlib.sha256(b"").digest()
            continue
        if isinstance(rd, str):
            payload = rd.encode("utf-8")
        elif isinstance(rd, (bytes, bytearray)):
            payload = bytes(rd)
        else:  # 防御：sqlite 不应该返回别的类型
            raise TypeError(f"unexpected result_data type {type(rd)} for id={rid_raw}")
        digests[rid] = hashlib.sha256(payload).digest()
        rd = None  # 释放结果字节引用

    # 聚合序 = 规范 id 字典序（rowid 无关，跨库可比）。
    agg = hashlib.sha256()
    for rid in sorted(digests):
        agg.update(digests[rid])

    missing = sorted(wanted - set(digests))
    elapsed = time.monotonic() - t0
    conn.close()

    out = {
        "db": args.db,
        "ids_requested": len(ids),
        "rows_hit": rows_hit,
        "missing_ids": missing,
        "missing_count": len(missing),
        "null_result_data": null_result,
        "sha256_aggregate": agg.hexdigest(),
        "elapsed_seconds": round(elapsed, 3),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
