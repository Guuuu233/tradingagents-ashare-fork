#!/usr/bin/env python3
"""DAV-1755 派工①: 实测 sqlite3 .backup() 同盘复制耗时（源=共享快照只读副本）。

用法:
    /usr/bin/time -l python3 time_backup.py <src_db> <dst_db>

只读打开 src（mode=ro&immutable=1），用 sqlite3.Connection.backup() 顺序写入
dst（同盘新建文件）。结束后打 quick_check + dst 文件大小。dst 存在则先删。
"""
import os
import sqlite3
import sys
import time

def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    if os.path.exists(dst):
        os.remove(dst)
    src_uri = f"file:{src}?mode=ro&immutable=1"
    t0 = time.monotonic()
    conn_src = sqlite3.connect(src_uri, uri=True)
    conn_dst = sqlite3.connect(dst)
    conn_src.backup(conn_dst)
    conn_dst.close()
    conn_src.close()
    elapsed = time.monotonic() - t0

    chk = sqlite3.connect(f"file:{dst}?mode=ro&immutable=1", uri=True)
    qc = chk.execute("PRAGMA quick_check").fetchone()[0]
    chk.close()

    src_sz = os.path.getsize(src)
    dst_sz = os.path.getsize(dst)
    print(f"src={src}")
    print(f"src_bytes={src_sz}")
    print(f"dst={dst}")
    print(f"dst_bytes={dst_sz}")
    print(f"backup_elapsed_seconds={elapsed:.3f}")
    print(f"throughput_MBps={src_sz/1048576/elapsed:.1f}")
    print(f"quick_check={qc}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
