"""DAV-1882 两臂输入快照逐字段比对工具的测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "phase2"))

import compare_input_snapshots as cis  # noqa: E402


def _snap(hash_, *, collected_at, report_id, bull):
    return {
        "input_snapshot": {
            "algo": "sha256", "hash": hash_, "bytes": len(bull),
            "collected_at": collected_at, "report_id": report_id,
            "prompts": {"zh": {"bull": bull}},
        }
    }


def test_flatten_keeps_nested_keys():
    flat = cis.flatten({"a": {"b": 1}, "c": [1, 2]})
    assert flat["a.b"] == 1
    assert flat["c"] == "<list len=2>"
    assert flat["c[0]"] == 1


def test_diff_separates_volatile_from_real_changes():
    a = _snap("aaa", collected_at="2026-10-13T09:00:00", report_id="r1", bull="A")
    b = _snap("bbb", collected_at="2026-10-13T09:00:07", report_id="r2", bull="B")
    rows = cis.diff_snapshots(a["input_snapshot"], b["input_snapshot"])
    kinds = {r["field"]: r["kind"] for r in rows}
    assert kinds["collected_at"] == "volatile"
    assert kinds["report_id"] == "volatile"
    assert kinds["prompts.zh.bull"] == "other"   # 真正的自变量差异


def test_identical_snapshots_report_no_diff():
    s = _snap("aaa", collected_at="t", report_id="r", bull="A")["input_snapshot"]
    assert cis.diff_snapshots(s, json.loads(json.dumps(s))) == []


def test_same_arm_rerun_isolates_volatile_fields(tmp_path):
    """同臂重跑只有 volatile 差异 → 两臂 hash 差异可归因到真实改动。"""
    o1 = _snap("aaa", collected_at="09:00:00", report_id="r1", bull="A")
    o2 = _snap("ccc", collected_at="10:00:00", report_id="r9", bull="A")
    n1 = _snap("bbb", collected_at="09:00:07", report_id="r2", bull="B")
    out = tmp_path / "r.json"
    rc = cis.main([str(_w(o1)), str(_w(n1)), "--rerun-A", str(_w(o2)), "--out", str(out)])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["A_hash"] == "aaa" and report["B_hash"] == "bbb"
    assert report["identical"] is False
    assert set(report["volatile_fields"]) == {"collected_at", "report_id"}
    # 同臂重跑：除 volatile 外无差异
    assert report["same_arm_rerun"]["identical"] is False
    assert set(report["same_arm_rerun"]["volatile_fields"]) == {"collected_at", "report_id"}
    assert "prompts.zh.bull" not in report["same_arm_rerun"]["volatile_fields"]


def _w(d):
    import tempfile

    p = Path(tempfile.mkdtemp()) / "s.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_cli_accepts_bare_snapshot_or_wrapped_result(tmp_path):
    bare = {"hash": "x", "collected_at": "t"}
    wrapped = {"result": {"input_snapshot": bare}}
    a = tmp_path / "a.json"; a.write_text(json.dumps(bare), encoding="utf-8")
    b = tmp_path / "b.json"; b.write_text(json.dumps(wrapped), encoding="utf-8")
    assert cis.main([str(a), str(b)]) == 0


@pytest.mark.parametrize("key", ["collected_at", "elapsed_seconds", "report_id",
                                 "job_id", "run_id", "generated_at", "cache_hit"])
def test_volatile_hints_cover_common_mandatory_churn(key):
    assert cis._is_volatile(key)
