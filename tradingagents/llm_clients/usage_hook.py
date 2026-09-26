"""DAV-1314: shared mount point for the unified LLM usage ledger callback.

Every provider client (OpenAI/Anthropic/Google/...) routes through here so the
``LLMUsageLogger`` is attached exactly once per model instance, regardless of
which vendor SDK the model runs on. Lives in ``tradingagents`` (not ``api``) so
client code never imports the api layer eagerly; the api import is lazy and
degrades silently for standalone CLI use.
"""

import logging
from typing import Any, Dict

_logger = logging.getLogger(__name__)


def attach_usage_logger(llm_kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Append the shared LLMUsageLogger to ``llm_kwargs["callbacks"]``.

    Idempotent: a caller-supplied LLMUsageLogger is never duplicated.
    Never raises — when the api package is unavailable (standalone CLI,
    partial installs), construction proceeds without the ledger callback.
    """
    try:
        from api.usage_logging import LLM_USAGE_LOGGER, LLMUsageLogger

        callbacks = list(llm_kwargs.get("callbacks") or [])
        if not any(isinstance(c, LLMUsageLogger) for c in callbacks):
            callbacks.append(LLM_USAGE_LOGGER)
        llm_kwargs["callbacks"] = callbacks
    except Exception:
        _logger.debug("LLMUsageLogger unavailable; skipping usage callback")
    return llm_kwargs
