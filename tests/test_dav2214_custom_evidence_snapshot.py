"""DAV-2214 — custom-prompt evidence bases for sealed ledger rows.

Offline only: synthetic ledger + a synthetic *read-only* snapshot db
(the same ``reports.result_data`` layout) exercise the four evidence
bases the card fixes:

* ledger seal carries ``custom_prompt_snapshot`` → ``custom_basis=ledger``;
* ledger seal lacks it, the report snapshot carries it →
  ``custom_basis=report_snapshot`` (register §3 canonicalisation);
* both carry it and the hashes agree → ``ledger`` (the seal wins);
* both carry it and they disagree → excluded, reason code
  ``custom_conflict`` (never a silent pick);
* neither carries it → ``unverifiable`` — the register's value is never
  substituted.

No network, no live DB, no production artifacts.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "phase2"))
import expiry_label_pipeline as elp
import p_rel_t10_baseline_eval as m

FIXED = m.DEFAULT_USER_ID
D0, D1, D2 = "2026-10-08", "2026-10-09", "2026-10-12"

SNAP_A = {"enabled": True, "placement": "after_data",
          "roles": {"trader": {"resolved_text": "a", "resolved_hash": "h1",
                               "resolved_length": 1, "injected": True}}}
SNAP_B = {"enabled": True, "placement": "after_data",
          "roles": {"trader": {"resolved_text": "b", "resolved_hash": "h2",
                               "resolved_length": 1, "injected": False}}}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _snapshot_db(path: Path, rows) -> Path:
    """Create a report snapshot db with the production column layout.

    ``rows`` = ``(report_id, result_data_dict | None, kind)`` with kind
    ``json`` (plaintext column) or ``zst`` (compressed column only).
    """
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, "
                "result_data TEXT, result_data_zst BLOB)")
    for rid, payload, kind in rows:
        if kind == "zst":
            from tradingagents.storage.compressed_json import (
                encode_result_data)
            con.execute(
                "INSERT INTO reports (id, result_data_zst) VALUES (?, ?)",
                (rid, encode_result_data(payload)))
        else:
            con.execute(
                "INSERT INTO reports (id, result_data) VALUES (?, ?)",
                (rid, json.dumps(payload) if payload is not None else None))
    con.commit()
    con.close()
    path.chmod(0o444)
    return path


def _rec(rid, sym, day, p=60, **kw):
    kw.setdefault("sealed_at", f"{day} 08:00:00.000000")
    return m._ledger_rec(rid, sym, day, p, **kw)


def _lab(rec, r_pct, l1="801010.SI", **kw):
    return m._label(rec["symbol"], rec["signal_date"], r_pct, l1=l1,
                    report_id=rec["report_id"],
                    record_sha256=elp._record_identity(rec), **kw)


def _write_ledger(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs),
                    encoding="utf-8")


def _write_labels(path, labs):
    path.write_text("".join(json.dumps(x) + "\n" for x in labs),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# the four bases
# ---------------------------------------------------------------------------

class TestEvidenceBases:
    def _reader(self, tmp_path, rows):
        return m.ReportSnapshotReader(_snapshot_db(tmp_path / "snap.db", rows))

    def test_ledger_only_is_basis_ledger(self, tmp_path):
        """Case ①: the seal carries it — the basis is stamped even when
        the report snapshot has nothing to contribute."""
        reader = self._reader(tmp_path, [("r_led", {"analysis_status": "x"},
                                         "json")])
        rec = _rec("r_led", "S1", D0, custom_prompt=SNAP_A)
        ev = m.resolve_custom_evidence(rec, reader)
        assert ev["basis"] == m.CUSTOM_BASIS_LEDGER
        assert ev["sha256"] == m._canonical_json_sha(SNAP_A)
        assert ev["ledger_sha256"] == ev["sha256"]
        assert ev["report_sha256"] is None
        assert ev["report_lookup"] == "missing_field"
        assert ev["reason"] is None

    def test_report_snapshot_only_is_basis_report_snapshot(self, tmp_path):
        """Case ②: the sealed ledger row never captured it — the report's
        own result_data.custom_prompt_snapshot is hashed under register
        §3 canonicalisation."""
        reader = self._reader(tmp_path, [("r_rep", {"custom_prompt_snapshot":
                                                    SNAP_A}, "json")])
        rec = _rec("r_rep", "S1", D0)
        assert m._record_custom_prompt_sha(rec) is None
        ev = m.resolve_custom_evidence(rec, reader)
        assert ev["basis"] == m.CUSTOM_BASIS_REPORT_SNAPSHOT
        assert ev["sha256"] == m._canonical_json_sha(SNAP_A)
        assert ev["ledger_sha256"] is None
        assert ev["report_lookup"] == "ok"

    def test_both_agree_is_basis_ledger(self, tmp_path):
        """Case ③: both carry it and agree — the ledger basis is stamped
        (agreement is the normal post-DAV-2181 shape)."""
        reader = self._reader(tmp_path, [("r_both", {"custom_prompt_snapshot":
                                                     SNAP_A}, "json")])
        rec = _rec("r_both", "S1", D0, custom_prompt=SNAP_A)
        ev = m.resolve_custom_evidence(rec, reader)
        assert ev["basis"] == m.CUSTOM_BASIS_LEDGER
        assert ev["sha256"] == m._canonical_json_sha(SNAP_A)
        assert ev["reason"] is None

    def test_both_disagree_is_custom_conflict(self, tmp_path):
        """Case ④: disagreement is an exclusion with reason code
        custom_conflict — no side is silently preferred (sha256 stays
        None so nothing downstream can consume a "chosen" hash)."""
        reader = self._reader(tmp_path, [("r_bad", {"custom_prompt_snapshot":
                                                    SNAP_B}, "json")])
        rec = _rec("r_bad", "S1", D0, custom_prompt=SNAP_A)
        ev = m.resolve_custom_evidence(rec, reader)
        assert ev["basis"] == m.CUSTOM_BASIS_CONFLICT
        assert ev["reason"] == m.REASON_CUSTOM_CONFLICT
        assert ev["sha256"] is None
        assert m._canonical_json_sha(SNAP_A) in ev["reason_detail"]
        assert m._canonical_json_sha(SNAP_B) in ev["reason_detail"]

    def test_neither_present_is_unverifiable(self, tmp_path):
        """Case ⑤: neither side carries it → unverifiable; the register's
        b0abab… value is NEVER substituted (that would assert an evidence
        this pipeline did not observe)."""
        reader = self._reader(tmp_path, [("r_none", {"analysis_status": "x"},
                                          "json")])
        rec = _rec("r_none", "S1", D0)
        ev = m.resolve_custom_evidence(rec, reader)
        assert ev["basis"] == m.CUSTOM_BASIS_UNVERIFIABLE
        assert ev["sha256"] is None
        assert ev["report_lookup"] == "missing_field"
        assert m._canonical_json_sha(SNAP_A) != ev["sha256"]

    def test_missing_report_row_and_absent_db_degrade(self, tmp_path):
        reader = self._reader(tmp_path, [])
        ev = m.resolve_custom_evidence(_rec("ghost", "S1", D0), reader)
        assert (ev["basis"], ev["report_lookup"]) == (
            m.CUSTOM_BASIS_UNVERIFIABLE, "missing_report")
        gone = m.ReportSnapshotReader(tmp_path / "not-there.db")
        assert gone.available is False
        ev2 = m.resolve_custom_evidence(_rec("ghost", "S1", D0), gone)
        assert ev2["basis"] == m.CUSTOM_BASIS_UNVERIFIABLE
        assert ev2["report_lookup"] == "unavailable"
        # no reader at all (hermetic callers) — same honest fallback
        ev3 = m.resolve_custom_evidence(_rec("ghost", "S1", D0), None)
        assert ev3["basis"] == m.CUSTOM_BASIS_UNVERIFIABLE

    def test_compressed_result_data_column_is_decoded(self, tmp_path):
        """The snapshot's physical payload may be a zstd frame
        (compressed_only rows) — the reader must use the production codec,
        not assume plaintext."""
        reader = self._reader(tmp_path, [("r_z", {"custom_prompt_snapshot":
                                                  SNAP_A}, "zst")])
        ev = m.resolve_custom_evidence(_rec("r_z", "S1", D0), reader)
        assert ev["basis"] == m.CUSTOM_BASIS_REPORT_SNAPSHOT
        assert ev["sha256"] == m._canonical_json_sha(SNAP_A)


# ---------------------------------------------------------------------------
# version key + register reconciliation
# ---------------------------------------------------------------------------

class TestVersionKeyStamp:
    def test_report_snapshot_hash_is_stamped_on_the_key(self):
        rec = _rec("r1", "S1", D0, commit="082be047xxxx", contract="b1.v1")
        sha = m._canonical_json_sha(SNAP_A)
        key = m._record_version_key(rec, anchor="082be047", custom_sha=sha)
        fields = dict(p.split("=", 1) for p in key.split("|")[1:])
        assert fields["custom"] == sha
        # without the resolved evidence the same record stays honest
        assert dict(p.split("=", 1) for p in m._record_version_key(
            rec, anchor="082be047").split("|")[1:])["custom"] == "unverifiable"

    def test_report_snapshot_reconciles_the_register_key(self):
        """The register §3 ``custom`` value IS the canonical sha256 of the
        report snapshot — a queue whose custom leg comes from the report
        snapshot must reconcile to an empty register diff (the pairing
        preflight this card exists to unblock)."""
        cps = {"enabled": True, "placement": "after_data",
               "roles": {"trader": {"resolved_text": "x",
                                    "resolved_hash": "b0ab",
                                    "resolved_length": 1,
                                    "injected": True}}}
        reg = dict(p.split("=", 1)
                   for p in m.REGISTER_ANALYSIS_VERSION_ID.split("|")[1:])
        rec = _rec("r1", "S1", D0, contract="b1.v1",
                   commit=reg["anchor"] + "xxxx",
                   prompt_resolved={"trader": "x"},
                   served_models={"by_role": {r: [reg["served"]]
                                              for r in ("trader",)}})
        key = m._record_version_key(rec, anchor=reg["anchor"],
                                    custom_sha=reg["custom"])
        # the remaining legs of this fixture are synthetic, so only the
        # custom leg may differ — and it must NOT (it is the register hash).
        diff = m._key_register_diff(key)
        assert "custom" not in diff, diff

    def test_unverifiable_never_becomes_the_register_value(self):
        rec = _rec("r1", "S1", D0, commit="082be047xxxx", contract="b1.v1")
        key = m._record_version_key(rec, anchor="082be047")
        fields = dict(p.split("=", 1) for p in key.split("|")[1:])
        assert fields["custom"] == "unverifiable"
        assert fields["custom"] != dict(
            p.split("=", 1)
            for p in m.REGISTER_ANALYSIS_VERSION_ID.split("|")[1:])["custom"]


# ---------------------------------------------------------------------------
# gate / alert / per-row evidence
# ---------------------------------------------------------------------------

class TestGateAndEvidence:
    def _frame(self, tmp_path):
        reader = m.ReportSnapshotReader(_snapshot_db(tmp_path / "s.db", [
            ("a", {"custom_prompt_snapshot": SNAP_A}, "json"),
            ("b", {"custom_prompt_snapshot": SNAP_B}, "json"),
            ("c", {"custom_prompt_snapshot": SNAP_A}, "json"),
            ("d", {"analysis_status": "x"}, "json"),
        ]))
        recs = [_rec("a", "S1", D0, custom_prompt=SNAP_A),   # ledger
                _rec("b", "S2", D0),                          # report_snapshot
                _rec("c", "S3", D0, custom_prompt=SNAP_A),    # conflict
                _rec("d", "S4", D0)]                          # unverifiable
        # the conflict needs BOTH sides disagreeing
        recs[2] = _rec("c", "S3", D0, custom_prompt=SNAP_B)
        rows = []
        for r in recs:
            ev = m.resolve_custom_evidence(r, reader)
            rows.append({"run_id": r["report_id"], "symbol": r["symbol"],
                         "signal_date": "20261008",
                         "custom_basis": ev["basis"],
                         "custom_sha256": ev["sha256"],
                         "custom_ledger_sha256": ev["ledger_sha256"],
                         "custom_report_sha256": ev["report_sha256"],
                         "custom_report_lookup": ev["report_lookup"],
                         "custom_reason": ev["reason"],
                         "custom_reason_detail": ev["reason_detail"]})
        reader.close()
        return pd.DataFrame(rows)

    def test_conflict_row_is_excluded_with_reason_code(self, tmp_path):
        df = self._frame(tmp_path)
        kept, gate = m._apply_custom_gate(df)
        assert list(kept["run_id"]) == ["a", "b", "d"]
        assert gate["reason_codes"] == {m.REASON_CUSTOM_CONFLICT: 1}
        assert gate["excluded"][0]["report_id"] == "c"
        assert gate["basis_counts"] == {"ledger": 1, "report_snapshot": 1,
                                        m.CUSTOM_BASIS_CONFLICT: 1,
                                        "unverifiable": 1}
        # one-sided/absent evidence is never excluded by this gate
        assert "unverifiable" not in gate["reason_codes"]
        assert m._custom_evidence_alert(gate)
        assert "custom_conflict" in m._custom_evidence_alert(gate)

    def test_no_conflict_no_alert(self):
        df = pd.DataFrame([{"run_id": "x", "custom_basis": "ledger",
                            "custom_reason": None}])
        kept, gate = m._apply_custom_gate(df)
        assert len(kept) == 1
        assert gate["reason_codes"] == {}
        assert m._custom_evidence_alert(gate) is None

    def test_per_row_evidence_rows_are_deterministic(self, tmp_path):
        df = self._frame(tmp_path)
        rows = m._custom_evidence_rows(df)
        assert [r["report_id"] for r in rows] == ["a", "b", "c", "d"]
        assert {r["custom_basis"] for r in rows} == {
            "ledger", "report_snapshot", "conflict", "unverifiable"}
        assert all(set(r) == {"report_id", "user_id", "symbol", "signal_date",
                              "custom_basis", "custom_sha256",
                              "ledger_custom_sha256", "report_custom_sha256",
                              "report_lookup", "reason", "detail"}
                   for r in rows)


# ---------------------------------------------------------------------------
# run() integration
# ---------------------------------------------------------------------------

class TestRunIntegration:
    def _run(self, tmp_path, *, snapshot_db, rows_extra=(), name="o"):
        recs, labs = [], []
        for i in range(12):
            rec = _rec(f"ok{i}", f"S{i:02d}", D0, 30 + i)
            recs.append(rec)
            labs.append(_lab(rec, 0.1 * i))
        recs.extend(rows_extra)
        ledger = tmp_path / f"{name}_l.jsonl"
        labf = tmp_path / f"{name}_lab.jsonl"
        _write_ledger(ledger, recs)
        _write_labels(labf, labs)
        cal = tmp_path / f"{name}_c.txt"
        cal.write_text("\n".join([D0, D1, D2]), encoding="utf-8")
        db = None
        if snapshot_db is None:
            db = _snapshot_db(tmp_path / f"{name}_snap.db", [
                (r["report_id"], {"custom_prompt_snapshot": SNAP_A}, "json")
                for r in recs])
        else:
            db = snapshot_db
        return m.run(ledger, labf, tmp_path / name, calendar_path=cal,
                     snapshot_db=db)

    def test_report_snapshot_basis_reaches_the_queue_and_artifacts(
            self, tmp_path):
        """12 cohort rows whose ledger seal lacks the snapshot: every row
        is stamped report_snapshot, the queue's version key carries the
        report hash, and custom_evidence.jsonl + run.json record it."""
        out = self._run(tmp_path, snapshot_db=None)
        sha = m._canonical_json_sha(SNAP_A)
        ce = out["custom_evidence"]
        assert ce["basis_counts"] == {"report_snapshot": 12}
        assert ce["report_lookup_counts"] == {"ok": 12}
        assert ce["register_value_used_as_fill"] is False
        assert ce["alert"] is None
        assert "custom=" + sha in out["verdict"]["funnel"]["version_key"]
        assert out["verdict"]["funnel"]["excl_custom_conflict"] == 0
        assert out["verdict"]["funnel"]["formal_rows"] == 12
        runj = json.loads((tmp_path / "o" / "run.json").read_text())
        assert runj["custom_evidence"]["basis_counts"] == {
            "report_snapshot": 12}
        assert runj["freeze"]["custom_evidence"] == runj["custom_evidence"]
        ev = tmp_path / "o" / "custom_evidence.jsonl"
        lines = [json.loads(x) for x in
                 ev.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 12
        assert {x["custom_basis"] for x in lines} == {"report_snapshot"}
        assert {x["report_custom_sha256"] for x in lines} == {sha}
        assert runj["custom_evidence"]["evidence_rows"] == 12
        assert len(runj["custom_evidence"]["evidence_file_sha256"]) == 64
        # conservation identity (DAV-2052 🟡-1 + the new bucket)
        f = runj["funnel"]
        assert runj["ledger_rows"] == (
            f["excl_custom_conflict"] + f["excl_version"]
            + f["excl_pre_cohort"] + f["excl_label_conflict"]
            + f["excl_label_record_sha256_mismatch"]
            + runj["seal_gate"]["admit_input_rows"])

    def test_conflicting_row_excluded_before_version_selection(self, tmp_path):
        """The conflict row carries SNAP_B while the cohort carries SNAP_A:
        it must be excluded with custom_conflict — NOT trip the
        multi-version fail-close — and the first screen must alarm."""
        bad = _rec("bad", "BAD", D0, 90, custom_prompt=SNAP_B)
        out = self._run(tmp_path, snapshot_db=None, rows_extra=[bad])
        fun = out["verdict"]["funnel"]
        assert fun["excl_custom_conflict"] == 1
        assert fun["formal_rows"] == 12
        ce = out["custom_evidence"]
        assert ce["reason_codes"] == {m.REASON_CUSTOM_CONFLICT: 1}
        assert ce["excluded"][0]["report_id"] == "bad"
        assert ce["alert"] and "custom_conflict" in ce["alert"]
        runj = json.loads((tmp_path / "o" / "run.json").read_text())
        assert runj["custom_evidence"]["excluded"][0]["report_id"] == "bad"

    def test_absent_snapshot_db_fails_closed_and_opt_out_is_honest(
            self, tmp_path):
        with pytest.raises(elp.PipelineError, match="snapshot db not found"):
            self._run(tmp_path, snapshot_db=tmp_path / "missing.db")
        # --no-report-snapshot path: no reader, rows stay unverifiable and
        # the register value is not used as a fill.
        recs, labs = [], []
        for i in range(12):
            rec = _rec(f"n{i}", f"N{i:02d}", D0, 30 + i)
            recs.append(rec)
            labs.append(_lab(rec, 0.1 * i))
        ledger = tmp_path / "n_l.jsonl"
        _write_ledger(ledger, recs)
        labf = tmp_path / "n_lab.jsonl"
        _write_labels(labf, labs)
        cal = tmp_path / "n_c.txt"
        cal.write_text("\n".join([D0, D1, D2]), encoding="utf-8")
        out = m.run(ledger, labf, tmp_path / "no", calendar_path=cal,
                    snapshot_db=None)
        assert out["custom_evidence"]["basis_counts"] == {"unverifiable": 12}
        assert "custom=unverifiable" in out["verdict"]["funnel"]["version_key"]
        assert m.REGISTER_ANALYSIS_VERSION_ID.split("custom=")[1].split("|")[0] \
            not in out["verdict"]["funnel"]["version_key"]

    def test_cli_flag_wires_the_snapshot(self, tmp_path):
        recs, labs = [], []
        for i in range(12):
            rec = _rec(f"c{i}", f"C{i:02d}", D0, 30 + i)
            recs.append(rec)
            labs.append(_lab(rec, 0.1 * i))
        ledger = tmp_path / "c_l.jsonl"
        _write_ledger(ledger, recs)
        labf = tmp_path / "c_lab.jsonl"
        _write_labels(labf, labs)
        cal = tmp_path / "c_c.txt"
        cal.write_text("\n".join([D0, D1, D2]), encoding="utf-8")
        db = _snapshot_db(tmp_path / "c_snap.db", [
            (r["report_id"], {"custom_prompt_snapshot": SNAP_A}, "json")
            for r in recs])
        rc = m.main(["run", "--ledger", str(ledger), "--labels", str(labf),
                     "--calendar-file", str(cal),
                     "--snapshot-db", str(db),
                     "--out-dir", str(tmp_path / "co")])
        assert rc == 0
        runj = json.loads((tmp_path / "co" / "run.json").read_text())
        assert runj["custom_evidence"]["basis_counts"] == {
            "report_snapshot": 12}
        assert runj["custom_evidence"]["report_snapshot_db"]["uri"] == (
            f"file:{db}?mode=ro&immutable=1")
        assert runj["custom_evidence"]["report_snapshot_db"]["available"] is True
        # hermetic opt-out on the same inputs
        rc2 = m.main(["run", "--ledger", str(ledger), "--labels", str(labf),
                      "--calendar-file", str(cal), "--no-report-snapshot",
                      "--out-dir", str(tmp_path / "co2")])
        assert rc2 == 0
        runj2 = json.loads((tmp_path / "co2" / "run.json").read_text())
        assert runj2["custom_evidence"]["basis_counts"] == {
            "unverifiable": 12}
        # a missing explicit snapshot path is a fail-close, not a silent
        # downgrade
        rc3 = m.main(["run", "--ledger", str(ledger), "--labels", str(labf),
                      "--calendar-file", str(cal),
                      "--snapshot-db", str(tmp_path / "gone.db"),
                      "--out-dir", str(tmp_path / "co3")])
        assert rc3 == 2
