"""Unit checks for the audit tool only; no production gate changes."""
import copy
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "measurement", Path(__file__).with_name("dav1440_zero_model_measurement.py")
)
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def payload(direction="BULL", status="VALID", action="WAIT", basis=None, codes=None):
    return {
        "decision_status": {"analysis_status": status, "direction": direction,
                            "trade_action": action, "reason_codes": codes or []},
        "manager_verdict": {"direction": "偏多", "winner": "bull",
                            "adopted_claim_ids": [], "partially_adopted_claims": [],
                            "direction_basis": basis},
    }


class AuditTests(unittest.TestCase):
    def test_reason_without_basis_changes(self):
        p = payload(codes=["no_adjudicated_support"])
        result = measurement.project_horizon(p)
        self.assertTrue(result["would_change"])
        self.assertEqual(result["conditions"], ["no_adjudicated_support"])

    def test_unledgered_empty_changes_without_reason(self):
        p = payload(basis={"status": "unledgered", "same_direction_claims": []})
        self.assertTrue(measurement.project_horizon(p)["would_change"])

    def test_missing_null_nonempty_and_unknown_basis_do_not_expand_scope(self):
        for basis in (None, {}, {"status": "unledgered"},
                      {"status": "unledgered", "same_direction_claims": None},
                      {"status": "unledgered", "same_direction_claims": ["INV-1"]},
                      {"status": "unknown", "same_direction_claims": []},
                      {"status": "partial_only", "same_direction_claims": ["INV-1"]}):
            with self.subTest(basis=basis):
                self.assertFalse(measurement.project_horizon(payload(basis=basis))["would_change"])

    def test_existing_nondirectional_or_nonvalid_not_changed(self):
        for direction, status in (("NEUTRAL", "VALID"), ("N/A", "ABSTAIN"),
                                  ("BEAR", "PARTIAL"), ("N/A", "INVALID_RUN")):
            p = payload(direction=direction, status=status, codes=["no_adjudicated_support"])
            self.assertFalse(measurement.project_horizon(p)["would_change"])

    def test_partial_presence_does_not_exempt_unledgered(self):
        p = payload(basis={"status": "unledgered", "same_direction_claims": []})
        p["manager_verdict"]["partially_adopted_claims"] = ["INV-2"]
        result = measurement.project_horizon(p)
        self.assertTrue(result["would_change"])
        self.assertTrue(result["has_partially_adopted_claims"])

    def test_both_proposals_preserve_input_and_block_executable_action(self):
        p = payload(action="BUY", codes=["no_adjudicated_support"])
        before = copy.deepcopy(p)
        result = measurement.project_horizon(p)
        self.assertEqual(p, before)
        self.assertEqual(result["neutral_candidate"], "VALID/NEUTRAL/WAIT")
        self.assertEqual(result["abstain_candidate"], "ABSTAIN/N/A/WAIT")
        self.assertEqual(result["proposed_status"], "NEUTRAL 或 ABSTAIN（待总控裁定）")

    def test_manager_fallback_preserves_empty_partial_and_raw_direction(self):
        p = payload(codes=["no_adjudicated_support"])
        mv = p.pop("manager_verdict")
        p["investment_debate_state"] = {"manager_verdict": mv}
        p["investment_plan"] = '<!-- MANAGER_VERDICT: {"direction":"看多","partially_adopted_claims":["INV-1"]} -->'
        result = measurement.project_horizon(p)
        self.assertEqual(result["manager_direction"], "偏多")
        self.assertEqual(result["manager_direction_original"], "看多")
        self.assertFalse(result["has_partially_adopted_claims"])
        self.assertEqual(result["raw_partially_adopted_claims"], ["INV-1"])

    def test_aliases_are_not_double_counted_and_single_label_preserved(self):
        p = payload()
        d = {"short_term": p, "horizons": {"short": copy.deepcopy(p)}}
        self.assertEqual([(h, path) for h, path, _ in measurement.horizons(d)],
                         [("short", "short_term")])
        self.assertEqual(measurement.horizons({"horizon": "medium"})[0][0], "medium")
        self.assertEqual(measurement.horizons({})[0][0], "single_unspecified")

    def test_freeze_reads_completed_only_and_strips_private_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "audit.db"
            con = sqlite3.connect(db)
            con.execute('CREATE TABLE reports (id, symbol, trade_date, status, created_at, updated_at, result_data)')
            con.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?)',
                        ("a", "600941.SH", "2026-05-27", "completed", "now", "now",
                         '{"api_key":"do-not-export","user_context":{"secret":"private"}}'))
            con.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?)',
                        ("b", "x", "x", "failed", "now", "now", 'null'))
            con.commit()
            before = db.read_bytes()
            con.close()
            frozen = measurement.freeze(db)
            self.assertEqual(len(frozen["reports"]), 1)
            self.assertNotIn("api_key", str(frozen))
            self.assertNotIn("user_context", str(frozen))
            self.assertEqual(frozen["manifest"]["production_rows_before"], 2)
            self.assertEqual(frozen["manifest"]["production_rows_after"], 2)
            self.assertEqual(db.read_bytes(), before)

    def test_null_historical_report_is_explicitly_unassessable(self):
        self.assertEqual(measurement.horizons(None), [])
        result = measurement.project_horizon({"direction": "看多"})
        self.assertFalse(result["assessable"])
        self.assertFalse(result["would_change"])


if __name__ == "__main__":
    unittest.main()
