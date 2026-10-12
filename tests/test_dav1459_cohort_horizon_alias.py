"""DAV-1459: cohort spec horizon tier alias normalization.

The printed canonical cohort key uses namespaced tier spellings
(``...:horizon.short``), while sample-side ``extract_sample_cohort`` produces
bare labels (``short``). ``normalize_horizon_label`` must collapse the
namespaced form so both spellings select the identical sample set, and a
printed key fed back as ``--cohort`` round-trips.

Acceptance:
1. ``horizon.short`` ≡ ``short`` (``medium`` likewise) — same filtered set.
2. Round-trip: printed canonical_key reused as --cohort selects the same set.
3. Three-part spec and ``legacy_unversioned[:<hz>]`` behavior unchanged.
4. Unrecognized tier values stay isolated (fail-closed), never silently
   folded into short/medium.
"""

from tradingagents.agents.utils.shadow_credit import (
    HORIZON_UNSPECIFIED,
    extract_sample_cohort,
    filter_reports_by_cohort,
    is_cohort_homogeneous,
    normalize_horizon_label,
    parse_cohort_spec,
)

_TRIAD = "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq"


def _key(r):
    """Stable identity for filtered results (filter returns dict copies)."""
    return (r.get("symbol"), r.get("horizon"), r.get("marker"))


def _unit(*, horizon=None, dmv="decision_model.v1", marker=None):
    """Minimal cohort-bearing sample; ``dmv=None`` makes it legacy-unversioned."""
    s = {
        "symbol": "600519.SH",
        "trade_date": "2026-09-20",
        "evidence_contract_version": "evidence_contract.v2",
        "price_basis_version": "price_basis.vendor_qfq",
    }
    if marker is not None:
        s["marker"] = marker
    if dmv is not None:
        s["decision_model_version"] = dmv
    if horizon is not None:
        s["horizon"] = horizon
    return s


class TestHorizonAliasNormalization:
    def test_namespaced_spellings_normalize_to_bare_labels(self):
        assert normalize_horizon_label("horizon.short") == "short"
        assert normalize_horizon_label("horizon.medium") == "medium"
        assert normalize_horizon_label("Horizon.Short") == "short"
        assert normalize_horizon_label("horizon.unspecified") == HORIZON_UNSPECIFIED
        assert normalize_horizon_label("short") == "short"
        assert normalize_horizon_label(None) is None
        assert normalize_horizon_label("") is None

    def test_sample_side_namespaced_horizon_collapses(self):
        # A sample stamped "horizon.short" lands in the same cohort as "short".
        assert extract_sample_cohort({"horizon": "horizon.short"})["horizon"] == "short"


class TestNamespacedSpecEqualsBareSpec:
    def test_quad_spec_alias_selects_same_samples(self):
        pool = [
            _unit(horizon="short", marker=1),
            _unit(horizon="medium", marker=2),
            _unit(horizon=None, marker=3),
            _unit(horizon="horizon.short", marker=4),
        ]
        bare, meta_bare = filter_reports_by_cohort(pool, f"{_TRIAD}:short")
        namespaced, meta_ns = filter_reports_by_cohort(pool, f"{_TRIAD}:horizon.short")
        assert len(bare) == 2
        assert {_key(r) for r in bare} == {_key(r) for r in namespaced}
        # Both spellings normalize to the same canonical key
        assert meta_ns["canonical_key"] == meta_bare["canonical_key"]
        assert meta_ns["horizon"] == "short"

    def test_medium_alias_selects_same_samples(self):
        pool = [_unit(horizon="short"), _unit(horizon="medium")]
        bare, _ = filter_reports_by_cohort(pool, f"{_TRIAD}:medium")
        namespaced, meta_ns = filter_reports_by_cohort(pool, f"{_TRIAD}:horizon.medium")
        assert len(bare) == len(namespaced) == 1
        assert bare[0]["horizon"] == "medium"
        assert meta_ns["canonical_key"] == f"{_TRIAD}:medium"

    def test_round_trip_printed_key_reselects_same_set(self):
        """The canonical_key printed in the report, fed back as --cohort,
        must select the identical sample set."""
        pool = [_unit(horizon="short", marker=1), _unit(horizon="medium", marker=2)]
        filtered, meta = filter_reports_by_cohort(pool, f"{_TRIAD}:short")
        printed_key = meta["canonical_key"]
        re_filtered, _ = filter_reports_by_cohort(pool, printed_key)
        assert {_key(r) for r in filtered} == {_key(r) for r in re_filtered}

        filtered2, meta2 = filter_reports_by_cohort(pool, f"{_TRIAD}:horizon.short")
        assert meta2["canonical_key"] == printed_key

    def test_parse_spec_alias_canonical_key(self):
        spec_alias = parse_cohort_spec(f"{_TRIAD}:horizon.short")
        spec_bare = parse_cohort_spec(f"{_TRIAD}:short")
        assert spec_alias["horizon"] == spec_bare["horizon"] == "short"
        assert spec_alias["canonical_key"] == spec_bare["canonical_key"]


class TestUnchangedBehavior:
    def test_three_part_spec_still_unspecified_bucket(self):
        pool = [_unit(horizon="short"), _unit(horizon=None)]
        spec = parse_cohort_spec(_TRIAD)
        assert spec["horizon"] == HORIZON_UNSPECIFIED
        assert spec["canonical_key"] == f"{_TRIAD}:{HORIZON_UNSPECIFIED}"
        filtered, meta = filter_reports_by_cohort(pool, _TRIAD)
        assert len(filtered) == 1
        assert meta["canonical_key"] == f"{_TRIAD}:{HORIZON_UNSPECIFIED}"

    def test_legacy_unversioned_forms_unchanged(self):
        pool = [
            _unit(horizon="short", dmv=None),
            _unit(horizon="medium", dmv=None),
            _unit(horizon=None, dmv=None),
        ]
        bare, meta_bare = filter_reports_by_cohort(pool, "legacy_unversioned")
        assert len(bare) == 1
        assert meta_bare["canonical_key"] == "legacy_unversioned"

        short_f, meta_s = filter_reports_by_cohort(pool, "legacy_unversioned:short")
        assert len(short_f) == 1
        assert meta_s["canonical_key"] == "legacy_unversioned:short"

        # The namespaced spelling also collapses on the legacy path.
        alias_f, meta_a = filter_reports_by_cohort(pool, "legacy_unversioned:horizon.short")
        assert {_key(r) for r in alias_f} == {_key(r) for r in short_f}
        assert meta_a["canonical_key"] == "legacy_unversioned:short"

    def test_sample_side_unspecified_sentinel_unchanged(self):
        # horizon.unspecified sentinel must NOT collapse to a tier bucket
        c = extract_sample_cohort({"horizon": "horizon.unspecified"})
        assert c["horizon"] == HORIZON_UNSPECIFIED
        assert c["horizon"] not in ("short", "medium")


class TestUnrecognizedHorizonFailsClosed:
    def test_unknown_tier_stays_isolated_never_silently_merged(self):
        pool = [_unit(horizon="short"), _unit(horizon="medium")]
        for bad in ("horizon.bogus", "quarterly", "horizon."):
            filtered, meta = filter_reports_by_cohort(pool, f"{_TRIAD}:{bad}")
            assert filtered == [], f"{bad!r} silently matched a tier cohort"
            assert meta["horizon"] == bad

    def test_unknown_tier_sample_not_merged_into_known_cohort(self):
        # A sample with an unrecognized horizon never enters short/medium.
        pool = [_unit(horizon="quarterly"), _unit(horizon="short")]
        for spec in (f"{_TRIAD}:short", f"{_TRIAD}:horizon.short", f"{_TRIAD}:medium"):
            filtered, _ = filter_reports_by_cohort(pool, spec)
            assert all(u.get("horizon") != "quarterly" for u in filtered)
        # It forms its own cohort, breaking homogeneity rather than merging.
        is_homo, _ = is_cohort_homogeneous(pool)
        assert is_homo is False
