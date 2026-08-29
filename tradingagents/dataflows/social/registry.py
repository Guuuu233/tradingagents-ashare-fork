"""TradingAgents social data provider registry (Task 5 / B5).

Specifications:
- docs/social_data/implementation_plan.md Task 5, §7
- Standalone social provider registry.
- Protocol: SocialDataProvider (only name + fetch_records).
- Does NOT inherit from BaseMarketDataProvider.
- Does NOT register into market providers/registry.py.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from tradingagents.dataflows.social.provider import (
    SocialArchiveProvider,
    SocialDataProvider,
)


class SocialDataProviderRegistry:
    """Registry for SocialDataProvider implementations."""

    def __init__(self) -> None:
        self._providers: Dict[str, SocialDataProvider] = {}

    def register(self, provider: SocialDataProvider) -> None:
        """Register a social data provider instance."""
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> Optional[SocialDataProvider]:
        """Get a registered social data provider by name."""
        return self._providers.get(provider_name)

    def list_names(self) -> List[str]:
        """List all registered provider names."""
        return list(self._providers.keys())


def build_default_social_registry(
    archive_db_path: Optional[str] = None,
    timeout_ms: Optional[int] = None,
) -> SocialDataProviderRegistry:
    """Build default social registry containing archive_sqlite provider."""
    registry = SocialDataProviderRegistry()
    archive_provider = SocialArchiveProvider(
        db_path=archive_db_path,
        timeout_ms=timeout_ms,
    )
    registry.register(archive_provider)
    return registry
