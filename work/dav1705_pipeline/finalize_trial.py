#!/usr/bin/env python3
"""DAV-1705 管线试跑，非成绩 — 产物标注固化（M-01）.

m2_sampler.py / m2_periodic_report.py 为主干生产脚本，白名单不允许修改，
故由本脚本对试跑产物统一注入「非成绩」标注。注入内容为固定字符串，
同一输入两次运行逐字节相同。

动作:
  1. picks JSON: 顶层注入 "pipeline_trial_non_result": "管线试跑，非成绩"
     （canonical 重排，sort_keys + 紧凑分隔符，与 sampler 输出格式一致）。
  2. m2 报告 md: 文件首行前置固定抬头
     `> 管线试跑，非成绩 — 仅验证 m2 报告管线，不作任何结论`
     及空行。

用法:
    python work/dav1705_pipeline/finalize_trial.py <picks.json> <report.md>...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TAG = "管线试跑，非成绩"
PICKS_FIELD = "pipeline_trial_non_result"
REPORT_HEADER = "> 管线试跑，非成绩 — 仅验证 m2 报告管线，不作任何结论\n\n"


def finalize_picks(path: Path) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    obj[PICKS_FIELD] = TAG
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False) + "\n", encoding="utf-8")


def finalize_report(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if text.startswith(REPORT_HEADER):
        text = text[len(REPORT_HEADER):]
    path.write_text(REPORT_HEADER + text, encoding="utf-8")


def main(argv: list[str]) -> int:
    if not argv:
        print(f"用法: finalize_trial.py <picks.json> <report.md>...",
              file=sys.stderr)
        return 2
    for arg in argv:
        p = Path(arg)
        if p.suffix not in (".json", ".md"):
            print(f"SKIP: {p} (unknown suffix)", file=sys.stderr)
            return 2
        if not p.is_file():
            print(f"FAILED: file not found: {p}", file=sys.stderr)
            return 1
        try:
            if p.suffix == ".json":
                finalize_picks(p)
            else:
                finalize_report(p)
        except (OSError, ValueError) as exc:
            print(f"FAILED: {p}: {exc}", file=sys.stderr)
            return 1
        print(f"OK: labeled {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
