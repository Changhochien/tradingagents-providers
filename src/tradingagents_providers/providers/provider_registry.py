"""
Provider Registry - Standalone implementation for TradingAgents providers.

This is a standalone copy of the provider registry for use when
the provider package is installed independently of TradingAgents core.

For full functionality, install TradingAgents alongside this package.
"""

from __future__ import annotations

from typing import Optional

from .provider_profiles import ProviderProfile

# Global registry state
_PROVIDERS: dict[str, ProviderProfile] = {}
_ALIASES: dict[str, str] = {}
_REGISTERED = False


def register_provider(profile: ProviderProfile) -> None:
    """Register a provider profile in the global registry."""
    global _PROVIDERS, _ALIASES

    _PROVIDERS[profile.name] = profile

    for alias in profile.aliases:
        _ALIASES[alias.lower()] = profile.name


def get_provider_profile(name: str) -> Optional[ProviderProfile]:
    """Get a provider profile by name or alias."""
    name_lower = name.lower()
    canonical = _ALIASES.get(name_lower, name_lower)
    return _PROVIDERS.get(canonical)


def list_provider_profiles() -> list[ProviderProfile]:
    """Get all registered provider profiles."""
    return list(_PROVIDERS.values())


def get_provider_names() -> list[str]:
    """Get all registered provider names."""
    return list(_PROVIDERS.keys())


def resolve_provider_base_url(provider: str) -> Optional[str]:
    """Resolve the base URL for a provider."""
    import os

    profile = get_provider_profile(provider)
    if profile:
        if profile.base_url_env_var:
            env_url = os.environ.get(profile.base_url_env_var)
            if env_url:
                return env_url
        return profile.base_url
    return None


def resolve_api_key_env(provider: str) -> Optional[str]:
    """Resolve the API key environment variable for a provider."""
    import os

    profile = get_provider_profile(provider)
    if profile and profile.api_key_env_vars:
        for env_var in profile.api_key_env_vars:
            if env_var in os.environ:
                return env_var
        return profile.api_key_env_vars[0]
    return None
