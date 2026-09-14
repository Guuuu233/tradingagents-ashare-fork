from abc import ABC, abstractmethod
from typing import Any, Optional

from .proxy_guard import check_llm_proxy_guard


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        self.model = model
        self.base_url = base_url
        self.kwargs = kwargs
        if self.base_url:
            check_llm_proxy_guard(self.base_url)

    @abstractmethod
    def get_llm(self) -> Any:
        """Return the configured LLM instance."""
        pass

    @abstractmethod
    def validate_model(self) -> bool:
        """Validate that the model is supported by this client."""
        pass

    def evaluate_model(
        self,
        *,
        discovered_models: Optional[list] = None,
        strict: bool = False,
    ) -> Any:
        """Evaluate model support against advisory policy, returning ModelValidationResult."""
        from .validators import evaluate_model_policy

        provider = getattr(self, "provider", "unknown")
        return evaluate_model_policy(
            provider=provider,
            model=self.model,
            base_url=self.base_url,
            discovered_models=discovered_models,
            strict=strict,
        )
