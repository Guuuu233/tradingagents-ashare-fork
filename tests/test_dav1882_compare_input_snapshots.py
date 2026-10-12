"""DAV-1882 两臂输入快照比对工具的测试（总控 10-12 晚 v0.3 §3 口径）。

关键性质：
- **默认不剔除任何字段**（空白名单），任意差异都判不一致；
- 规范化只认显式逐路径白名单，禁子串/通配；
- 白名单 sha256 落进结果；
- 原始/规范化结果分别给出；
- valid / provider / evidence_* 等语义字段不得被误剔除；
- **判定口径本身的字段（hash / algo / keys / bytes）不得被白名单剔除**。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "phase2"))

import compare_input_snapshots as cis  # noqa: E402


def _snap(hash_, *, collected_at, report_id, bull="A", extra=None):
    d = {
        "algo": "sha256", "hash": hash_, "bytes": 100,
        "collected_at": collected_at, "report_id": report_id,
        "prompts": {"zh": {"bull": bull}},
    }
    if extra:
        d.update(extra)
    return {"input_snapshot": d}


def _w(obj) -> str:
    p = Path(tempfile.mkdtemp()) / "s.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 默认口径：不剔除任何字段
# ---------------------------------------------------------------------------


def test_flatten_keeps_nested_keys():
    flat = cis.flatten({"a": {"b": 1}, "c": [1, 2]})
    assert flat["a.b"] == 1
    assert flat["c[0]"] == 1


def test_empty_whitelist_flags_any_difference_as_inconsistent():
    a = _snap("aaa", collected_at="t1", report_id="r1")
    b = _snap("bbb", collected_at="t1", report_id="r1")   # 仅 hash 不同
    rep = cis.compare(a["input_snapshot"], b["input_snapshot"])
    assert rep["normalized"]["consistent"] is False
    assert rep["normalized"]["whitelist_paths"] == []
    assert rep["normalized"]["whitelist_sha256"] is None
    assert rep["normalized"]["diff_count"] >= 1


def test_identical_snapshots_are_consistent():
    s = _snap("aaa", collected_at="t", report_id="r")
    rep = cis.compare(s["input_snapshot"], json.loads(json.dumps(s["input_snapshot"])))
    assert rep["raw"]["consistent"] is True
    assert rep["normalized"]["consistent"] is True
    assert rep["raw"]["hash_equal"] is True


def test_semantic_fields_are_not_auto_excluded():
    """valid / provider / evidence_* 等不得因为名字像 volatile 就被抹掉。"""
    a = {"hash": "a", "valid": True, "provider": "p1",
         "evidence": "e1", "evidence_count": 3, "candidate_id": "c1",
         "debate": {"insight_id": "bull-1"}}
    b = {"hash": "b", "valid": False, "provider": "p2",
         "evidence": "e2", "evidence_count": 4, "candidate_id": "c2",
         "debate": {"insight_id": "bull-9"}}
    rep = cis.compare(a, b)
    paths = {r["path"] for r in rep["normalized"]["diffs"]}
    assert "valid" in paths
    assert "provider" in paths
    assert "evidence" in paths
    assert "evidence_count" in paths
    assert "candidate_id" in paths
    assert "debate.insight_id" in paths
    assert rep["normalized"]["consistent"] is False
    assert rep["normalized"]["excluded_count"] == 0


# ---------------------------------------------------------------------------
# 白名单：显式逐路径、禁通配、sha256 留档
# ---------------------------------------------------------------------------


def test_whitelist_excludes_only_listed_exact_paths():
    a = _snap("aaa", collected_at="t1", report_id="r1", extra={"valid": True})
    b = _snap("bbb", collected_at="t2", report_id="r2", extra={"valid": False})
    wl = ["collected_at", "report_id"]
    rep = cis.compare(a["input_snapshot"], b["input_snapshot"],
                      whitelist=wl, whitelist_sha256="deadbeef")
    assert rep["raw"]["consistent"] is False
    assert rep["raw"]["diff_count"] > rep["normalized"]["diff_count"]
    paths = {r["path"] for r in rep["normalized"]["diffs"]}
    assert "valid" in paths and "hash" in paths
    assert "collected_at" not in paths and "report_id" not in paths
    assert rep["normalized"]["whitelist_sha256"] == "deadbeef"
    assert rep["normalized"]["whitelist_paths"] == sorted(wl)


def test_whitelist_is_not_substring_matching():
    """白名单写 collected_at 不得连带剔除 collected_at_ms_x 这类别的路径。"""
    a = {"collected_at": "t1", "collected_at_ms_x": "1"}
    b = {"collected_at": "t2", "collected_at_ms_x": "2"}
    rep = cis.compare(a, b, whitelist=["collected_at"])
    paths = {r["path"] for r in rep["normalized"]["diffs"]}
    assert paths == {"collected_at_ms_x", "hash"} or "collected_at_ms_x" in paths


def test_load_whitelist_empty_by_default():
    paths, sha = cis.load_whitelist(None)
    assert paths == [] and sha is None


def test_load_whitelist_records_sha256(tmp_path):
    f = tmp_path / "wl.json"
    f.write_text(json.dumps({"paths": ["collected_at"]}), encoding="utf-8")
    paths, sha = cis.load_whitelist(str(f))
    assert paths == ["collected_at"]
    assert isinstance(sha, str) and len(sha) == 64


@pytest.mark.parametrize("entry", ["prompts.*", "debate.insight_*", "a[0]", "p%", "x?"])
def test_whitelist_rejects_wildcards(tmp_path, entry):
    f = tmp_path / "wl.json"
    f.write_text(json.dumps({"paths": [entry]}), encoding="utf-8")
    with pytest.raises(ValueError, match="精确 JSON 路径"):
        cis.load_whitelist(str(f))


def test_whitelist_accepts_bare_array(tmp_path):
    f = tmp_path / "wl.json"
    f.write_text(json.dumps(["collected_at", "report_id"]), encoding="utf-8")
    assert cis.load_whitelist(str(f))[0] == ["collected_at", "report_id"]


def test_whitelist_rejects_non_string_entry(tmp_path):
    f = tmp_path / "wl.json"
    f.write_text(json.dumps({"paths": [123]}), encoding="utf-8")
    with pytest.raises(ValueError, match="非空字符串"):
        cis.load_whitelist(str(f))

# ---------------------------------------------------------------------------
# 受保护字段（总控追加·升为必修）：白名单不得剔除判定口径本身
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry", [
    "hash", "algo", "keys", "bytes",
    "input_snapshot.hash", "input_snapshot.algo",
    "meta.keys", "nested.hash",
])
def test_whitelist_rejects_protected_verdict_fields(tmp_path, entry):
    """白名单命中 hash/algo/keys/bytes → 报错退出，不静默放行。"""
    f = tmp_path / "wl.json"
    f.write_text(json.dumps({"paths": [entry]}), encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        cis.load_whitelist(str(f))
    assert "不得包含用于判定比对口径的字段" in str(exc.value)
    assert entry in str(exc.value)

def test_whitelist_rejects_protected_field_among_valid_entries(tmp_path):
    f = tmp_path / "wl.json"
    f.write_text(json.dumps({"paths": ["collected_at", "hash"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        cis.load_whitelist(str(f))

def test_protected_paths_detects_nested_index_segments():
    assert cis.protected_paths(["a[0].hash", "b.keys"]) == ["a[0].hash", "b.keys"]
    assert cis.protected_paths(["collected_at", "report_id"]) == []

def test_hash_cannot_be_knocked_out_by_whitelist_at_compare_time():
    """直接调用 compare() 时受保护路径同样不生效，并被显式列出来。"""
    a = _snap("aaa", collected_at="t1", report_id="r1")
    b = _snap("bbb", collected_at="t2", report_id="r2")
    rep = cis.compare(a["input_snapshot"], b["input_snapshot"],
                      whitelist=["hash", "collected_at"])
    assert rep["normalized"]["whitelist_paths"] == ["collected_at"]
    assert rep["normalized"]["ignored_protected_paths"] == ["hash"]
    paths = {r["path"] for r in rep["normalized"]["diffs"]}
    assert "hash" in paths
    assert rep["normalized"]["consistent"] is False

def test_cli_exits_nonzero_on_protected_whitelist(tmp_path, capsys):
    a = _snap("aaa", collected_at="t1", report_id="r1")
    b = _snap("bbb", collected_at="t1", report_id="r1")
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"paths": ["hash"]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        cis.main([_w(a), _w(b), "--whitelist", str(wl)])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_reports_raw_and_normalized_separately(tmp_path):
    a = _snap("aaa", collected_at="t1", report_id="r1")
    b = _snap("bbb", collected_at="t2", report_id="r2")
    out = tmp_path / "r.json"
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"paths": ["collected_at"]}), encoding="utf-8")
    rc = cis.main([_w(a), _w(b), "--whitelist", str(wl), "--out", str(out)])
    assert rc == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert "raw" in rep and "normalized" in rep
    assert rep["raw"]["A_hash"] == "aaa" and rep["raw"]["B_hash"] == "bbb"
    assert rep["normalized"]["whitelist_sha256"]
    assert rep["inputs"]["whitelist_file"] == str(wl)


def test_cli_accepts_wrapped_and_bare_forms(tmp_path):
    bare = {"hash": "x", "collected_at": "t"}
    wrapped = {"result": {"input_snapshot": bare}}
    assert cis.main([_w(bare), _w(wrapped)]) == 0


def test_cli_without_whitelist_reports_no_exclusions(tmp_path):
    a = _snap("aaa", collected_at="t1", report_id="r1")
    b = _snap("bbb", collected_at="t2", report_id="r2")
    out = tmp_path / "r.json"
    cis.main([_w(a), _w(b), "--out", str(out)])
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["normalized"]["whitelist_paths"] == []
    assert rep["normalized"]["excluded_count"] == 0
    assert rep["normalized"]["whitelist_sha256"] is None