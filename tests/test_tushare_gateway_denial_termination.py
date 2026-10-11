"""DAV-1818: Tushare gateway denial must be terminal — no retry, no bisection.

Background (DAV-1812 diagnosis): with Tushare credentials present on the host,
``DataCollector.collect()`` fans out to two gateways and, before this fix,
amplified a single denial into **484 real POST attempts** (468 news + 16
industry-linkage) plus ~180s of budget-bound spinning:

- ``tushare_global_news.TushareNewsClient`` — a 7-day window x 4 sources,
  each unresponsive segment bisected down to 30-minute slices
  (``_fetch_segment``) with ``_RETRY_ATTEMPTS = 5`` per request;
- ``industry_linkage_provider._query_tushare_api`` — financial statements
  (``_query_financial_api`` retries twice) and global indices, consumed by
  ``tushare_provider`` and ``sw_industry`` via by-value imports.

Contracts verified here (all offline, zero real traffic):

1. ``TushareNewsClient.query`` — a guardrail denial is terminal: exactly one
   POST attempt, not five.
2. ``_fetch_segment`` — denial aborts the bisection immediately instead of
   fanning out into 2**depth sub-segments.
3. ``_query_tushare_api`` — denial is categorised ``network_denied``, which is
   deliberately **not** a member of the retryable set, so downstream retry
   loops terminate on the first attempt.
4. ``_query_financial_api`` — denial must not consume its retry budget
   (``_FINANCIAL_MAX_ATTEMPTS = 2``).
5. ``get_global_news`` — denial propagates and releases the call-level
   single-flight slot so concurrent callers fail fast instead of waiting ~200s.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest

from tests.conftest import OfflineTestGuardrailError


# TEST-NET-1 (RFC 5737): guaranteed non-routable, and non-local so the offline
# guardrail denies it at the socket/audit layer (repo convention).
EXTERNAL_TEST_URL = "http://192.0.2.1:80/"


def _counting_post(module, counter):
    """Wrap ``module.requests.post`` so attempts are counted before denial."""
    real_post = module.requests.post

    def _post(*args, **kwargs):
        counter["n"] += 1
        return real_post(*args, **kwargs)

    return _post


def tp_retryable_categories():
    """Retryable transport categories of the financial-report retry loop."""
    from tradingagents.dataflows.providers import tushare_provider as tp

    return tp._FINANCIAL_RETRYABLE_ERRORS


# ==============================================================================
# 1. News gateway: denial is terminal (no 5x retry)
# ==============================================================================

class TestNewsClientDenialIsTerminal:
    """``TushareNewsClient.query`` must not burn its retry budget on a denial."""

    def test_query_attempts_once_on_guardrail_denial(self):
        """Real guardrail denial → exactly 1 POST attempt (pre-fix: 5)."""
        from tradingagents.dataflows import tushare_global_news as tgn

        assert tgn.TushareNewsClient._RETRY_ATTEMPTS == 5, "contract drift"

        counter = {"n": 0}
        client = tgn.TushareNewsClient(
            api_url=EXTERNAL_TEST_URL, token="synthetic-token"
        )

        with patch.object(tgn.requests, "post", _counting_post(tgn, counter)):
            with pytest.raises(OfflineTestGuardrailError):
                client.query(
                    "news",
                    {"src": "cls", "limit": 800},
                    "datetime,title,channels",
                )

        assert counter["n"] == 1, (
            f"denial must be terminal, but {counter['n']} POST attempts were made"
        )

    def test_query_once_reraises_denial_instead_of_categorising_it(self):
        """``_query_once`` must re-raise rather than return a retryable category."""
        from tradingagents.dataflows import tushare_global_news as tgn

        client = tgn.TushareNewsClient(
            api_url=EXTERNAL_TEST_URL, token="synthetic-token"
        )

        def _deny(*_args, **_kwargs):
            raise OfflineTestGuardrailError("denied by offline guardrail")

        with patch.object(tgn.requests, "post", _deny):
            with pytest.raises(OfflineTestGuardrailError):
                client._query_once("news", {"src": "cls"}, "datetime")


# ==============================================================================
# 2. News gateway: denial aborts segment bisection
# ==============================================================================

class TestSegmentBisectionTerminatesOnDenial:
    """``_fetch_segment`` must not subdivide after a denial."""

    def test_denial_aborts_bisection_after_single_query(self):
        """One denial stops the recursion; no 30-minute slice fan-out."""
        from tradingagents.dataflows import tushare_global_news as tgn

        spec = next(s for s in tgn._NEWS_SOURCE_SPECS if s["key"] == "news:cls")
        calls = {"n": 0}

        class DenyingClient(tgn.TushareNewsClient):
            def __init__(self):
                super().__init__(api_url=EXTERNAL_TEST_URL, token="synthetic-token")

            def query(self, api_name, params=None, fields=None, deadline=None):
                calls["n"] += 1
                raise OfflineTestGuardrailError("denied by offline guardrail")

        reports = []
        with pytest.raises(OfflineTestGuardrailError):
            tgn._fetch_segment(
                DenyingClient(),
                spec,
                datetime(2026, 8, 14, 0, 0, 0),
                datetime(2026, 8, 14, 23, 59, 59),
                reports,
            )

        assert calls["n"] == 1, (
            f"bisection must stop at the first denial, got {calls['n']} queries"
        )
        # Only the root segment was opened; no child segments were created.
        assert len(reports) == 1

    def test_fetch_news_pool_propagates_denial(self):
        """``fetch_news_pool`` must surface the denial, not a truncated-pool error."""
        from tradingagents.dataflows import tushare_global_news as tgn

        class DenyingClient(tgn.TushareNewsClient):
            def __init__(self):
                super().__init__(api_url=EXTERNAL_TEST_URL, token="synthetic-token")

            def query(self, api_name, params=None, fields=None, deadline=None):
                raise OfflineTestGuardrailError("denied by offline guardrail")

        with pytest.raises(OfflineTestGuardrailError):
            tgn.fetch_news_pool(
                DenyingClient(), date(2026, 8, 14), date(2026, 8, 14)
            )


# ==============================================================================
# 3. Shared Tushare gateway client: denial is not downgraded
# ==============================================================================

class TestSharedGatewayDenialIsNotDowngraded:
    """``_query_tushare_api`` denial must be non-retryable and clearly categorised.

    The function keeps its documented ``(df, category, note)`` return contract
    (raising here would turn one denied indicator into a whole-industry
    failure), but the category must NOT be one of the retryable transport
    classes, or callers such as ``_query_financial_api`` retry it.
    """

    def test_denial_returns_non_retryable_category(self, monkeypatch):
        from tradingagents.dataflows.providers import industry_linkage_provider as ilp

        monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-token")

        def _deny(*_args, **_kwargs):
            raise OfflineTestGuardrailError("denied by offline guardrail")

        with patch.object(ilp.requests, "post", _deny):
            df, category, note = ilp._query_tushare_api(
                api_name="index_global", ts_code="SPX", as_of="2026-08-14"
            )

        assert df is None
        assert category == "network_denied"
        assert category not in tp_retryable_categories()
        assert "denied by offline guardrail" not in note, "must not echo internals"

    def test_denial_category_is_not_network_error(self, monkeypatch):
        """Regression guard: the pre-fix code returned the retryable ``network_error``."""
        from tradingagents.dataflows.providers import industry_linkage_provider as ilp

        monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-token")

        def _deny(*_args, **_kwargs):
            raise OfflineTestGuardrailError("denied by offline guardrail")

        with patch.object(ilp.requests, "post", _deny):
            _df, category, _note = ilp._query_tushare_api(
                api_name="index_global", ts_code="SPX", as_of="2026-08-14"
            )

        assert category != "network_error", (
            "denial downgraded to a retryable category → retry amplification"
        )
        assert category != "timeout"

    def test_denied_indicator_degrades_to_typed_gap(self, monkeypatch):
        """A denied indicator stays a typed per-indicator gap (no industry failure)."""
        from tradingagents.dataflows.providers import industry_linkage_provider as ilp

        monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-token")

        def _deny(*_args, **_kwargs):
            raise OfflineTestGuardrailError("denied by offline guardrail")

        provider = ilp.IndustryLinkageProvider()
        indicator = ilp.IndustryLinkageIndicator(
            name="标普500指数", source="tushare", symbol="SPX"
        )

        # Deny at the transport, so the categorisation in ``_query_tushare_api``
        # is what the provider actually observes.
        with patch.object(ilp.requests, "post", _deny):
            result = provider._fetch_indicator(indicator, as_of="2026-08-14")

        # Contract preserved: one denied indicator is a typed gap, not a crash.
        assert result["status"] == "unavailable"
        assert result["category"] == "network_denied"
        assert result["confidence"] == "低（网络策略拒绝）"
        assert result["current_value"] is None


# ==============================================================================
# 4. Denial must not consume a retry budget
# ==============================================================================

class TestDenialDoesNotConsumeRetryBudget:
    """Retry loops must observe the terminal denial, not a retryable category."""

    def test_financial_api_does_not_retry_denial(self, monkeypatch):
        """``_query_financial_api`` retries transient errors; denial must skip it."""
        from tradingagents.dataflows.providers import industry_linkage_provider as ilp
        from tradingagents.dataflows.providers import tushare_provider as tp

        monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-token")
        assert tp._FINANCIAL_MAX_ATTEMPTS == 2, "contract drift"
        assert "network_denied" not in tp._FINANCIAL_RETRYABLE_ERRORS, "contract drift"
        counter = {"n": 0}

        with patch.object(ilp.requests, "post", _counting_post(ilp, counter)):
            _df, category, _note = tp._query_financial_api("income", "600519.SH")

        assert category == "network_denied"
        assert counter["n"] == 1, (
            f"denial must not consume the retry budget, got {counter['n']} attempts"
        )

    def test_denial_is_terminal_at_the_shared_gateway(self, monkeypatch):
        """The single attempt happens in ``_query_tushare_api``; category is final."""
        from tradingagents.dataflows.providers import industry_linkage_provider as ilp

        monkeypatch.setenv("TUSHARE_TOKEN", "synthetic-token")
        counter = {"n": 0}

        with patch.object(ilp.requests, "post", _counting_post(ilp, counter)):
            _df, category, _note = ilp._query_tushare_api(
                api_name="index_global", ts_code="SPX", as_of="2026-08-14"
            )

        assert counter["n"] == 1, f"expected 1 HTTP attempt, got {counter['n']}"
        assert category == "network_denied"


# ==============================================================================
# 5. Entry point: denial propagates and releases the single-flight slot
# ==============================================================================

class TestGlobalNewsEntryDenialAndSingleFlight:
    """``get_global_news`` must fail fast and never strand concurrent callers."""

    def test_denial_propagates_and_releases_inflight_slot(self, tmp_path):
        from tradingagents.dataflows import tushare_global_news as tgn

        class DenyingClient(tgn.TushareNewsClient):
            def __init__(self):
                super().__init__(api_url=EXTERNAL_TEST_URL, token="synthetic-token")

            def query(self, api_name, params=None, fields=None, deadline=None):
                raise OfflineTestGuardrailError("denied by offline guardrail")

        cache = tgn.TushareNewsCache(str(tmp_path))
        with pytest.raises(OfflineTestGuardrailError):
            tgn.get_global_news(
                "2026-08-14", 7, 30, client=DenyingClient(), cache=cache
            )

        # The single-flight entry must be released, otherwise a later call for
        # the same key would wait ~200s and return a soft timeout error.
        assert tgn._CALL_INFLIGHT == {}, (
            f"single-flight slot leaked: {tgn._CALL_INFLIGHT!r}"
        )

    def test_second_call_after_denial_is_not_blocked_by_stale_inflight(self, tmp_path):
        from tradingagents.dataflows import tushare_global_news as tgn

        class DenyingClient(tgn.TushareNewsClient):
            def __init__(self):
                super().__init__(api_url=EXTERNAL_TEST_URL, token="synthetic-token")

            def query(self, api_name, params=None, fields=None, deadline=None):
                raise OfflineTestGuardrailError("denied by offline guardrail")

        cache = tgn.TushareNewsCache(str(tmp_path))
        for _ in range(2):
            with pytest.raises(OfflineTestGuardrailError):
                tgn.get_global_news(
                    "2026-08-14", 7, 30, client=DenyingClient(), cache=cache
                )


# ==============================================================================
# 6. The opt-in stub fixture denies both gateway seams
# ==============================================================================

class TestOfflineTushareGatewayStubFixture:
    """``offline_tushare_gateway_stub`` must close both gateway seams."""

    def test_stub_denies_news_client(self, offline_tushare_gateway_stub):
        from tradingagents.dataflows import tushare_global_news as tgn

        client = tgn.TushareNewsClient(
            api_url=EXTERNAL_TEST_URL, token="synthetic-token"
        )
        with pytest.raises(OfflineTestGuardrailError):
            client.query("news", {"src": "cls"}, "datetime")

    def test_stub_denies_industry_linkage_gateway(self, offline_tushare_gateway_stub):
        from tradingagents.dataflows.providers import industry_linkage_provider as ilp

        with pytest.raises(OfflineTestGuardrailError):
            ilp._query_tushare_api(api_name="shibor", as_of="2026-08-14")

    def test_stub_denies_tushare_provider_and_sw_industry_holders(
        self, offline_tushare_gateway_stub
    ):
        """Both modules import ``_query_tushare_api`` by value; each must be patched."""
        from tradingagents.dataflows.providers import tushare_provider as tp
        from tradingagents.dataflows import sw_industry as swi

        with pytest.raises(OfflineTestGuardrailError):
            tp._query_tushare_api(api_name="index_global", ts_code="SPX")

        with pytest.raises(OfflineTestGuardrailError):
            swi._query_tushare_api(
                "index_classify", fields="index_code,industry_name,level"
            )