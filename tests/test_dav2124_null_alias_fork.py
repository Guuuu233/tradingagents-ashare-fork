"""DAV-2124: narrow null-alias-fork allowlist for the B-4 slim migration.

Implements the 10-16 总控裁定. ``detect_alias_conflicts`` is NOT relaxed;
the allowlist is a separate, opt-in, positive gate consulted only by the
offline migration. Four conditions must all hold before a conflicting row
may be converted:

  ① every common-key difference is "alias null, term has a value";
  ② the alias has no key the term side lacks;
  ③ no contradiction at any depth;
  ④ differences are confined to ``shadow_credit_metrics`` and the
     ``shadow_credit_metrics`` nested inside ``investment_debate_state``.

The positive sample is modelled on the real production shape measured in the
DAV-2124 diagnosis (T+5 stamps present in ``<h>_term``, ``null`` in the
alias). Three interception cases are required by the ruling: alias-only key,
genuine contradiction, and a contradiction appearing in some OTHER field.
"""
import copy
import json

import pytest
from scripts import slim_result_data_storage as slim
from tradingagents.storage.result_data_compat import (
    AliasForkRejected,
    StorageCompatConflict,
    assert_alias_fork_allowlisted,
    classify_alias_fork,
    detect_alias_conflicts,
    expand_compat_view,
    is_canonical_storage,
)


# --------------------------------------------------------------------------
# fixtures — shaped like the measured 579 rows
# --------------------------------------------------------------------------

def t5_scm(evaluated=True, price=46.85):
    """The ``<h>_term`` side: carries the T+5 stamps the alias never got."""
    scm = {
        "schema_version": "h1a_json_v1",
        "credit_weighting_enabled": False,
        "bull_verified_rate": 0.5,
        "bear_verified_rate": 0.5,
        "sample_count": 1,
    }
    if evaluated:
        scm.update({
            "is_t_plus_5_due": True,
            "t_plus_5_evaluated": True,
            "t_plus_5_date": "2026-10-09",
            "t_plus_5_price": price,
            "t_plus_5_return_pct": 3.5,
            "t_plus_5_direction_hit": False,
            "t_plus_5_status": "due_and_evaluated",
            "t_plus_5_provenance": "tushare_daily",
        })
    else:
        scm.update({
            "is_t_plus_5_due": True,
            "t_plus_5_evaluated": False,
            "t_plus_5_missing_reason": "calendar_not_reached",
        })
    return scm


def unit(horizon, evaluated=True):
    payload = {
        "horizon": horizon,
        "status": "completed",
        "trade_action": "BUY",
        "direction": "看多",
        "manager_verdict": {"winner": "bull"},
        "shadow_credit_metrics": t5_scm(evaluated),
        # the alias never mirrors these term-only stamps
        "decision_model_version": "decision_model.v1",
        "generated_by_commit_sha": "b" * 40,
    }
    payload["investment_debate_state"] = {
        "count": 3,
        "protocol_stage": "judge",
        # nested mirror of the same scm — the 1148 "real_divergence" cases
        "shadow_credit_metrics": copy.deepcopy(payload["shadow_credit_metrics"]),
    }
    return payload


def alias_of(term_slice):
    """Build the stale legacy alias: same keys, T+5 values nulled out.

    The alias is an *independent* copy of the slice (the legacy writer
    serialized it separately), so the nested ``investment_debate_state``
    mirror is nulled on its own terms too — matching the measured shape
    where the nested scm diverges exactly as the top-level one does.
    """
    alias = copy.deepcopy(term_slice)
    # the alias physically drops the term-only stamps
    for k in ("decision_model_version", "generated_by_commit_sha"):
        alias.pop(k, None)

    def nullify(node):
        for key in list(node):
            if key.startswith("t_plus_5") or key == "is_t_plus_5_due":
                if key in ("t_plus_5_missing_reason",):
                    # measured: the alias has no key at all for this one
                    node.pop(key)
                else:
                    node[key] = None
        if "shadow_credit_metrics" in node:
            nullify(node["shadow_credit_metrics"])

    nullify(alias)
    # the nested mirror inside investment_debate_state is a separate object
    # in the legacy row; give it its own stale copy.
    if "investment_debate_state" in alias:
        ids = alias["investment_debate_state"]
        ids["shadow_credit_metrics"] = copy.deepcopy(ids["shadow_credit_metrics"])
        nullify(ids)
    return alias


def fork_row(evaluated=True):
    short = unit("short", evaluated)
    medium = unit("medium", evaluated)
    return {
        "symbol": "000296.SZ",
        "trade_date": "2026-10-09",
        "mode": "dual_horizon",
        "short_term": short,
        "medium_term": medium,
        "horizons": {
            "short": alias_of(short),
            "medium": alias_of(medium),
        },
    }


# --------------------------------------------------------------------------
# the positive sample: the real 579-row shape must be allowlisted
# --------------------------------------------------------------------------

def test_realistic_fork_row_is_allowlisted():
    row = fork_row()
    # pre-condition: the strict detector DOES fire (this is the whole point)
    assert detect_alias_conflicts(row), "fixture must be a genuine conflict row"
    verdict = classify_alias_fork(row)
    assert verdict is not None
    assert verdict["category"] == "t5_null_alias_fork"
    assert "shadow_credit_metrics" in verdict["fields"]
    assert "investment_debate_state.shadow_credit_metrics" in verdict["fields"]


def test_allowlisted_row_is_classified_but_conversion_stays_blocked():
    """See test_allowlisted_row_cannot_preserve_the_served_view for why."""
    row = fork_row()
    raw = json.dumps(row, ensure_ascii=False, indent=2)
    # strict path fails closed (unchanged)
    with pytest.raises(StorageCompatConflict):
        slim.convert_legacy_text(raw)
    # allowlisted path classifies, then still fails closed on the view contract
    assert classify_alias_fork(row) is not None
    with pytest.raises(ValueError, match="round-trip differs"):
        slim.convert_legacy_text(raw, allow_null_alias_fork=True)
def test_allowlist_is_opt_in_only():
    """Without the flag the strict detector's verdict is untouched."""
    row = fork_row()
    raw = json.dumps(row, ensure_ascii=False)
    with pytest.raises(StorageCompatConflict):
        slim.convert_legacy_text(raw)


# --------------------------------------------------------------------------
# the three required interception cases
# --------------------------------------------------------------------------

def test_rejects_alias_only_key():
    """② alias has a key the term side lacks -> must be blocked."""
    row = fork_row()
    row["horizons"]["short"]["shadow_credit_metrics"]["legacy_only"] = 123
    with pytest.raises(AliasForkRejected) as e:
        assert_alias_fork_allowlisted(row)
    assert e.value.category == "alias_only_key"
    with pytest.raises(StorageCompatConflict):
        slim.convert_legacy_text(json.dumps(row, ensure_ascii=False),
                                 allow_null_alias_fork=True)


def test_rejects_genuine_contradiction():
    """③ both sides non-null and unequal -> must be blocked."""
    row = fork_row()
    row["horizons"]["short"]["shadow_credit_metrics"]["t_plus_5_price"] = 99.99
    with pytest.raises(AliasForkRejected) as e:
        assert_alias_fork_allowlisted(row)
    assert e.value.category == "contradiction"
    with pytest.raises(StorageCompatConflict):
        slim.convert_legacy_text(json.dumps(row, ensure_ascii=False),
                                 allow_null_alias_fork=True)


def test_rejects_contradiction_in_other_field():
    """④ divergence outside scm / debate_state -> must be blocked."""
    row = fork_row()
    row["horizons"]["short"]["direction"] = "看空"
    with pytest.raises(AliasForkRejected):
        assert_alias_fork_allowlisted(row)
    with pytest.raises(StorageCompatConflict):
        slim.convert_legacy_text(json.dumps(row, ensure_ascii=False),
                                 allow_null_alias_fork=True)


def test_allowlist_verdict_matches_measured_production_shape():
    """The classifier must accept the real 579-row shape (DAV-2124 §2).

    The production alias diverges from ``<h>_term`` in exactly two places:
    the top-level ``shadow_credit_metrics``, and the ``t_plus_5_*`` /
    ``is_t_plus_5_due`` keys that ``investment_debate_state`` carries at its
    OWN top level (not only inside its nested scm mirror). A classifier that
    only understands the nested mirror rejects all 579 real rows.
    """
    row = fork_row()
    verdict = classify_alias_fork(row)
    assert verdict is not None
    assert verdict["category"] == "t5_null_alias_fork"
    assert "shadow_credit_metrics" in verdict["fields"]
    # the nested mirror is recognised too
    assert "investment_debate_state.shadow_credit_metrics" in verdict["fields"]


def test_rejects_debate_state_non_t5_divergence():
    """④ debate_state may only differ via T+5 stamps / the nested scm."""
    row = fork_row()
    row["horizons"]["short"]["investment_debate_state"]["protocol_stage"] = "opening"
    with pytest.raises(AliasForkRejected):
        assert_alias_fork_allowlisted(row)


def test_rejects_t5_key_with_non_null_contradiction():
    """① a T+5 key that disagrees with a NON-null alias value is a contradiction."""
    row = fork_row()
    row["horizons"]["short"]["investment_debate_state"]["t_plus_5_price"] = 99.99
    with pytest.raises(AliasForkRejected) as e:
        assert_alias_fork_allowlisted(row)
    assert e.value.category == "contradiction"


# --------------------------------------------------------------------------
# DOCUMENTED BLOCKER (DAV-2124, found during implementation)
# --------------------------------------------------------------------------

def test_allowlisted_row_cannot_preserve_the_served_view():
    """The allowlist admits the 579 rows, but conversion is still blocked.

    Why: the compat layer rebuilds ``horizons.<h>`` from ``<h>_term`` under
    ``storage_compat.horizon_key_masks``, and that mask is a list of TOP-LEVEL
    key names. It cannot express "inside ``shadow_credit_metrics`` these
    sub-keys were null in the alias". So for exactly the two allowlisted
    fields, the rebuilt view serves the term value where the legacy row
    served ``null`` — a real change to what callers read.

    B-4's hard acceptance criterion is that the compat-layer read stays
    byte-identical, so the row must keep failing closed until the mask gains
    a nested form. This test pins that behaviour so a future change to the
    mask cannot silently widen the blast radius.
    """
    row = fork_row()
    assert classify_alias_fork(row) is not None, "must be allowlisted"
    raw = json.dumps(row, ensure_ascii=False, indent=2)
    # the byte-identical-view contract is what refuses the conversion
    with pytest.raises(ValueError, match="round-trip differs"):
        slim.convert_legacy_text(raw, allow_null_alias_fork=True)


def test_rejects_bool_vs_int_type_mismatch():
    """③ type mismatch is a contradiction, not an alias-null fork."""
    row = fork_row()
    row["horizons"]["short"]["shadow_credit_metrics"]["credit_weighting_enabled"] = 1
    with pytest.raises(AliasForkRejected):
        assert_alias_fork_allowlisted(row)


def test_non_fork_row_is_not_allowlisted():
    """A conflict-free row gains nothing from the allowlist."""
    row = fork_row()
    row["horizons"]["short"] = copy.deepcopy(row["short_term"])
    row["horizons"]["medium"] = copy.deepcopy(row["medium_term"])
    assert detect_alias_conflicts(row) == []
    assert classify_alias_fork(row) is None


# --------------------------------------------------------------------------
# scan-pass integration: counters + journal category
# --------------------------------------------------------------------------

def test_scan_pass_keeps_blocked_rows_in_conflicts_with_category(tmp_path):
    """The scan pass must NOT claim these rows as convertible yet.

    It records the rejection category per row so the journal shows exactly
    which rule caught each one, and ``converted``/``candidates`` stay 0.
    """
    import sqlite3
    db = tmp_path / "t3.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE reports (id VARCHAR(36) PRIMARY KEY, status VARCHAR(20),"
        " symbol VARCHAR(20), trade_date VARCHAR(10), result_data JSON)")
    conn.execute("INSERT INTO reports VALUES (?,?,?,?,?)",
                 ("r1", "completed", "000296.SZ", "2026-10-09",
                  json.dumps(fork_row(), ensure_ascii=False)))
    conn.commit()
    conn.close()
    result = {"scanned": 0, "already_canonical": 0, "resumed_skipped": 0,
              "non_dict": 0, "no_alias_content": 0, "candidates": 0,
              "conflicts": 0, "top_mdc_sole_copy": 0, "top_mdc_rebuildable": 0,
              "alias_fork_allowed": 0, "by_category": {}}
    planned = slim._scan_pass(str(db), set(), result, [])
    assert result["conflicts"] == 1
    assert result["candidates"] == 0
    assert planned == []
    assert result["by_category"]["second_pass_failed"] == 1


def test_scan_pass_allowlist_is_wired_but_view_contract_gates_it(tmp_path):
    """Counter wiring exists; the byte-identical-view check is what blocks."""
    import sqlite3
    db = tmp_path / "t4.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE reports (id VARCHAR(36) PRIMARY KEY, status VARCHAR(20),"
        " symbol VARCHAR(20), trade_date VARCHAR(10), result_data JSON)")
    conn.execute("INSERT INTO reports VALUES (?,?,?,?,?)",
                 ("r1", "completed", "000296.SZ", "2026-10-09",
                  json.dumps(fork_row(), ensure_ascii=False)))
    conn.commit()
    conn.close()
    result = {"scanned": 0, "already_canonical": 0, "resumed_skipped": 0,
              "non_dict": 0, "no_alias_content": 0, "candidates": 0,
              "conflicts": 0, "top_mdc_sole_copy": 0, "top_mdc_rebuildable": 0,
              "alias_fork_allowed": 0, "by_category": {}}
    slim._scan_pass(str(db), set(), result, [])
    assert result["alias_fork_allowed"] == 0
    assert result["by_category"].get("second_pass_failed") == 1
def test_detect_alias_conflicts_unchanged_by_this_card():
    """The safety net itself must be untouched (裁定: 不放宽)."""
    row = fork_row()
    # still fires on the allowlistable shape...
    assert detect_alias_conflicts(row)
    # ...and encode_canonical's default (no flag) still refuses it
    from tradingagents.storage.result_data_compat import encode_canonical
    with pytest.raises(StorageCompatConflict):
        encode_canonical(row)
    # but with the explicit flag it succeeds
    assert encode_canonical(row, allow_null_alias_fork=True)