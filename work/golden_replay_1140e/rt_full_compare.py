#!/usr/bin/env python3
"""DAV-1179 RT-FULL 门禁判定：parent vs candidate 新增失败对照。

输入两侧 junit xml + 运行元信息，输出机读门禁报告：
- 主门禁字段 new_failures_relative_to_parent（parent 已过、candidate 失败的 nodeid 集合）；
- 绝对 passed/failed 仅作环境旁证（受代理/.env/网络 guard/生产计数漂移影响，不可移植）；
- 记录解释器版本、HEAD、worktree 状态、隔离 DB、既有 deselect。

用法:
  rt_full_compare.py <out_dir> --parent-label parent --candidate-label candidate
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

DESELECT = ("tests/test_fund_flow_scale_consumption.py::"
            "TestFundFlowScalePersistenceAndReadback::"
            "test_single_horizon_report_persists_and_reads_all_scale_fields")


def parse_junit(path: Path) -> dict:
    tree = ET.parse(path)
    cases = {}
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for ts in tree.getroot().iter("testsuite"):
        for k in totals:
            totals[k] += int(ts.attrib.get(k, 0))
        for tc in ts.iter("testcase"):
            nodeid = f"{tc.attrib.get('classname')}::{tc.attrib.get('name')}"
            status = "passed"
            for child in tc:
                if child.tag == "failure":
                    status = "failed"
                elif child.tag == "error":
                    status = "error"
                elif child.tag == "skipped":
                    status = "skipped"
            # 同一 nodeid 多次出现（rerun）取最差结果
            order = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
            if order[status] > order.get(cases.get(nodeid, "passed"), 0):
                cases[nodeid] = status
    return {"nodeids": cases, "totals": totals}


def load_meta(out_dir: Path, label: str) -> dict:
    def read(name):
        p = out_dir / f"{label}.{name}"
        return p.read_text(encoding="utf-8").strip() if p.exists() else None
    return {
        "python_version": read("python_version.txt"),
        "head_sha": read("head_sha.txt"),
        "worktree_dirty": bool(read("worktree_status.txt")),
        "database_url": f"sqlite:///{out_dir}/{label}_rtfull.db",
        "proxy": "unset (http_proxy/https_proxy/all_proxy cleared)",
        "deselect": DESELECT,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--parent-label", default="parent")
    ap.add_argument("--candidate-label", default="candidate")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 <out_dir>/rt_full_gate_report.json）")
    args = ap.parse_args()
    out_dir = args.out_dir

    parent = parse_junit(out_dir / f"{args.parent_label}.junit.xml")
    cand = parse_junit(out_dir / f"{args.candidate_label}.junit.xml")

    p_bad = {n for n, s in parent["nodeids"].items() if s in ("failed", "error")}
    c_bad = {n for n, s in cand["nodeids"].items() if s in ("failed", "error")}
    new_failures = sorted(c_bad - p_bad)
    fixed = sorted(p_bad - c_bad)

    report = {
        "gate": "RT-FULL relative-to-parent",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "primary_gate_field": "new_failures_relative_to_parent",
        "new_failures_relative_to_parent": len(new_failures),
        "new_failure_nodeids": new_failures,
        "fixed_relative_to_parent": fixed,
        "verdict": "PASS" if not new_failures else "FAIL",
        "environment_side_evidence": {
            "note": "绝对 passed/failed 不可移植，仅旁证；门禁只看相对父新增失败",
            "parent": {"label": args.parent_label,
                       "totals": parent["totals"],
                       "failed_nodeids": len(p_bad),
                       **load_meta(out_dir, args.parent_label)},
            "candidate": {"label": args.candidate_label,
                          "totals": cand["totals"],
                          "failed_nodeids": len(c_bad),
                          **load_meta(out_dir, args.candidate_label)},
        },
        "forbidden": "禁止写生产库 data/tradingagents.db；禁止真实模型/供应商调用",
    }
    out = Path(args.out) if args.out else out_dir / "rt_full_gate_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verdict={report['verdict']} new_failures={len(new_failures)} -> {out}")
    return 0 if not new_failures else 1


if __name__ == "__main__":
    sys.exit(main())
