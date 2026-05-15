"""
Runtime Provider Resolution - Standalone implementation.

This module provides the runtime resolution logic for when the provider
package is installed independently of TradingAgents core.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from .provider_profiles import ProviderProfile
from .provider_registry import get_provider_profile


@dataclass(frozen=True)
class RuntimeProvider:
    """Resolved runtime configuration for executing a provider."""

    provider: str
    requested_provider: str
    api_mode: str
    auth_type: str
    base_url: str | None
    api_key: str | None
    source: str
    model: str | None = None
    runtime_status: str = "ready"
    profile: ProviderProfile | None = None


class ProviderRuntimeError(Exception):
    """Raised when a provider cannot be used at runtime."""

    def __init__(self, provider: str, reason: str, suggestion: str | None = None):
        self.provider = provider
        self.reason = reason
        self.suggestion = suggestion
        msg = f"Provider '{provider}' cannot run: {reason}"
        if suggestion:
            msg += f"\n\nSuggestion: {suggestion}"
        super().__init__(msg)


class ProviderNotFoundError(Exception):
    """Raised when a provider is not found in the registry."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(
            f"Provider '{provider}' not found. "
            f"Run 'tradingagents providers list' to see available providers."
        )


class NeedsAdapterError(ProviderRuntimeError):
    """Raised when a provider needs an adapter that isn't implemented."""

    def __init__(self, provider: str, adapter: str):
        self.adapter = adapter
        super().__init__(
            provider,
            f"requires the {adapter}",
            "Install the required adapter or use a different provider.",
        )


class NeedsOAuthError(ProviderRuntimeError):
    """Raised when a provider needs OAuth flow."""

    def __init__(self, provider: str):
        super().__init__(
            provider,
            "requires OAuth/device-code authentication",
            "Use 'tradingagents providers setup {provider}' to authenticate.",
        )


def resolve_runtime_provider(
    provider: str,
    model: str | None = None,
    explicit_api_key: str | None = None,
    explicit_base_url: str | None = None,
) -> RuntimeProvider:
    """
    Resolve a provider to a RuntimeProvider with credentials and config.
    """
    requested_provider = provider
    provider_lower = provider.lower()

    profile = get_provider_profile(provider_lower)

    if profile is None:
        return _resolve_unknown_provider(
            provider, model, explicit_api_key, explicit_base_url
        )

    # Check runtime status
    if profile.runtime_status == "needs_adapter":
        adapter_map = {
            "anthropic_messages": "Anthropic Messages adapter",
            "google_native": "Google GenerativeAI adapter",
            "azure_openai": "Azure OpenAI adapter",
            "bedrock_converse": "Bedrock Converse adapter",
            "codex_responses": "Codex Responses adapter",
            "external_process": "external process adapter",
        }
        adapter = adapter_map.get(profile.api_mode, "required adapter")
        raise NeedsAdapterError(profile.name, adapter)

    # Resolve base_url
    base_url = None
    source = "profile"

    if explicit_base_url:
        base_url = explicit_base_url
        source = "explicit"
    elif profile.base_url_env_var:
        env_url = os.environ.get(profile.base_url_env_var)
        if env_url:
            base_url = env_url
        elif profile.base_url:
            base_url = profile.base_url
    elif profile.base_url:
        base_url = profile.base_url

    # Resolve API key
    api_key = None

    if explicit_api_key:
        api_key = explicit_api_key
        source = "explicit"
    elif profile.auth_type in {"oauth_device_code", "oauth_external"}:
        try:
            from tradingagents_providers.oauth import resolve_oauth_runtime_credentials

            oauth_runtime = resolve_oauth_runtime_credentials(profile.name)
        except Exception as exc:
            raise NeedsOAuthError(profile.name) from exc
        api_key = oauth_runtime.get("api_key")
        base_url = explicit_base_url or oauth_runtime.get("base_url") or base_url
        source = str(oauth_runtime.get("source") or "oauth")
    elif profile.auth_type == "api_key":
        for env_var in profile.api_key_env_vars:
            key = os.environ.get(env_var)
            if key:
                api_key = key
                break

    return RuntimeProvider(
        provider=profile.name,
        requested_provider=requested_provider,
        api_mode=profile.api_mode,
        auth_type=profile.auth_type,
        base_url=base_url,
        api_key=api_key,
        source=source,
        model=model,
        runtime_status=profile.runtime_status,
        profile=profile,
    )


def _resolve_unknown_provider(
    provider: str,
    model: str | None,
    explicit_api_key: str | None,
    explicit_base_url: str | None,
) -> RuntimeProvider:
    """Handle unknown providers with legacy fallback."""
    legacy_api_key = explicit_api_key or os.environ.get(f"{provider.upper()}_API_KEY")
    legacy_base_url = os.environ.get(f"{provider.upper()}_BASE_URL")

    return RuntimeProvider(
        provider=provider.lower(),
        requested_provider=provider,
        api_mode="chat_completions",
        auth_type="api_key" if legacy_api_key else "none",
        base_url=explicit_base_url or legacy_base_url,
        api_key=legacy_api_key,
        source="legacy" if legacy_base_url or legacy_api_key else "explicit",
        model=model,
        runtime_status="ready",
        profile=None,
    )
