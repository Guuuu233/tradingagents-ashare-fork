"""DAV-2129: six read points must resolve through the authoritative view —
``<h>_term`` wins over a stale ``horizons.<h>`` alias mirror, and T+5 fields
read the authoritative copy.

Background (2124 问 3 / DAV-2128 实测): the 579-row B-4 conflict shape is a
stale ``horizons`` mirror — alias-side ``shadow_credit_metrics`` T+5 keys are
null while the authoritative ``<h>_term`` slice carries real values, plus a
stale *top-level* ``shadow_credit_metrics`` third copy (T+5 all null).
``result_data_compat_view`` alone is a no-op on these rows (non-canonical →
fail-close keeps the stored form), so the read points must prefer ``*_term``.

Every read point is driven through its *production* entry point (no
re-implementation of the view call inside the test) so a revert of the
candidate's fix — alias-first read, ``compat_view``, or a dropped
``<h>_term`` fallback — turns the suite red:

* read points 1-3, 6: sealed record / ledger helpers / D4 units;
* read point 4: ``expiry_label_pipeline.compute_label_row`` (production);
* read point 5: ``weekly_report.build_report`` §1 bucket counts + §3
  spread (parsed out of the rendered report, never matched against a
  fixed header literal);
* read point 7 (card-external twin): ``verify_h1b_gates._stream_run_gate_eval``
  streaming D4 — term slice, not the stale top-level copy.

Offline only — synthetic rows/records; no DB, no akshare, no network.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PHASE2 = REPO / "scripts" / "phase2"
sys.path.insert(0, str(PHASE2))
sys.path.insert(0, str(REPO))

from tradingagents.storage.result_data_compat import (  # noqa: E402
    is_canonical_storage,
    result_data_authoritative_view,
    result_data_compat_view,
)
from tradingagents.agents.utils.shadow_credit import (  # noqa: E402
    split_report_into_units,
    evaluate_h1b_system_gates,
    T_PLUS_5_STATUS_DUE_AND_EVALUATED,
    T_PLUS_5_STATUS_PENDING_DUE,
)
from tradingagents.eval.shadow_portfolio import (  # noqa: E402
    ledger_confidence,
    ledger_probability,
    ledger_risk_status,
    ledger_final_action,
)
from tradingagents.eval.trade_execution import (  # noqa: E402
    ledger_direction,
    ledger_trade_action,
)
from tradingagents.dataflows.return_labels import OutcomeStatus  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dsl = _load("daily_snapshot_ledger", PHASE2 / "daily_snapshot_ledger.py")

T5_KEYS = (
    "t_plus_5_status", "t_plus_5_date", "t_plus_5_price",
    "t_plus_5_return_pct", "t_plus_5_direction_hit",
    "t_plus_5_missing_reason",
)


def _term_slice(horizon: str, *, direction: str, scm_hit):
    """Authoritative ``<h>_term`` slice — carries real T+5 values."""
    scm = {
        "t_plus_5_status": T_PLUS_5_STATUS_DUE_AND_EVALUATED,
        "t_plus_5_direction_hit": scm_hit,
        "t_plus_5_date": "2026-10-13",
        "t_plus_5_return_pct": 0.031,
    }
    return {
        "horizon": horizon,
        "status": "completed",
        "direction": direction,
        "probability": 0.62 if direction == "看多" else 0.38,
        "confidence": 0.71,
        "trade_action": "BUY" if direction == "看多" else "SELL",
        "risk_status": "OK",
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "generated_by_commit_sha": "a" * 40,
        "shadow_credit_metrics": scm,
        "market_data_context": {
            "source_provenance": {
                "stock_data": {
                    "requested_as_of": "2026-10-14",
                    "actual_as_of": "2026-10-14",
                    "provenance_status": "verified",
                },
            },
        },
    }


def _stale_alias_slice(horizon: str, *, direction: str):
    """The stale ``horizons.<h>`` mirror: judgement fields match (F2) but
    T+5 keys inside shadow_credit_metrics are all null (the 579 shape)."""
    alias = _term_slice(horizon, direction=direction, scm_hit=None)
    # legacy alias never carried the term-only stamps
    for k in ("decision_model_version", "evidence_contract_version",
              "generated_by_commit_sha"):
        alias.pop(k, None)
    for k in T5_KEYS:
        alias["shadow_credit_metrics"][k] = None
    return alias


def stale_mirror_row(*, short_dir="看多", medium_dir="看空",
                     short_hit=True, medium_hit=None):
    """A 579-style legacy row: physical ``horizons`` alias is a stale
    mirror of ``<h>_term``; top-level ``shadow_credit_metrics`` is a third
    stale copy (all T+5 null)."""
    rd = {
        "symbol": "600519.SH",
        "trade_date": "2026-10-02",
        "mode": "dual_horizon",
        "short_term": _term_slice("short", direction=short_dir,
                                  scm_hit=short_hit),
        "medium_term": _term_slice("medium", direction=medium_dir,
                                   scm_hit=medium_hit),
        "is_t_plus_5_due": True,
        "t_plus_5_status": T_PLUS_5_STATUS_DUE_AND_EVALUATED,
        "horizons": {
            "short": _stale_alias_slice("short", direction=short_dir),
            "medium": _stale_alias_slice("medium", direction=medium_dir),
        },
        "shadow_credit_metrics": {k: None for k in T5_KEYS},
        "input_snapshot": {"algo": "sha256", "hash": "x",
                           "keys": ["stock_data"]},
        "market_data_context": {
            "short": _term_slice("short", direction=short_dir,
                                 scm_hit=short_hit)["market_data_context"],
            "medium": _term_slice("medium", direction=medium_dir,
                                  scm_hit=medium_hit)["market_data_context"],
        },
    }
    return rd


# ---------------------------------------------------------------------------
# view contract


def test_compat_view_is_noop_on_stale_mirror_row():
    """F1 guard: compat_view returns the stored form on the 579 shape —
    this is exactly why plain compat_view is NOT enough here."""
    rd = stale_mirror_row()
    assert not is_canonical_storage(rd)
    assert result_data_compat_view(rd) is rd


def test_authoritative_view_term_wins_on_stale_mirror():
    rd = stale_mirror_row()
    view = result_data_authoritative_view(rd)
    assert view is not rd
    # term values surface through the horizons slot
    assert view["horizons"]["short"]["direction"] == "看多"
    assert (view["horizons"]["short"]["shadow_credit_metrics"]
            ["t_plus_5_direction_hit"]) is True
    assert (view["horizons"]["medium"]["shadow_credit_metrics"]
            ["t_plus_5_direction_hit"]) is None
    # alias slot keeps the legacy key shape (term-only stamps not injected)
    assert "decision_model_version" not in view["horizons"]["short"]
    # stored form untouched
    assert rd["horizons"]["short"]["shadow_credit_metrics"][
        "t_plus_5_direction_hit"] is None


def test_authoritative_view_canonical_matches_compat_view():
    """Canonical rows: horizons.<h> rebuilt from <h>_term — same as
    compat_view (authoritative by construction)."""
    from tradingagents.storage.result_data_compat import encode_canonical
    rd = stale_mirror_row()
    # encode a *consistent* dual-alias row (fix the stale mirror first so
    # encode passes fail-close), then verify the read views agree.
    consistent = copy.deepcopy(rd)
    consistent["horizons"] = {
        h: {k: v for k, v in consistent[f"{h}_term"].items()
            if k not in ("decision_model_version",
                         "evidence_contract_version",
                         "generated_by_commit_sha")}
        for h in ("short", "medium")
    }
    consistent.pop("shadow_credit_metrics", None)
    canon = encode_canonical(consistent)
    assert is_canonical_storage(canon)
    v_auth = result_data_authoritative_view(canon)
    v_compat = result_data_compat_view(canon)
    assert v_auth == v_compat
    assert v_auth["horizons"]["short"]["direction"] == "看多"
    assert (v_auth["horizons"]["short"]["shadow_credit_metrics"]
            ["t_plus_5_direction_hit"]) is True


def test_authoritative_view_passthrough_without_alias():
    rd = {"short_term": _term_slice("short", direction="看多", scm_hit=True)}
    assert result_data_authoritative_view(rd) is rd
    assert result_data_authoritative_view(None) is None
    assert result_data_authoritative_view("x") == "x"


def test_authoritative_view_alias_only_key_kept():
    """Alias-only content is preserved in the read view (fail-close storage
    belongs to B-4, not the reader) — but shared keys still take term."""
    rd = stale_mirror_row()
    rd["horizons"]["short"]["alias_only_field"] = "keepme"
    view = result_data_authoritative_view(rd)
    assert view["horizons"]["short"]["alias_only_field"] == "keepme"
    assert (view["horizons"]["short"]["shadow_credit_metrics"]
            ["t_plus_5_direction_hit"]) is True


# ---------------------------------------------------------------------------
# read point 1: daily_snapshot_ledger._horizon_judgement (was :366)


def _seal(rd_dict):
    row = dict(
        id="r2129", user_id=dsl.DEFAULT_USER_ID, symbol="600519.SH",
        industry="白酒", trade_date="2026-10-14", status="completed",
        analysis_status="VALID", decision="BUY", direction="看多",
        probability=60, trade_action="BUY", risk_status="APPROVED",
        final_trade_decision="ok",
        created_at="2026-10-14 15:43:57.730577",
        updated_at="2026-10-14 15:43:57.730577")
    sealed = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    cal = []
    d = date(2026, 9, 1)
    while len(cal) < 120:
        if d.weekday() < 5:
            cal.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return dsl.build_record(row, json.dumps(rd_dict, ensure_ascii=False),
                            sealed, "2026-10-14", cal)


def test_ledger_horizon_judgement_prefers_term_on_stale_mirror():
    """_horizon_judgement reads <h>_term first — the sealed horizons map
    must carry term judgement values, not a stale alias's."""
    rd = stale_mirror_row()
    # diverge a judgement field: alias direction says SELL-ish, term BUY-ish
    rd["horizons"]["short"]["direction"] = "看空"
    rd["horizons"]["short"]["probability"] = 0.11
    rec = _seal(rd)
    assert rec["horizons"]["short"]["direction"] == "看多"
    assert rec["horizons"]["short"]["probability"] == 0.62
    assert rec["horizons"]["medium"]["direction"] == "看空"


def test_ledger_record_identical_on_consistent_row():
    """Regression: when alias == term (the common case), the sealed record
    is unchanged vs the old alias-first read."""
    rd = stale_mirror_row()
    consistent = copy.deepcopy(rd)
    consistent["horizons"] = {
        h: {k: copy.deepcopy(v) for k, v in consistent[f"{h}_term"].items()
            if k not in ("decision_model_version",
                         "evidence_contract_version",
                         "generated_by_commit_sha")}
        for h in ("short", "medium")
    }
    rec = _seal(consistent)
    assert rec["horizons"]["short"]["direction"] == "看多"
    assert rec["input_pit_status"] == "VERIFIED"


# ---------------------------------------------------------------------------
# read points 2 & 3: shadow_portfolio / trade_execution ledger_* helpers


def _ledger_like_record():
    """A sealed ledger record whose horizons slot is a stale alias copy."""
    rd = stale_mirror_row()
    return {
        "report_id": "r2129",
        "signal_date": "2026-10-02",
        "horizons": rd["horizons"],           # alias (stale) shape
        "short_term": rd["short_term"],       # authoritative twins present
        "medium_term": rd["medium_term"],
        "direction_top": "看多",
        "trade_action_top": "BUY",
        "confidence_top": 0.5,
        "probability_top": 0.5,
        "risk_status_top": "OK",
    }


def test_eval_helpers_read_term_side_on_stale_alias():
    rec = _ledger_like_record()
    # flip alias direction on short → proves the read took the term value
    rec["horizons"]["short"]["direction"] = "看空"
    rec["horizons"]["short"]["probability"] = 0.05
    rec["horizons"]["short"]["confidence"] = 0.05
    rec["horizons"]["short"]["risk_status"] = "REJECTED"
    rec["horizons"]["short"]["trade_action"] = "SELL"

    assert ledger_direction(rec, "short") == "BUY"        # term 看多
    assert ledger_probability(rec, "short") == pytest.approx(0.62)
    assert ledger_confidence(rec, "short") == pytest.approx(0.71)
    assert ledger_risk_status(rec, "short") == "OK"
    assert ledger_trade_action(rec, "short") == "BUY"
    assert ledger_final_action(rec, "short") == "BUY"
    # medium unchanged
    assert ledger_direction(rec, "medium") == "SELL"


def test_eval_helpers_plain_ledger_record_unchanged():
    """Sealed ledger records have no *_term twin — the view is a pass-
    through and reads exactly what was sealed."""
    rec = {
        "horizons": {"short": {"direction": "看多", "probability": 0.6,
                               "trade_action": "BUY"}},
        "direction_top": "看多",
    }
    assert ledger_direction(rec, "short") == "BUY"
    assert ledger_probability(rec, "short") == pytest.approx(0.6)
    assert ledger_trade_action(rec, "short") == "BUY"


# ---------------------------------------------------------------------------
# read point 4: expiry_label_pipeline direction read (production call)


def _weekday_calendar(start: str, n: int) -> list[str]:
    """n consecutive Mon-Fri ISO dates from ``start`` (offline calendar)."""
    d = date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class _FlatMarket:
    """Duck-typed ``MarketData`` stand-in: deterministic bars, no vendor."""

    def __init__(self, calendar: list[str]) -> None:
        self.calendar = calendar

    def _bars(self, start: str, end: str) -> dict:
        idx = {d: i for i, d in enumerate(self.calendar)}
        return {
            d: {"date": d, "open": 10.0, "high": 10.0, "low": 10.0,
                "close": 10.0 + 0.01 * idx[d], "volume": 1.0e6,
                "is_suspended": False}
            for d in self.calendar if start <= d <= end
        }

    def stock_window(self, symbol, start, end):
        return self._bars(start, end)

    def benchmark_window(self, start, end):
        return self._bars(start, end)

    def sw_daily_fetcher(self):
        return lambda code, start, end: {}

    def membership_resolver(self):
        return lambda symbol, as_of: (None, None)


def test_expiry_label_compute_label_row_reads_term_direction():
    """读点 4 直调生产函数: ``compute_label_row`` must resolve the judgement
    direction through the authoritative view, so ``medium_term`` beats the
    stale ``horizons.medium`` alias (579 shape).

    Anti-regression: reverting ``expiry_label_pipeline.py``'s view call to a
    direct ``record["horizons"]`` read flips ``direction_raw`` to the alias
    (看多) and this test goes red (DAV-2258 M4 mutation — the old test
    re-wrote the view inline and never touched production code).
    """
    ep = _load("expiry_label_pipeline", PHASE2 / "expiry_label_pipeline.py")
    rec = _ledger_like_record()
    rec["symbol"] = "600519.SH"
    # The alias is stale (579 shape) and points at the opposite side; the
    # top-level direction_top copy says 看多 as well, so 看空 can only come
    # from the authoritative ``medium_term`` slice.
    rec["horizons"]["medium"]["direction"] = "看多"
    rec["horizons"]["medium"]["probability"] = 0.99
    cal = _weekday_calendar("2026-09-01", 120)

    row = ep.compute_label_row(rec, horizon="medium", calendar=cal,
                               as_of=cal[-1], as_of_market_closed=True,
                               market=_FlatMarket(cal))

    assert row is not None                      # reached the judgement read
    assert row["outcome_status"] == OutcomeStatus.EVALUATED_OK.value
    assert rec["medium_term"]["direction"] == "看空"   # term side
    assert row["direction_raw"] == "看空"       # not the alias / top copy
    assert row["direction_bucket"] == "看空"    # DAV-1498 SELL-side bucket
    assert row["direction_bucket"] == ep._direction_bucket(row["direction_raw"])


# ---------------------------------------------------------------------------
# read point 5: weekly_report medium split (production report, parsed)


def _weekly_trade_dates(n: int = 60) -> list[date]:
    """Minimal trade calendar so the synthetic record matures at T+40."""
    out: list[date] = []
    d = date(2026, 10, 2)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _md_row(report: str, first_cell: str) -> list[str]:
    """Cells of the first markdown table row whose first cell matches.

    §1/§3 headers are fixed literals that already contain 「看空」/SELL, so
    assertions must be made against parsed *data* rows, never against
    ``x in report`` (DAV-2258 🟡-2).
    """
    for line in report.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == first_cell:
            return cells
    raise AssertionError(f"no table row starting with {first_cell!r}")


def _weekly_record(*, symbol: str = "600519.SH") -> dict:
    rec = _ledger_like_record()
    rec["symbol"] = symbol
    rec["timing_class"] = "F0"
    rec["input_pit_status"] = "VERIFIED"
    rec["backfilled"] = False
    return rec


def test_weekly_report_medium_split_uses_term():
    """读点 5: ``build_report`` §1 direction distribution must be built from
    the authoritative ``medium_term`` judgement — term 看空 buckets into
    看空=1, while the stale alias 看多 would render 看多=1.

    Anti-regression: the previous assertion (``"看空" in out or ...``) was
    satisfied by the fixed §1 header row, so reverting ``weekly_report.py`` to
    ``compat_view``/a direct alias read stayed green (DAV-2258 🟡-2 / M5).
    """
    wr = _load("weekly_report", PHASE2 / "weekly_report.py")
    rec = _weekly_record()
    rec["horizons"]["medium"]["direction"] = "看多"   # stale alias
    rec["horizons"]["medium"]["probability"] = 0.99

    out = wr.build_report(
        records=[rec], risk_status={}, labels={}, label_field="return_pct",
        relative_caliber="exec", bench_bars={}, bench_symbols=(),
        trade_dates=_weekly_trade_dates(), as_of=date(2026, 12, 30),
        min_cross_n=1, min_total_n=1, input_digest="x")

    assert isinstance(out, str)
    # §1 data row: | 版本队列 | 总数 | 看多 | 看空 | 中性 | 无结论 |
    assert _md_row(out, "unversioned") == [
        "unversioned", "1", "0", "1", "0", "0"]


def test_weekly_report_medium_probability_is_term_side():
    """读点 5, second read-out: §3 consumes the medium *probability*, which
    must be the authoritative ``medium_term`` value (0.38), not the stale
    alias copy (0.99).

    Two matured records whose probabilities order one way under term
    (0.38 < 0.75) and the opposite way under the alias (0.99 > 0.75) make the
    rendered §3 spread flip sign under M5-style mutation
    (+1.0000 → -1.0000) — a value-level lock, not a header match.
    """
    wr = _load("weekly_report", PHASE2 / "weekly_report.py")
    stale = _weekly_record(symbol="600519.SH")
    stale["horizons"]["medium"]["probability"] = 0.99      # stale alias only
    clean = _weekly_record(symbol="000001.SZ")
    clean["medium_term"]["probability"] = 0.75             # authoritative
    clean["horizons"]["medium"]["probability"] = 0.75
    labels = {("600519.SH", "2026-10-02"): {"return_pct": 1.0},
              ("000001.SZ", "2026-10-02"): {"return_pct": 2.0}}

    out = wr.build_report(
        records=[stale, clean], risk_status={}, labels=labels,
        label_field="return_pct", relative_caliber="exec", bench_bars={},
        bench_symbols=(), trade_dates=_weekly_trade_dates(),
        as_of=date(2026, 12, 30), min_cross_n=1, min_total_n=1,
        input_digest="x")

    # §3 data row: | 日期 | n | 状态 | IC | spread |
    row = _md_row(out, "2026-10-02")
    assert row[1] == "2" and row[2] == "ok"
    # term probs (0.38, 0.75) rank 600519 → 000001 → spread +1.0
    assert row[3] == "1.0000"      # IC
    assert row[4] == "1.0000"      # top-minus-bottom tercile spread


# ---------------------------------------------------------------------------
# 2128 未扫完并入: 多个 as_of 场景 + input_pit 时点判定细节


def _t5_unit(*, horizon, status, hit, t5_date="2026-10-13",
             trade_date="2026-10-02", is_due=None, evaluated=None):
    """One horizon unit carrying T+5 markers at unit level."""
    u = {
        "symbol": "600519.SH",
        "horizon": horizon,
        "trade_date": trade_date,
        "status": "completed",
        "shadow_credit_metrics": {
            "t_plus_5_status": status,
            "t_plus_5_direction_hit": hit,
            "t_plus_5_date": t5_date,
        },
    }
    if status is not None:
        u["t_plus_5_status"] = status
    if is_due is not None:
        u["is_t_plus_5_due"] = is_due
    if evaluated is not None:
        u["t_plus_5_evaluated"] = evaluated
    return u


_D4_THRESH = {"min_sample_count": 1, "min_unique_symbols": 1,
              "min_industries": 1, "min_side_samples": 1,
              "min_verified_claims_per_side": 0,
              "min_calendar_days": 0, "min_trading_days": 0,
              "min_t_plus_5_completeness": 0.95,
              "min_side_balance_ratio": 0.0, "max_side_balance_ratio": 1.0,
              "max_side_count_diff": 999, "max_single_symbol_ratio": 1.0}


@pytest.mark.parametrize("as_of,expected_due,expected_completed", [
    # before the stamped t5_date → not yet due
    ("2026-10-10", 0, 0),
    # on the t5_date → due
    ("2026-10-13", 2, 0),
    # after → due
    ("2026-11-01", 2, 0),
])
def test_d4_as_of_sweep_pending_vs_due(as_of, expected_due,
                                       expected_completed):
    """多 as_of 场景: the same units flip due-ness across the T+5 boundary —
    a stale alias must never leak into that flip. Units carry no explicit
    status so the as_of vs t5_date comparison drives the verdict."""
    units = [
        # hit=None for both — due-ness decided purely by t5_date <= as_of
        _t5_unit(horizon="short", status=None, hit=None),
        _t5_unit(horizon="medium", status=None, hit=None),
    ]
    res = evaluate_h1b_system_gates(units, as_of=as_of, thresholds=_D4_THRESH)
    d4 = res["matrix"]["dimension_t5"]["details"]
    assert d4["due_count"] == expected_due
    assert d4["completed_count"] == expected_completed


def test_d4_stale_term_status_pending_never_counts_completed():
    """时点细节: a stale mirror row whose TERM slice says pending_due is
    excluded from the denominator even after the t5_date — the status
    string wins over date arithmetic."""
    rd = stale_mirror_row()
    for hz in ("short", "medium"):
        rd[f"{hz}_term"]["shadow_credit_metrics"][
            "t_plus_5_status"] = T_PLUS_5_STATUS_PENDING_DUE
        rd[f"{hz}_term"]["shadow_credit_metrics"][
            "t_plus_5_direction_hit"] = None
    rep = {"id": "r", "symbol": "600519.SH", "trade_date": "2026-10-02",
           "status": "completed", "result_data": rd}
    units = split_report_into_units(rep)
    res = evaluate_h1b_system_gates(units, as_of="2026-11-01",
                            thresholds=_D4_THRESH)
    d4 = res["matrix"]["dimension_t5"]["details"]
    assert d4["due_count"] == 0
    assert res["matrix"]["dimension_t5"]["passed"] is False


def test_input_pit_uses_term_mdc_when_top_mdc_stale():
    """input_pit 细节: a stale-mirror row whose top-level mdc is *absent*
    (canonical-style) must be judged per-horizon from ``<h>_term`` mdcs —
    the authoritative view keeps that path intact."""
    rd = stale_mirror_row()
    rd.pop("market_data_context", None)  # canonical-ish: no top mdc
    view = result_data_authoritative_view(rd)
    status, ev, mdc = dsl._classify_input_pit(view)
    assert status == "VERIFIED"
    assert ev["mdc_shape"] == "per_horizon"
    assert set(ev["horizons"]) == {"short", "medium"}
    assert mdc == {
        "short": rd["short_term"]["market_data_context"],
        "medium": rd["medium_term"]["market_data_context"],
    }


def test_input_pit_top_mdc_physically_present_still_wins():
    """时点细节 (总控 10-15 裁①): a physically present top-level mdc is
    judged as before — the authoritative view does NOT replace it on
    legacy rows (it only remaps ``horizons``)."""
    rd = stale_mirror_row()
    # make one term slice look-ahead FAILED but keep top mdc verified —
    # the physical top mdc must still drive the verdict (read position
    # unchanged by DAV-2129).
    sp = rd["short_term"]["market_data_context"]["source_provenance"]
    sp["stock_data"]["actual_as_of"] = "2026-10-20"  # look-ahead
    status, ev, _ = dsl._classify_input_pit(
        result_data_authoritative_view(rd))
    assert status == "VERIFIED"          # top mdc read, term ignored
    assert ev["mdc_shape"] == "top_level"


def test_input_pit_reattest_path_matches_build_record():
    """The reattest sidecar re-runs the same fixed classifier — verdicts
    must agree between seal-time and reattest-time on a stale row."""
    rd = stale_mirror_row()
    rec = _seal(rd)
    entry = dsl.reattest_entries_for_row(
        _FakeRow(id="r2129"), json.dumps(rd, ensure_ascii=False))
    assert entry["input_pit_status"] == rec["input_pit_status"]
    assert entry["mdc_sha256"] == \
        rec["input_pit_evidence"]["market_data_context_sha256"]


class _FakeRow(dict):
    pass


# ---------------------------------------------------------------------------
# read point 6: shadow_credit D4 — T+5 must come from the unit/term, never
# the stale top-level or alias copy


def _dual_report_with_stale_copies(short_hit=True, medium_hit=None):
    """A packaged report row: authoritative *_term scm carries T+5, stale
    horizons alias + top-level scm have T+5 null (579 shape)."""
    rd = stale_mirror_row(short_hit=short_hit, medium_hit=medium_hit)
    return {
        "id": "rep-2129",
        "symbol": "600519.SH",
        "trade_date": "2026-10-02",
        "status": "completed",
        "result_data": rd,
    }


def test_d4_t5_reads_term_scm_not_stale_top_copy():
    """The streaming/module D4 path must not let the stale top-level
    shadow_credit_metrics (T+5 null) override the term slice values."""
    report = _dual_report_with_stale_copies(short_hit=True)
    units = split_report_into_units(report)
    assert len(units) == 2
    for u in units:
        hz = u["horizon"]
        term = report["result_data"][f"{hz}_term"]
        # unit scm came from the term slice (authoritative)
        assert (u["shadow_credit_metrics"]["t_plus_5_direction_hit"]
                == term["shadow_credit_metrics"]["t_plus_5_direction_hit"])

    # Feed units through the gate: stale top-level scm must not matter.
    # as_of after 2026-10-13 (t_plus_5_date) → both units due.
    res = evaluate_h1b_system_gates(
        units,
        as_of="2026-11-01",
        thresholds={"min_sample_count": 1, "min_unique_symbols": 1,
                    "min_industries": 1, "min_side_samples": 1,
                    "min_verified_claims_per_side": 0,
                    "min_calendar_days": 0, "min_trading_days": 0,
                    "min_t_plus_5_completeness": 0.95,
                    "min_side_balance_ratio": 0.0,
                    "max_side_balance_ratio": 1.0,
                    "max_side_count_diff": 999,
                    "max_single_symbol_ratio": 1.0},
    )
    d4 = res["matrix"]["dimension_t5"]["details"]
    # short hit=True, medium hit=False → due=2, completed=1
    assert d4["due_count"] == 2
    assert d4["completed_count"] == 1
    assert d4["completeness_rate"] == pytest.approx(0.5)

def test_d4_counterfactual_alias_first_would_collapse():
    """Regression pin (documents the 93.2%→11.2% collapse 2128 measured):
    a sample that carries only the STALE copies (unit scm absent, horizon
    term scm nulled) must read no T+5 hit — while the same sample with an
    intact ``<h>_term`` slice reads the authoritative value."""
    report = _dual_report_with_stale_copies(short_hit=True)
    units = split_report_into_units(report)

    # (a) term slice nulled on res_data + unit scm absent → only stale
    # copies remain; D4 must see completed=0 (the collapse case).
    stale_only = []
    for u in units:
        u2 = dict(u)
        u2.pop("shadow_credit_metrics", None)
        u2.pop("t_plus_5_status", None)
        u2.pop("is_t_plus_5_due", None)
        u2["result_data"] = dict(report["result_data"])
        hz = u2["horizon"]
        rd = dict(u2["result_data"])
        rd[f"{hz}_term"] = dict(rd[f"{hz}_term"])
        rd[f"{hz}_term"]["shadow_credit_metrics"] = {
            k: None for k in T5_KEYS}
        u2["result_data"] = rd
        stale_only.append(u2)
    res = evaluate_h1b_system_gates(
        stale_only,
        as_of="2026-11-01",
        thresholds={"min_sample_count": 1, "min_unique_symbols": 1,
                    "min_industries": 1, "min_side_samples": 1,
                    "min_verified_claims_per_side": 0,
                    "min_calendar_days": 0, "min_trading_days": 0,
                    "min_t_plus_5_completeness": 0.95,
                    "min_side_balance_ratio": 0.0,
                    "max_side_balance_ratio": 1.0,
                    "max_side_count_diff": 999,
                    "max_single_symbol_ratio": 1.0},
    )
    assert res["matrix"]["dimension_t5"]["details"]["completed_count"] == 0

    # (b) same samples but the unit carries NO scm while the horizon's
    # ``<h>_term`` scm is intact → the term slice wins over the stale
    # top-level copy (hit read from authoritative copy).
    term_fed = []
    for u in units:
        u2 = dict(u)
        u2.pop("shadow_credit_metrics", None)
        u2.pop("t_plus_5_status", None)
        u2.pop("is_t_plus_5_due", None)
        u2["result_data"] = report["result_data"]
        term_fed.append(u2)
    res2 = evaluate_h1b_system_gates(
        term_fed,
        as_of="2026-11-01",
        thresholds={"min_sample_count": 1, "min_unique_symbols": 1,
                    "min_industries": 1, "min_side_samples": 1,
                    "min_verified_claims_per_side": 0,
                    "min_calendar_days": 0, "min_trading_days": 0,
                    "min_t_plus_5_completeness": 0.95,
                    "min_side_balance_ratio": 0.0,
                    "max_side_balance_ratio": 1.0,
                    "max_side_count_diff": 999,
                    "max_single_symbol_ratio": 1.0},
    )
    d4 = res2["matrix"]["dimension_t5"]["details"]
    # short term hit=True, medium hit=None → due=2, completed=1
    assert d4["due_count"] == 2
    assert d4["completed_count"] == 1


# ---------------------------------------------------------------------------
# read point 7 (card-external twin): verify_h1b_gates.py streaming D4
#
# ``_stream_run_gate_eval`` mirrors the module D4 (read point 6). The card
# lists six read points; this seventh rewrite ships in the same candidate and
# had zero coverage (DAV-2258 🟡-3 / M7). Both the scm resolution and the
# term-level T+5 gates are pinned here.


def _stream_d4_details(sample: dict) -> dict:
    vhg = _load("verify_h1b_gates", REPO / "scripts" / "verify_h1b_gates.py")
    res = vhg._stream_run_gate_eval(
        [sample], as_of="2026-11-01", cohort=None, excluded_counts={},
        pipeline_ledger={},
        cohort_meta={"canonical_key": None, "commit_shas": []})
    return res["matrix"]["dimension_t5"]["details"]


def _stream_sample(horizon: str = "short") -> dict:
    """A 579-shape projected sample: only the ``<h>_term`` slice carries
    T+5, while the top-level ``shadow_credit_metrics`` is the third (stale,
    all-null) copy. The sample itself carries no unit-level scm, so the
    fallback chain is the only thing that can resolve the hit."""
    return {
        "id": "rep-2266", "symbol": "600519.SH",
        "trade_date": "2026-10-02", "status": "completed",
        "horizon": horizon, "result_data": stale_mirror_row(),
        "is_t_plus_5_due": True,
    }


def test_stream_gate_d4_t5_reads_term_slice_not_stale_top_copy():
    """The streaming D4 must read T+5 through the sample horizon's
    ``<h>_term`` slice — the stale top-level scm (T+5 all null) must never
    win. Anti-regression: deleting the ``term_slice`` fallback in
    ``_stream_run_gate_eval`` drops completed_count 1 → 0."""
    sample = _stream_sample()
    term_scm = sample["result_data"]["short_term"]["shadow_credit_metrics"]
    top_scm = sample["result_data"]["shadow_credit_metrics"]
    assert term_scm["t_plus_5_direction_hit"] is True   # authoritative
    assert top_scm["t_plus_5_direction_hit"] is None    # stale copy

    d4 = _stream_d4_details(sample)
    assert d4["due_count"] == 1
    assert d4["completed_count"] == 1
    assert d4["completeness_rate"] == pytest.approx(1.0)


@pytest.mark.parametrize("kind", ["in_flight", "pending_due", "suspended"])
def test_stream_gate_d4_term_slice_t5_gates_leave_denominator(kind):
    """The term slice's own T+5 gates (in-flight / pending / suspension)
    must keep the sample out of the due denominator even though the stale
    top-level copy and ``is_t_plus_5_due=True`` say "due". Anti-regression:
    without the term-slice read every case counts due=1 instead of 0."""
    sample = _stream_sample()
    term = sample["result_data"]["short_term"]
    if kind == "in_flight":
        term["is_in_flight"] = True
    elif kind == "pending_due":
        term["shadow_credit_metrics"]["t_plus_5_status"] = \
            T_PLUS_5_STATUS_PENDING_DUE
    else:
        term["is_suspended"] = True

    d4 = _stream_d4_details(sample)
    assert d4["due_count"] == 0
    assert d4["completed_count"] == 0
