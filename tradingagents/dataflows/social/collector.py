"""TradingAgents social data collector and runtime mode coordinator (Task 7 / §7 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 7, §5.5, §7, §8, D-008
- DECISIONS.md D-008, D-009, D-010

Core Responsibilities:
1. Configuration & Mode Resolution:
   - Defaults: TA_SOCIAL_MODE=disabled, TA_SOCIAL_PROVIDER=archive_sqlite, TA_SOCIAL_FETCH_TIMEOUT=5.
   - Strict adherence to §7 table keys and default values.
2. Mode Enforcement:
   - disabled: return status=not_applicable, reason_codes=['social_not_applicable'], direction_allowed=False, never open DB or call provider.
   - shadow / active: validate archive_db is non-empty absolute path and exists; missing/relative path -> failed + social_archive_missing.
   - canary symbols: in active mode with canary list, non-canary symbol falls back to non-active (not_applicable / direction_allowed=False).
   - non-A-share symbol: return status=not_applicable without querying DB.
3. Execution & Context Assembly:
   - Invoke SocialDataProvider.fetch_records with configured timeout.
   - Invoke SocialSentimentAggregator.aggregate_sentiment_bundle.
   - Assemble SocialDataContext with mode, bundle, direction_allowed, reason_codes, source_provenance, and data_failure_ledger (§5.5).
4. Safe Logging (§C6):
   - Only log symbol, as_of, mode, status, record counts, and elapsed time.
   - Never log sensitive user IDs, tokens, cookies, or full content texts.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Union

from tradingagents.dataflows.social.aggregator import (
    SocialSentimentAggregator,
    aggregate_sentiment_bundle,
)
from tradingagents.dataflows.social.contracts import (
    REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED,
    REASON_SOCIAL_ARCHIVE_LOCKED,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_FUTURE_AS_OF,
    REASON_SOCIAL_INSUFFICIENT_COVERAGE,
    REASON_SOCIAL_INVALID_AS_OF,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    REASON_SOCIAL_NOT_APPLICABLE,
    REASON_SOCIAL_PLATFORM_PARTIAL,
    REASON_SOCIAL_SCHEMA_MISMATCH,
    SentimentBundleV1,
    SocialDataContext,
    SocialStatus,
    create_default_social_data_context,
    create_empty_sentiment_bundle,
)
from tradingagents.dataflows.social.entity_resolver import normalize_stock_code
from tradingagents.dataflows.social.provider import (
    SocialArchiveProvider,
    SocialDataProvider,
    SocialFetchResult,
)
from tradingagents.dataflows.social.registry import (
    SocialDataProviderRegistry,
    build_default_social_registry,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Ledger Builder Helper (§5.5)
# ============================================================================

def build_social_failure_ledger(
    status: str,
    reason_codes: Sequence[str],
) -> List[Dict[str, Any]]:
    """Map social outcomes to standard data_failure_ledger entries per §5.5.

    Rules (§5.5):
    - available / partial / empty / not_applicable: do NOT produce failure ledger entries.
    - refused (invalid / future as_of / no snapshot): structural failure entry.
    - failed (archive missing / schema mismatch): operational failure entry.
    - timeout (archive locked): operational failure entry.
    """
    if status in (
        SocialStatus.AVAILABLE.value,
        SocialStatus.PARTIAL.value,
        SocialStatus.EMPTY.value,
        SocialStatus.NOT_APPLICABLE.value,
    ):
        return []

    entries: List[Dict[str, Any]] = []

    if status == SocialStatus.REFUSED.value:
        codes = list(reason_codes) if reason_codes else ["social_refused"]
        for code in codes:
            if code in (REASON_SOCIAL_INVALID_AS_OF, REASON_SOCIAL_FUTURE_AS_OF):
                entries.append({
                    "source": "social_archive",
                    "status": SocialStatus.REFUSED.value,
                    "reason_code": code,
                    "reason": "日期非法或超出交易日历上限",
                    "gap": f"【数据获取失败】社交归档：{code}",
                    "gap_class": "structural",
                })
            elif code in (
                REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
                REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED,
            ):
                entries.append({
                    "source": "social_archive",
                    "status": SocialStatus.REFUSED.value,
                    "reason_code": code,
                    "reason": "无历史观测快照或快照在截止时间之后",
                    "gap": f"【数据获取失败】社交归档：{code}",
                    "gap_class": "structural",
                })
        if not entries:
            code = codes[0]
            entries.append({
                "source": "social_archive",
                "status": SocialStatus.REFUSED.value,
                "reason_code": code,
                "reason": "社交归档请求被拒绝",
                "gap": f"【数据获取失败】社交归档：{code}",
                "gap_class": "structural",
            })

    elif status == SocialStatus.TIMEOUT.value:
        code = reason_codes[0] if reason_codes else REASON_SOCIAL_ARCHIVE_LOCKED
        entries.append({
            "source": "social_archive",
            "status": SocialStatus.TIMEOUT.value,
            "reason_code": code,
            "reason": "社交归档数据库锁等待超时",
            "gap": f"【数据获取失败】社交归档：{code}",
            "gap_class": "operational",
        })

    elif status == SocialStatus.FAILED.value:
        code = reason_codes[0] if reason_codes else REASON_SOCIAL_ARCHIVE_MISSING
        reason_text = "社交归档数据库缺失或不可用"
        if code == REASON_SOCIAL_SCHEMA_MISMATCH:
            reason_text = "社交归档数据库Schema版本不匹配"
        entries.append({
            "source": "social_archive",
            "status": SocialStatus.FAILED.value,
            "reason_code": code,
            "reason": reason_text,
            "gap": f"【数据获取失败】社交归档：{code}",
            "gap_class": "operational",
        })

    return entries


# ============================================================================
# SocialDataCollector Implementation
# ============================================================================

class SocialDataCollector:
    """Independent Social Data Collector (Task 7 / §7)."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        registry: Optional[SocialDataProviderRegistry] = None,
        custom_provider: Optional[SocialDataProvider] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize SocialDataCollector with config dict, kwargs, or environment variables."""
        cfg: Dict[str, Any] = {}
        if config:
            if "social" in config and isinstance(config["social"], dict):
                cfg.update(config["social"])
            cfg.update(config)

        # 1. Mode: disabled | shadow | active (default: disabled)
        self.mode = str(
            kwargs.get("mode")
            or cfg.get("mode")
            or cfg.get("social_mode")
            or os.getenv("TA_SOCIAL_MODE", "disabled")
        ).strip().lower()

        # 2. Provider: default archive_sqlite
        self.provider_name = str(
            kwargs.get("provider_name")
            or kwargs.get("provider")
            or cfg.get("provider")
            or cfg.get("social_provider")
            or os.getenv("TA_SOCIAL_PROVIDER", "archive_sqlite")
        ).strip()

        # 3. Archive DB Path: default empty
        raw_db = (
            kwargs.get("archive_db")
            or cfg.get("archive_db")
            or cfg.get("social_archive_db")
            or os.getenv("TA_SOCIAL_ARCHIVE_DB", "")
        )
        self.archive_db = str(raw_db).strip() if raw_db else ""

        # 4. Platforms: default xhs,dy
        platforms_raw = (
            kwargs.get("platforms")
            or cfg.get("platforms")
            or cfg.get("social_platforms")
            or os.getenv("TA_SOCIAL_PLATFORMS", "xhs,dy")
        )
        if isinstance(platforms_raw, (list, tuple, set)):
            self.platforms = [str(p).strip() for p in platforms_raw if str(p).strip()]
        elif isinstance(platforms_raw, str):
            self.platforms = [p.strip() for p in platforms_raw.split(",") if p.strip()]
        else:
            self.platforms = ["xhs", "dy"]

        # 5. Numerical / Threshold Settings (§7)
        self.lookback_days = int(
            kwargs.get("lookback_days")
            or cfg.get("lookback_days")
            or os.getenv("TA_SOCIAL_LOOKBACK_DAYS")
            or 7
        )
        self.max_posts = int(
            kwargs.get("max_posts")
            or cfg.get("max_posts")
            or os.getenv("TA_SOCIAL_MAX_POSTS")
            or 100
        )
        self.max_comments = int(
            kwargs.get("max_comments")
            or cfg.get("max_comments")
            or os.getenv("TA_SOCIAL_MAX_COMMENTS")
            or 300
        )
        self.min_posts = int(
            kwargs.get("min_posts")
            or cfg.get("min_posts")
            or os.getenv("TA_SOCIAL_MIN_POSTS")
            or 3
        )
        self.min_classified = int(
            kwargs.get("min_classified")
            or cfg.get("min_classified")
            or os.getenv("TA_SOCIAL_MIN_CLASSIFIED")
            or 20
        )
        self.min_authors = int(
            kwargs.get("min_authors")
            or cfg.get("min_authors")
            or os.getenv("TA_SOCIAL_MIN_AUTHORS")
            or 10
        )
        self.evidence_limit = int(
            kwargs.get("evidence_limit")
            or cfg.get("evidence_limit")
            or os.getenv("TA_SOCIAL_EVIDENCE_LIMIT")
            or 20
        )

        # 6. Canary Symbols: active whitelist; empty = all (§7)
        canary_raw = (
            kwargs.get("canary_symbols")
            or cfg.get("canary_symbols")
            or os.getenv("TA_SOCIAL_CANARY_SYMBOLS", "")
        )
        if isinstance(canary_raw, (list, tuple, set)):
            self.canary_symbols = {str(s).strip() for s in canary_raw if str(s).strip()}
        elif isinstance(canary_raw, str):
            self.canary_symbols = {s.strip() for s in canary_raw.split(",") if s.strip()}
        else:
            self.canary_symbols = set()

        # 7. Timeout: default 5 seconds
        timeout_val = (
            kwargs.get("fetch_timeout")
            or kwargs.get("timeout")
            or cfg.get("fetch_timeout")
            or os.getenv("TA_SOCIAL_FETCH_TIMEOUT")
            or 5
        )
        self.fetch_timeout = float(timeout_val)
        self.fetch_timeout_ms = int(self.fetch_timeout * 1000)

        # 8. Provider / Registry
        self._custom_provider = custom_provider
        if registry is not None:
            self.registry = registry
        else:
            self.registry = build_default_social_registry(
                archive_db_path=self.archive_db if self.archive_db else None,
                timeout_ms=self.fetch_timeout_ms,
            )

    def _is_canary_matched(self, symbol: str, norm_symbol: str) -> bool:
        """Check if symbol matches canary whitelist.

        Empty canary_symbols means all symbols are allowed.
        """
        if not self.canary_symbols:
            return True

        normalized_canary = set()
        for c in self.canary_symbols:
            n_c = normalize_stock_code(c)
            if n_c:
                normalized_canary.add(n_c)
                normalized_canary.add(n_c.split(".")[0])
            normalized_canary.add(c.upper())
            normalized_canary.add(c.split(".")[0].upper())

        code_only = norm_symbol.split(".")[0] if "." in norm_symbol else norm_symbol
        raw_upper = symbol.strip().upper()
        raw_code = raw_upper.split(".")[0]

        return bool(
            norm_symbol in normalized_canary
            or code_only in normalized_canary
            or raw_upper in normalized_canary
            or raw_code in normalized_canary
        )

    def collect(
        self,
        symbol: str,
        as_of: str,
        *,
        now: Optional[datetime] = None,
    ) -> SocialDataContext:
        """Collect social sentiment context for the given symbol and as-of date (Task 7 / §7).

        Returns:
            SocialDataContext dictionary matching §8 contract.
        """
        start_time = time.time()

        # Step 1: Normalize symbol and validate A-share code
        norm_symbol = normalize_stock_code(symbol)
        if not norm_symbol:
            bundle = create_empty_sentiment_bundle(
                status=SocialStatus.NOT_APPLICABLE.value,
                requested_as_of=as_of,
                cutoff_at="",
                reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
                symbol=symbol,
            )
            return create_default_social_data_context(
                status=SocialStatus.NOT_APPLICABLE.value,
                mode=self.mode,
                requested_as_of=as_of,
                reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
                bundle=bundle,
                source_provenance={},
                data_failure_ledger=[],
            )

        # Step 2: Check disabled mode
        if self.mode == "disabled":
            bundle = create_empty_sentiment_bundle(
                status=SocialStatus.NOT_APPLICABLE.value,
                requested_as_of=as_of,
                cutoff_at="",
                reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
                symbol=norm_symbol,
            )
            return create_default_social_data_context(
                status=SocialStatus.NOT_APPLICABLE.value,
                mode="disabled",
                requested_as_of=as_of,
                reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
                bundle=bundle,
                source_provenance={},
                data_failure_ledger=[],
            )

        # Step 3: Canary check in active mode
        if self.mode == "active" and self.canary_symbols:
            if not self._is_canary_matched(symbol, norm_symbol):
                # Symbol did not hit canary whitelist: MUST NOT silently be active.
                # Fall back to not_applicable with direction_allowed=False
                bundle = create_empty_sentiment_bundle(
                    status=SocialStatus.NOT_APPLICABLE.value,
                    requested_as_of=as_of,
                    cutoff_at="",
                    reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
                    symbol=norm_symbol,
                )
                return create_default_social_data_context(
                    status=SocialStatus.NOT_APPLICABLE.value,
                    mode="disabled",
                    requested_as_of=as_of,
                    reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
                    bundle=bundle,
                    source_provenance={},
                    data_failure_ledger=[],
                )

        # Step 4: Validate archive_db path in shadow / active mode
        db_path = self.archive_db
        if not db_path or not os.path.isabs(db_path) or not os.path.exists(db_path):
            bundle = create_empty_sentiment_bundle(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at="",
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
                symbol=norm_symbol,
            )
            ledger = build_social_failure_ledger(
                status=SocialStatus.FAILED.value,
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
            )
            return create_default_social_data_context(
                status=SocialStatus.FAILED.value,
                mode=self.mode,
                requested_as_of=as_of,
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
                bundle=bundle,
                source_provenance={
                    "social_archive": {
                        "status": SocialStatus.FAILED.value,
                        "provider": self.provider_name,
                    }
                },
                data_failure_ledger=ledger,
            )

        # Step 5: Resolve Provider
        provider = self._custom_provider or self.registry.get(self.provider_name)
        if provider is None:
            bundle = create_empty_sentiment_bundle(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at="",
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
                symbol=norm_symbol,
            )
            ledger = build_social_failure_ledger(
                status=SocialStatus.FAILED.value,
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
            )
            return create_default_social_data_context(
                status=SocialStatus.FAILED.value,
                mode=self.mode,
                requested_as_of=as_of,
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
                bundle=bundle,
                source_provenance={
                    "social_archive": {
                        "status": SocialStatus.FAILED.value,
                        "provider": self.provider_name,
                    }
                },
                data_failure_ledger=ledger,
            )

        # Step 6: Fetch records from provider
        fetch_result = provider.fetch_records(
            symbol=norm_symbol,
            as_of=as_of,
            lookback_days=self.lookback_days,
            platforms=self.platforms,
            max_posts=self.max_posts,
            max_comments=self.max_comments,
            now=now,
        )

        # Step 7: Aggregate records into sentiment bundle
        bundle = aggregate_sentiment_bundle(
            records=fetch_result,
            symbol=norm_symbol,
            as_of=as_of,
            lookback_days=self.lookback_days,
            max_posts=self.max_posts,
            max_comments=self.max_comments,
            min_posts=self.min_posts,
            min_classified=self.min_classified,
            min_authors=self.min_authors,
            evidence_limit=self.evidence_limit,
            now=now,
            platforms=self.platforms,
        )

        # Step 8: Build failure ledger & provenance (§5.5, §8)
        failure_ledger = build_social_failure_ledger(
            status=bundle.status,
            reason_codes=bundle.reason_codes,
        )

        meta_info = fetch_result.meta if hasattr(fetch_result, "meta") and fetch_result.meta else {}
        source_provenance = {
            "social_archive": {
                "status": bundle.status,
                "content_as_of": bundle.content_as_of,
                "metric_as_of": bundle.metric_as_of,
                "provider": self.provider_name,
                "schema_version": int(meta_info.get("schema_version", 1)),
            }
        }

        # Step 9: Assemble final SocialDataContext
        context = create_default_social_data_context(
            status=bundle.status,
            mode=self.mode,
            requested_as_of=as_of,
            reason_codes=bundle.reason_codes,
            bundle=bundle,
            source_provenance=source_provenance,
            data_failure_ledger=failure_ledger,
        )

        elapsed = time.time() - start_time
        post_cnt = bundle.social_attention.post_count if bundle.social_attention else 0
        comment_cnt = bundle.social_attention.comment_count if bundle.social_attention else 0
        logger.info(
            "SocialDataCollector.collect: symbol=%s as_of=%s mode=%s status=%s posts=%d comments=%d elapsed=%.3fs",
            norm_symbol,
            as_of,
            self.mode,
            bundle.status,
            post_cnt,
            comment_cnt,
            elapsed,
        )

        return context
