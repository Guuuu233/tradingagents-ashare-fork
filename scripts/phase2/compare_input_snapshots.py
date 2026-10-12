#!/usr/bin/env python3
"""DAV-1882 — 两臂输入快照比对（总控 10-12 晚 v0.3 §3 口径重定义）。

口径要点（与旧版**不兼容**，按新令执行）：

1. **默认不做任何字段剔除**。`input_snapshot.hash` 是整池 canonical sha256
   （DAV-1430 `_compute_input_snapshot`），对任何逐次变化字段都敏感。默认判定
   就是拿原始 hash 比：不等即"不一致"，不靠猜哪些字段"每次必然不同"。
   （旧版用 `VOLATILE_KEY_HINTS` 子串推断 volatile，已按新令删除。）
2. **规范化只认显式逐路径白名单文件**：精确 JSON 路径，**禁子串、禁通配符**。
   默认为空 = 不剔除任何字段。白名单内容须由总控在 5 对试跑后确认才填。
   **判定口径本身的字段（``hash`` / ``algo`` / ``keys`` / ``bytes``）不得进白名单**：
   命中即报错退出（``ValueError``），否则一条 ``paths: ["hash"]`` 就能把两份
   不同的输入判成「一致」，等于自毁口径。
3. **所用白名单的 sha256 必须落进运行记录**，否则事后无法判断某次结论用的是
   哪份白名单。
4. **原始结果与规范化结果分别记录、分别报告**，不合并成一个结论。

用法::

    python scripts/phase2/compare_input_snapshots.py O.json N.json
    python scripts/phase2/compare_input_snapshots.py O.json N.json \
        --whitelist whitelist.json          # 显式逐路径白名单
    python scripts/phase2/compare_input_snapshots.py O.json N.json --out r.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# 用于**判定比对口径**的字段：白名单不得剔除它们，否则结论不可复核。
# 命中即报错退出，不静默忽略。
PROTECTED_FIELDS = frozenset({"hash", "algo", "keys", "bytes"})

_SEGMENT_RE = re.compile(r"[^.\[\]]+")


def _segments(path: str) -> List[str]:
    """把 ``a.b[0].hash`` 拆成 ``["a", "b", "0", "hash"]``。"""
    return [s.strip().lower() for s in _SEGMENT_RE.findall(path)]


def protected_paths(paths: Sequence[str]) -> List[str]:
    """返回 ``paths`` 里命中的受保护路径（任一层段名在 PROTECTED_FIELDS 内）。"""
    return [p for p in paths if PROTECTED_FIELDS.intersection(_segments(p))]


def load_whitelist(path: Optional[str]) -> Tuple[List[str], Optional[str]]:
    """读显式逐路径白名单。返回 ``(paths, sha256)``。

    * ``path`` 为空 → 白名单为空、sha256 为 None（默认不剔除任何字段）。
    * 白名单内容必须是**精确 JSON 路径**；出现 ``*`` / ``%`` 等通配符直接报错，
      避免"看起来是白名单、实际在做子串匹配"的静默放宽。
    * 白名单命中受保护字段（``hash`` / ``algo`` / ``keys`` / ``bytes``）直接报错
      ——那些字段就是比对口径本身，剔除它们等于把判定变成永真。
    """
    if not path:
        return [], None
    raw = Path(path).read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if isinstance(payload, dict):
        entries = payload.get("paths") or []
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError(f"{path}: 白名单必须是 JSON 数组或含 paths 的对象")
    paths: List[str] = []
    for item in entries:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}: 白名单条目必须是非空字符串，收到 {item!r}")
        entry = item.strip()
        if any(ch in entry for ch in "*?%[]"):
            raise ValueError(
                f"{path}: 白名单只接受精确 JSON 路径，禁止通配符/下标通配：{entry!r}"
            )
        paths.append(entry)
    bad = protected_paths(paths)
    if bad:
        raise ValueError(
            f"{path}: 白名单不得包含用于判定比对口径的字段"
            f"{sorted(PROTECTED_FIELDS)}，命中：{bad}"
        )
    return paths, hashlib.sha256(raw).hexdigest()


def flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """嵌套结构拍平成 ``a.b`` / ``a[0]`` → value，用于逐字段比对。"""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        # 必须收集递归结果：丢弃它会让嵌套字段凭空消失、diff 恒为空。
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def diff_paths(a: Any, b: Any) -> List[Dict[str, Any]]:
    """逐字段差异。**不做任何剔除或分类**——全量列出。"""
    fa, fb = flatten(a or {}), flatten(b or {})
    rows = []
    for key in sorted(set(fa) | set(fb)):
        va, vb = fa.get(key, "<absent>"), fb.get(key, "<absent>")
        if va == vb:
            continue
        rows.append({
            "path": key,
            "A": va if not isinstance(va, (dict, list)) else json.dumps(va, ensure_ascii=False),
            "B": vb if not isinstance(vb, (dict, list)) else json.dumps(vb, ensure_ascii=False),
        })
    return rows


def compare(
    a: Any,
    b: Any,
    *,
    whitelist: Sequence[str] = (),
    whitelist_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """原始与规范化两组结果，分别给出。

    直接调用本函数（而不是走 ``load_whitelist``）时，白名单里的受保护字段不会
    报错，但**同样不生效**：它们从剔除集里剔除，并列进 ``ignored_protected_paths``，
    调用方一眼能看出自己写的路径没被采纳。
    """
    rows = diff_paths(a, b)
    requested = list(whitelist or ())
    ignored = sorted(set(protected_paths(requested)))
    wl = set(requested) - set(ignored)
    normalized_rows = [r for r in rows if r["path"] not in wl]
    return {
        "raw": {
            "A_hash": (a or {}).get("hash"),
            "B_hash": (b or {}).get("hash"),
            "hash_equal": (a or {}).get("hash") == (b or {}).get("hash"),
            "consistent": not rows,
            "diff_count": len(rows),
            "diffs": rows,
        },
        "normalized": {
            "whitelist_paths": sorted(wl),
            "whitelist_sha256": whitelist_sha256,
            "ignored_protected_paths": ignored,
            "consistent": not normalized_rows,
            "diff_count": len(normalized_rows),
            "excluded_count": len(rows) - len(normalized_rows),
            "diffs": normalized_rows,
        },
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="两臂输入快照比对（原始/规范化分别报告）")
    ap.add_argument("A", help="臂 A 的 result_data.input_snapshot（或整个 result）JSON")
    ap.add_argument("B")
    ap.add_argument("--whitelist", help="显式逐路径白名单 JSON（精确路径，默认空=不剔除）")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    def load(p: str) -> Any:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        # 允许直接喂整个 result / result_data / 裸快照
        if isinstance(data, dict) and "input_snapshot" in data:
            return data["input_snapshot"]
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            inner = data["result"]
            return inner.get("input_snapshot", inner)
        return data

    try:
        wl_paths, wl_sha = load_whitelist(args.whitelist)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"白名单不可用：{exc}") from exc
    report = compare(load(args.A), load(args.B),
                     whitelist=wl_paths, whitelist_sha256=wl_sha)
    report["inputs"] = {"A": str(args.A), "B": str(args.B),
                        "whitelist_file": args.whitelist or None}

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())