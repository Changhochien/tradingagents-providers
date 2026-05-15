"""
Standalone provider implementation for TradingAgents.

This module provides ProviderProfile, ProviderRegistry, and RuntimeProvider
for use when the provider package is installed independently of TradingAgents core.

For full TradingAgents integration, the providers are registered via the
'tradingagents.model_providers' entry point.
"""

from .provider_profiles import ProviderProfile, VALID_API_MODES, VALID_RUNTIME_STATUSES
from .provider_registry import (
    register_provider,
    get_provider_profile,
    list_provider_profiles,
    get_provider_names,
    resolve_provider_base_url,
    resolve_api_key_env,
)
from .runtime import (
    RuntimeProvider,
    resolve_runtime_provider,
    ProviderRuntimeError,
    ProviderNotFoundError,
    NeedsAdapterError,
    NeedsOAuthError,
)

__all__ = [
    "ProviderProfile",
    "RuntimeProvider",
    "VALID_API_MODES",
    "VALID_RUNTIME_STATUSES",
    "register_provider",
    "get_provider_profile",
    "list_provider_profiles",
    "get_provider_names",
    "resolve_provider_base_url",
    "resolve_api_key_env",
    "resolve_runtime_provider",
    "ProviderRuntimeError",
    "ProviderNotFoundError",
    "NeedsAdapterError",
    "NeedsOAuthError",
]
