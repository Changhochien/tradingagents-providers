"""
Provider Catalog Registration

This module is loaded via the 'tradingagents.model_providers' entry point.
It registers all built-in provider profiles with TradingAgents' provider registry.

TradingAgents core calls this function automatically when it discovers
the entry point.
"""

from __future__ import annotations


_THINKING_LEVELS = ("low", "medium", "high")
_THINKING_ALIASES = {
    "minimal": "low",
    "xhigh": "high",
}
_THINKING_CONFIG_BY_PROVIDER = {
    "xiaomi": {
        "config_key": "xiaomi_thinking_level",
        "env_var": "XIAOMI_THINKING_LEVEL",
        "param": "reasoning_effort",
        "levels": _THINKING_LEVELS,
    },
    "minimax": {
        "config_key": "minimax_thinking_level",
        "env_var": "MINIMAX_THINKING_LEVEL",
        "param": "reasoning_effort",
        "levels": _THINKING_LEVELS,
    },
    "minimax-cn": {
        "config_key": "minimax_cn_thinking_level",
        "env_var": "MINIMAX_CN_THINKING_LEVEL",
        "param": "reasoning_effort",
        "levels": _THINKING_LEVELS,
    },
    "openai-codex": {
        "config_key": "openai_codex_thinking_level",
        "env_var": "OPENAI_CODEX_THINKING_LEVEL",
        "param": "reasoning_effort",
        "levels": _THINKING_LEVELS,
    },
}

_CODEX_MODELS = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
)


def _models(*ids: str) -> tuple[tuple[str, str], ...]:
    """Create model tuples from model IDs."""
    if not ids:
        return (("Custom model ID", "custom"),)
    return tuple((model_id, model_id) for model_id in ids) + (
        ("Custom model ID", "custom"),
    )


def register() -> None:
    """
    Register all provider profiles with TradingAgents.

    This function is called by TradingAgents core via the entry point system.
    It imports the provider registry and registers all built-in providers.
    """
    _register_extension_hooks()

    # Try TradingAgents context first
    try:
        from tradingagents.model_providers import register_provider, ProviderProfile

        _do_register(register_provider, ProviderProfile)
    except ImportError:
        pass

    # Try plugins context (legacy)
    try:
        from plugins.model_providers import register_provider, ProviderProfile

        _do_register(register_provider, ProviderProfile)
    except ImportError:
        pass

    # Standalone mode - use our own registry
    from tradingagents_providers.providers.provider_registry import register_provider
    from tradingagents_providers.providers.provider_profiles import ProviderProfile

    _do_register(register_provider, ProviderProfile)


def _register_extension_hooks() -> None:
    """Register TradingAgents runtime hooks when core is installed."""
    try:
        from tradingagents.ext_loader import (
            set_cli_command_hook,
            set_factory_resolver,
            set_model_catalog_hook,
        )
    except ImportError:
        return

    set_factory_resolver(_factory_resolver)
    set_model_catalog_hook(_model_catalog_hook)
    set_cli_command_hook(_cli_command_hook)


def _get_provider_api():
    """Return provider API functions from core if available, else standalone."""
    from tradingagents_providers.providers import (
        ProviderRuntimeError,
        get_provider_profile,
        list_provider_profiles,
        resolve_runtime_provider,
    )

    return (
        ProviderRuntimeError,
        get_provider_profile,
        list_provider_profiles,
        resolve_runtime_provider,
    )


def _get_core_provider_api():
    """Return core provider API functions when a TradingAgents fork exposes them."""
    try:
        from tradingagents.model_providers import (
            ProviderRuntimeError,
            get_provider_profile,
            list_provider_profiles,
            resolve_runtime_provider,
        )

        return (
            ProviderRuntimeError,
            get_provider_profile,
            list_provider_profiles,
            resolve_runtime_provider,
        )
    except ImportError:
        return _get_provider_api()


def _factory_resolver(provider: str, model: str, base_url: str | None = None, **kwargs):
    """Factory resolver hook used by TradingAgents core."""
    _, _, _, resolve_runtime_provider = _get_provider_api()
    client_kwargs = dict(kwargs)
    explicit_api_key = client_kwargs.pop("api_key", None)
    runtime = resolve_runtime_provider(
        provider,
        model=model,
        explicit_api_key=explicit_api_key,
        explicit_base_url=base_url,
    )

    if runtime.profile or runtime.source in ("explicit", "legacy"):
        return _create_client_from_runtime(runtime, **client_kwargs)
    return None


def _model_catalog_hook(provider: str, mode: str = "quick"):
    """Model catalog hook used by TradingAgents core."""
    _, get_provider_profile, _, _ = _get_provider_api()
    profile = get_provider_profile(provider)
    if not profile:
        return None
    if mode == "deep":
        return list(profile.deep_models)
    return list(profile.quick_models)


def get_provider_thinking_config(provider: str) -> dict | None:
    """Return thinking-level config for a provider, if the plugin declares one."""
    _, get_provider_profile, _, _ = _get_provider_api()
    profile = get_provider_profile(provider)
    profile_name = getattr(profile, "name", provider) if profile is not None else provider

    metadata = (getattr(profile, "metadata", {}) or {}) if profile is not None else {}
    thinking = metadata.get("thinking")
    if not isinstance(thinking, dict):
        thinking = _THINKING_CONFIG_BY_PROVIDER.get(str(profile_name).lower())
    if not isinstance(thinking, dict):
        return None
    return dict(thinking)


def get_thinking_level_kwargs(
    provider: str,
    config: dict | None = None,
) -> dict[str, str]:
    """Resolve provider thinking settings into LLM-client kwargs.

    Hermes exposes a unified reasoning-effort UX, while TradingAgents forwards
    provider kwargs directly to the selected LLM client. Xiaomi and MiniMax use
    the OpenAI-compatible path here, so their supported overlap is the
    ``reasoning_effort`` keyword.
    """
    thinking = get_provider_thinking_config(provider)
    if not thinking:
        return {}

    config = config or {}
    config_key = str(thinking.get("config_key") or "")
    env_var = str(thinking.get("env_var") or "")
    raw_level = None
    if config_key:
        raw_level = config.get(config_key)
    if not raw_level and env_var:
        import os

        raw_level = os.environ.get(env_var)
    if not raw_level:
        return {}

    level = str(raw_level).strip().lower()
    if level == "none":
        return {}
    level = _THINKING_ALIASES.get(level, level)

    allowed = tuple(thinking.get("levels") or _THINKING_LEVELS)
    if level not in allowed:
        allowed_text = ", ".join((*allowed, "minimal", "xhigh", "none"))
        raise ValueError(
            f"Invalid thinking level for {provider}: {raw_level!r}. "
            f"Expected one of: {allowed_text}."
        )

    param = str(thinking.get("param") or "reasoning_effort")
    return {param: level}


def _cli_command_hook(app) -> None:
    """CLI hook used by TradingAgents core."""
    from tradingagents_providers.bootstrap import patch_loaded_cli_modules
    from tradingagents_providers.cli import register_cli_commands

    patch_loaded_cli_modules(app)
    register_cli_commands(app)


def _create_client_from_runtime(runtime, **kwargs):
    """Create a TradingAgents LLM client from a RuntimeProvider."""
    ProviderRuntimeError, _, _, _ = _get_provider_api()
    if runtime.provider == "google-gemini-cli":
        return _create_google_cloudcode_client(
            model=runtime.model or "",
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            project_id=getattr(runtime, "project_id", None),
            managed_project_id=getattr(runtime, "managed_project_id", None),
            **kwargs,
        )

    if runtime.api_mode in ("chat_completions", "azure_openai"):
        return _create_openai_compatible_client(
            model=runtime.model or "",
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            provider=runtime.provider,
            **kwargs,
        )

    if runtime.api_mode == "codex_responses":
        return _create_codex_responses_client(
            model=runtime.model or "",
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            **kwargs,
        )

    if runtime.api_mode == "anthropic_messages":
        return _create_anthropic_client(
            model=runtime.model or "",
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            **kwargs,
        )

    if runtime.api_mode == "google_native":
        return _create_google_client(
            model=runtime.model or "",
            base_url=runtime.base_url,
            api_key=runtime.api_key,
            **kwargs,
        )

    raise ProviderRuntimeError(
        runtime.provider,
        f"api_mode '{runtime.api_mode}' is not supported by the TradingAgents integration",
        "Implement an adapter for this api_mode or mark the provider as needs_adapter.",
    )


def _create_openai_compatible_client(
    model: str,
    base_url: str | None,
    api_key: str | None,
    provider: str,
    **kwargs,
):
    """Create an OpenAI-compatible TradingAgents client."""
    from tradingagents.llm_clients.openai_client import OpenAIClient

    return OpenAIClient(
        model=model,
        base_url=base_url,
        provider=provider,
        **{"api_key": api_key, **kwargs},
    )


def _create_codex_responses_client(
    model: str,
    base_url: str | None,
    api_key: str | None,
    **kwargs,
):
    """Create a Responses API client for OAuth-backed OpenAI Codex."""
    from tradingagents_providers.codex_client import CodexResponsesClient

    return CodexResponsesClient(
        model=model,
        base_url=base_url,
        **{"api_key": api_key, **kwargs},
    )


def _create_google_cloudcode_client(
    model: str,
    base_url: str | None,
    api_key: str | None,
    project_id: str | None = None,
    managed_project_id: str | None = None,
    **kwargs,
):
    """Create a Cloud Code Assist client for Gemini CLI OAuth."""
    from tradingagents_providers.google_cloudcode_client import GoogleCloudCodeClient

    return GoogleCloudCodeClient(
        model=model,
        base_url=base_url,
        **{
            "api_key": api_key,
            "project_id": project_id,
            "managed_project_id": managed_project_id,
            **kwargs,
        },
    )


def _create_anthropic_client(
    model: str,
    api_key: str | None,
    base_url: str | None = None,
    **kwargs,
):
    """Create an Anthropic TradingAgents client."""
    from tradingagents.llm_clients.anthropic_client import AnthropicClient

    return AnthropicClient(model=model, base_url=base_url, api_key=api_key, **kwargs)


def _create_google_client(
    model: str,
    api_key: str | None,
    base_url: str | None = None,
    **kwargs,
):
    """Create a Google/Gemini TradingAgents client."""
    from tradingagents.llm_clients.google_client import GoogleClient

    return GoogleClient(model=model, base_url=base_url, api_key=api_key, **kwargs)


def _do_register(register_provider, ProviderProfile) -> None:
    """Register all providers with the given registry functions."""
    _register(
        register_provider,
        ProviderProfile,
        name="openai",
        display_name="OpenAI",
        aliases=("openai-compat",),
        api_key_env_vars=("OPENAI_API_KEY",),
        base_url="https://api.openai.com/v1",
        signup_url="https://platform.openai.com/api-keys",
        models=("gpt-4o", "gpt-4o-mini", "gpt-4.1-mini"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="anthropic",
        display_name="Anthropic",
        aliases=("claude",),
        api_mode="anthropic_messages",
        api_key_env_vars=("ANTHROPIC_API_KEY",),
        base_url="https://api.anthropic.com/",
        signup_url="https://console.anthropic.com/settings/keys",
        models=("claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="google",
        display_name="Google",
        aliases=("gemini", "google-ai"),
        api_mode="google_native",
        api_key_env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        base_url="https://generativelanguage.googleapis.com/",
        signup_url="https://aistudio.google.com/app/apikey",
        models=("gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="xai",
        display_name="xAI",
        aliases=("grok",),
        api_key_env_vars=("XAI_API_KEY",),
        base_url="https://api.x.ai/v1",
        signup_url="https://x.ai/api",
        models=("grok-3", "grok-3-mini", "grok-2"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="deepseek",
        display_name="DeepSeek",
        aliases=("deepseek-ai",),
        api_key_env_vars=("DEEPSEEK_API_KEY",),
        base_url="https://api.deepseek.com",
        signup_url="https://platform.deepseek.com/api_keys",
        models=("deepseek-chat", "deepseek-coder", "deepseek-reasoner"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="openrouter",
        display_name="OpenRouter",
        aliases=("open-router",),
        api_key_env_vars=("OPENROUTER_API_KEY",),
        base_url="https://openrouter.ai/api/v1",
        base_url_env_var="OPENROUTER_BASE_URL",
        signup_url="https://openrouter.ai/credits",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="ollama",
        display_name="Ollama",
        aliases=("local",),
        auth_type="none",
        base_url="http://localhost:11434/v1",
        base_url_env_var="OLLAMA_BASE_URL",
        signup_url="https://ollama.com/download",
        models=("llama3.2", "mistral", "qwen2.5"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="nvidia",
        display_name="NVIDIA NIM",
        aliases=("nvidia-nim",),
        api_key_env_vars=("NVIDIA_API_KEY",),
        base_url="https://integrate.api.nvidia.com/v1",
        signup_url="https://build.nvidia.com/",
        models=("nvidia/llama-3.3-70b-instruct", "nvidia/mistral-nemo-12b-instruct"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="ai-gateway",
        display_name="Vercel AI Gateway",
        aliases=("vercel", "ai-gateway"),
        api_key_env_vars=("AI_GATEWAY_API_KEY",),
        base_url="https://ai-gateway.vercel.sh/v1",
        base_url_env_var="AI_GATEWAY_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="alibaba",
        display_name="Alibaba Cloud DashScope",
        aliases=("dashscope", "qwen-dashscope"),
        api_key_env_vars=("DASHSCOPE_API_KEY",),
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        base_url_env_var="DASHSCOPE_BASE_URL",
        models=("qwen-plus", "qwen-max", "qwen-turbo"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="alibaba-coding-plan",
        display_name="Alibaba Cloud (Coding Plan)",
        aliases=("dashscope-coding-plan", "qwen-coding-plan"),
        api_key_env_vars=("QWEN_CODE_KEY", "DASHSCOPE_API_KEY"),
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        base_url_env_var="QWEN_CODE_BASE_URL",
        models=("qwen3-coder-plus", "qwen-plus"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="arcee",
        display_name="Arcee AI",
        aliases=("arcee-ai",),
        api_key_env_vars=("ARCEE_API_KEY",),
        base_url="https://conductor.arcee.ai/v1",
        base_url_env_var="ARCEE_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="gmi",
        display_name="GMI Cloud",
        aliases=("gmi-cloud",),
        api_key_env_vars=("GMI_API_KEY",),
        base_url="https://api.gmi-serving.com/v1",
        base_url_env_var="GMI_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="huggingface",
        display_name="Hugging Face",
        aliases=("hf", "hugging-face"),
        api_key_env_vars=("HF_TOKEN", "HUGGINGFACE_API_KEY"),
        base_url="https://router.huggingface.co/v1",
        base_url_env_var="HF_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="kilocode",
        display_name="Kilo Code",
        aliases=("kilo-code",),
        api_key_env_vars=("KILOCODE_API_KEY",),
        base_url="https://kilocode.ai/api/openrouter/v1",
        base_url_env_var="KILOCODE_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="kimi-coding",
        display_name="Kimi / Moonshot",
        aliases=("kimi", "moonshot"),
        api_key_env_vars=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        base_url="https://api.moonshot.ai/v1",
        base_url_env_var="MOONSHOT_BASE_URL",
        models=("kimi-k2-0905-preview", "kimi-k2-turbo-preview"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="kimi-coding-cn",
        display_name="Kimi / Moonshot (China)",
        aliases=("kimi-cn", "moonshot-cn"),
        api_key_env_vars=("MOONSHOT_CN_API_KEY", "KIMI_CN_API_KEY"),
        base_url="https://api.moonshot.cn/v1",
        base_url_env_var="MOONSHOT_CN_BASE_URL",
        models=("kimi-k2-0905-preview", "kimi-k2-turbo-preview"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="novita",
        display_name="NovitaAI",
        aliases=("novita-ai",),
        api_key_env_vars=("NOVITA_API_KEY",),
        base_url="https://api.novita.ai/v3/openai",
        base_url_env_var="NOVITA_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="ollama-cloud",
        display_name="Ollama Cloud",
        aliases=("ollama_cloud",),
        api_key_env_vars=("OLLAMA_CLOUD_API_KEY",),
        base_url="https://ollama.com/v1",
        base_url_env_var="OLLAMA_CLOUD_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="opencode-zen",
        display_name="OpenCode Zen",
        aliases=("opencode-zen-ai",),
        api_key_env_vars=("OPENCODE_ZEN_API_KEY",),
        base_url="https://api.opencode.ai/v1",
        base_url_env_var="OPENCODE_ZEN_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="opencode-go",
        display_name="OpenCode Go",
        aliases=("opencode-go-ai",),
        api_key_env_vars=("OPENCODE_GO_API_KEY",),
        base_url="https://api.opencode.ai/v1",
        base_url_env_var="OPENCODE_GO_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="stepfun",
        display_name="StepFun",
        aliases=("step", "stepfun-ai"),
        api_key_env_vars=("STEPFUN_API_KEY",),
        base_url="https://api.stepfun.com/v1",
        base_url_env_var="STEPFUN_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="xiaomi",
        display_name="Xiaomi MiMo",
        aliases=("mimo", "xiaomi-mimo"),
        api_key_env_vars=("XIAOMI_API_KEY",),
        base_url="https://api.xiaomimimo.com/v1",
        base_url_env_var="XIAOMI_BASE_URL",
        metadata={"thinking": _THINKING_CONFIG_BY_PROVIDER["xiaomi"]},
        models=(
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "mimo-v2-pro",
            "mimo-v2-omni",
            "mimo-v2-flash",
        ),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="zai",
        display_name="Z.AI (GLM)",
        aliases=("z-ai", "z.ai", "zhipu"),
        api_key_env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
        base_url="https://api.z.ai/api/paas/v4",
        base_url_env_var="GLM_BASE_URL",
        models=("glm-5", "glm-4-9b"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="gemini",
        display_name="Google AI Studio",
        aliases=("google-ai-studio", "google-gemini"),
        api_mode="google_native",
        api_key_env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        base_url="https://generativelanguage.googleapis.com/v1beta",
        base_url_env_var="GEMINI_BASE_URL",
        models=("gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-pro"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="azure-foundry",
        display_name="Azure Foundry",
        aliases=("azure-ai-foundry", "azure-ai"),
        api_mode="azure_openai",
        api_key_env_vars=("AZURE_FOUNDRY_API_KEY",),
        base_url_env_var="AZURE_FOUNDRY_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="bedrock",
        display_name="AWS Bedrock",
        aliases=("aws", "aws-bedrock", "amazon-bedrock", "amazon"),
        api_mode="bedrock_converse",
        runtime_status="needs_adapter",
        auth_type="aws_sdk",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        base_url_env_var="BEDROCK_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="copilot",
        display_name="GitHub Copilot",
        aliases=("github-copilot", "github-models", "github-model", "github"),
        api_mode="external_process",
        runtime_status="needs_adapter",
        auth_type="copilot",
        api_key_env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        base_url="https://api.githubcopilot.com",
        base_url_env_var="COPILOT_API_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="copilot-acp",
        display_name="GitHub Copilot ACP",
        aliases=("github-copilot-acp", "copilot-acp-agent"),
        api_mode="external_process",
        runtime_status="needs_adapter",
        auth_type="external_process",
        base_url="acp://copilot",
        base_url_env_var="COPILOT_ACP_BASE_URL",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="custom",
        display_name="Custom OpenAI-Compatible Endpoint",
        aliases=("vllm", "llamacpp", "llama.cpp", "llama-cpp"),
        auth_type="none",
    )
    _register(
        register_provider,
        ProviderProfile,
        name="minimax",
        display_name="MiniMax",
        aliases=("mini-max",),
        api_key_env_vars=("MINIMAX_API_KEY",),
        base_url="https://api.minimax.io/v1",
        base_url_env_var="MINIMAX_BASE_URL",
        metadata={"thinking": _THINKING_CONFIG_BY_PROVIDER["minimax"]},
        models=("MiniMax-M2.7", "MiniMax-M2.7-highspeed", "MiniMax-M2.5"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="minimax-cn",
        display_name="MiniMax (China)",
        aliases=("minimax-china", "minimax_cn"),
        api_key_env_vars=("MINIMAX_CN_API_KEY",),
        base_url="https://api.minimaxi.com/v1",
        base_url_env_var="MINIMAX_CN_BASE_URL",
        metadata={"thinking": _THINKING_CONFIG_BY_PROVIDER["minimax-cn"]},
        models=("MiniMax-M2.7", "MiniMax-M2.7-highspeed", "MiniMax-M2.5"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="google-gemini-cli",
        display_name="Google Gemini (OAuth)",
        aliases=("gemini-cli", "gemini-oauth"),
        api_mode="chat_completions",
        runtime_status="ready",
        auth_type="oauth_external",
        base_url="cloudcode-pa://google",
        models=("gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-pro"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="minimax-oauth",
        display_name="MiniMax (OAuth)",
        aliases=("minimax_oauth", "minimax-oauth-io"),
        api_mode="anthropic_messages",
        runtime_status="ready",
        auth_type="oauth_external",
        base_url="https://api.minimax.io/anthropic",
        signup_url="https://api.minimax.io/",
        models=("MiniMax-M2.7-highspeed", "MiniMax-M2.7"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="nous",
        display_name="Nous Research",
        aliases=("nous-portal", "nousresearch"),
        runtime_status="ready",
        auth_type="oauth_device_code",
        api_key_env_vars=("NOUS_API_KEY",),
        base_url="https://inference.nousresearch.com/v1",
        signup_url="https://nousresearch.com/",
        models=("hermes-3-405b", "hermes-3-70b"),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="openai-codex",
        display_name="OpenAI Codex",
        aliases=("codex", "openai_codex"),
        api_mode="codex_responses",
        runtime_status="ready",
        auth_type="oauth_device_code",
        base_url="https://chatgpt.com/backend-api/codex",
        metadata={"thinking": _THINKING_CONFIG_BY_PROVIDER["openai-codex"]},
        quick_models=_models(*_CODEX_MODELS),
        deep_models=_models(*_CODEX_MODELS),
    )
    _register(
        register_provider,
        ProviderProfile,
        name="qwen-oauth",
        display_name="Qwen Portal",
        aliases=("qwen-portal", "qwen-cli"),
        runtime_status="ready",
        auth_type="oauth_external",
        api_key_env_vars=("QWEN_API_KEY",),
        base_url="https://portal.qwen.ai/v1",
        models=("qwen3-coder-plus", "qwen-plus"),
    )


def _register(
    register_provider,
    ProviderProfile,
    *,
    name: str,
    display_name: str | None = None,
    aliases: tuple[str, ...] = (),
    api_mode: str = "chat_completions",
    runtime_status: str = "ready",
    auth_type: str = "api_key",
    api_key_env_vars: tuple[str, ...] = (),
    base_url: str | None = None,
    base_url_env_var: str | None = None,
    signup_url: str | None = None,
    models: tuple[str, ...] = (),
    quick_models: tuple[tuple[str, str], ...] | None = None,
    deep_models: tuple[tuple[str, str], ...] | None = None,
    metadata: dict | None = None,
) -> None:
    """Helper to register a provider."""
    resolved_models = _models(*models)
    profile_kwargs = dict(
        name=name,
        display_name=display_name or name,
        aliases=aliases,
        api_mode=api_mode,
        runtime_status=runtime_status,
        auth_type=auth_type,
        api_key_env_vars=api_key_env_vars,
        base_url=base_url,
        base_url_env_var=base_url_env_var,
        signup_url=signup_url,
        quick_models=quick_models or resolved_models,
        deep_models=deep_models or resolved_models,
        metadata=metadata or {},
    )
    try:
        profile = ProviderProfile(**profile_kwargs)
    except TypeError as exc:
        if "metadata" not in str(exc):
            raise
        profile_kwargs.pop("metadata", None)
        profile = ProviderProfile(**profile_kwargs)

    register_provider(profile)
