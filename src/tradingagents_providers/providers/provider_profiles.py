"""
ProviderProfile - Declarative provider configuration for TradingAgents.

This is a standalone copy of the ProviderProfile dataclass for use when
the provider package is installed independently of TradingAgents core.

For full functionality, install TradingAgents alongside this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Valid API modes
VALID_API_MODES = frozenset(
    {
        "chat_completions",
        "anthropic_messages",
        "google_native",
        "azure_openai",
        "bedrock_converse",
        "codex_responses",
        "external_process",
        "unsupported",
    }
)

# Valid runtime statuses
VALID_RUNTIME_STATUSES = frozenset(
    {
        "ready",
        "needs_adapter",
        "needs_oauth",
        "metadata_only",
    }
)


@dataclass(frozen=True)
class ProviderProfile:
    """
    Declarative configuration for an LLM provider.

    Attributes:
        name: Unique identifier (lowercase, no spaces)
        display_name: Human-readable name
        aliases: Alternative names that resolve to this provider
        api_mode: Runtime routing hint (chat_completions, anthropic_messages, etc.)
        runtime_status: ready, needs_adapter, needs_oauth, metadata_only
        auth_type: api_key, oauth, none
        api_key_env_vars: Environment variables to check for API key
        base_url: Default API endpoint base URL
        base_url_env_var: Environment variable that overrides base_url
        signup_url: URL where users can sign up
        quick_models: Default models for quick thinking agents
        deep_models: Default models for deep thinking agents
    """

    name: str
    display_name: str
    aliases: tuple[str, ...] = ()

    api_mode: str = "chat_completions"
    runtime_status: str = "ready"
    auth_type: str = "api_key"

    api_key_env_vars: tuple[str, ...] = ()
    base_url: Optional[str] = None
    base_url_env_var: Optional[str] = None
    signup_url: Optional[str] = None

    quick_models: tuple[tuple[str, str], ...] = ()
    deep_models: tuple[tuple[str, str], ...] = ()

    metadata: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Validate and normalize profile fields."""
        if self.name != self.name.lower():
            object.__setattr__(self, "name", self.name.lower())

        if self.api_mode not in VALID_API_MODES:
            raise ValueError(
                f"Invalid api_mode '{self.api_mode}'. "
                f"Must be one of: {sorted(VALID_API_MODES)}"
            )

        if self.runtime_status not in VALID_RUNTIME_STATUSES:
            raise ValueError(
                f"Invalid runtime_status '{self.runtime_status}'. "
                f"Must be one of: {sorted(VALID_RUNTIME_STATUSES)}"
            )

        if self.auth_type == "api_key" and not self.api_key_env_vars:
            raise ValueError(
                f"Provider '{self.name}' with auth_type='api_key' "
                f"must specify at least one api_key_env_vars entry."
            )

    def get_default_quick_model(self) -> Optional[str]:
        """Return the default model ID for quick thinking, or None."""
        if self.quick_models:
            return self.quick_models[0][1]
        return None

    def get_default_deep_model(self) -> Optional[str]:
        """Return the default model ID for deep thinking, or None."""
        if self.deep_models:
            return self.deep_models[0][1]
        return None
