"""
Fresh Install Test

This test verifies that the provider package works correctly after
a fresh installation, simulating what happens when a user runs:

    pip install tradingagents-providers

The test checks:
1. Entry points are properly registered
2. Providers can be registered and resolved
3. CLI commands are available
"""


def test_entry_point_registration():
    """Test that entry points are properly configured."""
    # Note: Entry points are only available after pip install
    # This test verifies the pyproject.toml has the correct entry point configuration
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    content = pyproject.read_text()

    # Verify entry point is defined in pyproject.toml
    assert "tradingagents.model_providers" in content
    assert 'catalog = "tradingagents_providers.catalog:register"' in content
    assert "tradingagents_providers_autoactivate.pth" in content

    pth = Path(__file__).parent.parent / "tradingagents_providers.pth"
    executable_lines = [
        line for line in pth.read_text().splitlines() if line.startswith("import ")
    ]
    assert executable_lines == [
        "import tradingagents_providers.bootstrap as _tap_bootstrap; _tap_bootstrap.apply_bootstrap()"
    ]


def test_catalog_registers_providers():
    """Test that calling register() adds providers."""
    # Clear any existing state
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    # Import and register
    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import get_provider_names

    # Initially empty
    register()

    names = get_provider_names()
    assert len(names) > 0, "Expected providers to be registered"

    # Should include key providers
    assert "openai" in names
    assert "anthropic" in names
    assert "google" in names


def test_resolve_runtime_provider():
    """Test runtime provider resolution."""
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import resolve_runtime_provider

    register()

    runtime = resolve_runtime_provider("openai", model="gpt-4")
    assert runtime.provider == "openai"
    assert runtime.api_mode == "chat_completions"


def test_standalone_runtime_uses_default_base_url_when_override_is_unset(monkeypatch):
    """Standalone runtime must fall back to profile base URLs without core hooks."""
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import resolve_runtime_provider

    monkeypatch.delenv("XIAOMI_BASE_URL", raising=False)
    register()

    runtime = resolve_runtime_provider(
        "xiaomi",
        model="mimo-v2.5-pro",
        explicit_api_key="test-key",
    )

    assert runtime.base_url == "https://api.xiaomimimo.com/v1"
    assert runtime.api_key == "test-key"


def test_openai_codex_runtime_uses_saved_oauth_login(tmp_path, monkeypatch):
    """OpenAI Codex should resolve to the Responses API using saved OAuth."""
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    monkeypatch.setenv("TRADINGAGENTS_PROVIDERS_HOME", str(tmp_path))

    from tradingagents_providers.catalog import register
    from tradingagents_providers.oauth import save_provider_auth_state
    from tradingagents_providers.providers import resolve_runtime_provider

    register()
    save_provider_auth_state(
        "openai-codex",
        {
            "provider": "openai-codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "access_token": "codex-token",
            "refresh_token": "codex-refresh",
        },
    )

    runtime = resolve_runtime_provider("openai-codex", model="gpt-5.5")

    assert runtime.provider == "openai-codex"
    assert runtime.api_mode == "codex_responses"
    assert runtime.api_key == "codex-token"
    assert runtime.base_url == "https://chatgpt.com/backend-api/codex"


def test_google_gemini_cli_runtime_imports_gemini_cli_login(tmp_path, monkeypatch):
    """Google Gemini CLI OAuth should import standard Gemini CLI credentials."""
    import json
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    auth_home = tmp_path / "auth-home"
    gemini_creds = tmp_path / "oauth_creds.json"
    gemini_creds.write_text(
        json.dumps(
            {
                "access_token": "google-token",
                "refresh_token": "google-refresh",
                "expiry_date": 9999999999999,
            }
        )
    )
    monkeypatch.setenv("TRADINGAGENTS_PROVIDERS_HOME", str(auth_home))
    monkeypatch.setenv("GEMINI_OAUTH_CREDS_PATH", str(gemini_creds))

    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import resolve_runtime_provider

    register()
    runtime = resolve_runtime_provider("google-gemini-cli", model="gemini-2.5-flash")

    assert runtime.provider == "google-gemini-cli"
    assert runtime.api_mode == "chat_completions"
    assert runtime.api_key == "google-token"
    assert runtime.base_url == "cloudcode-pa://google"


def test_codex_client_lifts_system_messages_to_instructions():
    """Codex backend rejects system messages in Responses input."""
    import pytest

    pytest.importorskip("langchain_openai")

    from langchain_core.messages import HumanMessage, SystemMessage
    from tradingagents_providers.codex_client import CodexResponsesChatOpenAI

    llm = CodexResponsesChatOpenAI(
        model="gpt-5.4-mini",
        api_key="test-key",
        base_url="https://example.test",
        use_responses_api=True,
        streaming=True,
    )
    payload = llm._get_request_payload(
        [SystemMessage(content="System rules"), HumanMessage(content="Hello")]
    )

    assert payload["instructions"] == "System rules"
    assert all(item.get("role") != "system" for item in payload["input"])
    assert payload["input"][0]["role"] == "user"


def test_needs_adapter_raises():
    """Test that providers with needs_adapter raise correctly."""
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import (
        resolve_runtime_provider,
        NeedsAdapterError,
    )

    register()

    try:
        resolve_runtime_provider("bedrock")
        assert False, "Should have raised NeedsAdapterError"
    except NeedsAdapterError as e:
        assert "bedrock" in str(e).lower()


def test_provider_status_counts():
    """Test that provider status counts match expected values."""
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import list_provider_profiles

    register()

    profiles = list_provider_profiles()

    ready = sum(1 for p in profiles if p.runtime_status == "ready")
    needs_adapter = sum(1 for p in profiles if p.runtime_status == "needs_adapter")
    needs_oauth = sum(1 for p in profiles if p.runtime_status == "needs_oauth")

    assert ready == 34
    assert needs_adapter == 3
    assert needs_oauth == 0


def test_register_sets_tradingagents_extension_hooks(monkeypatch):
    """Entry point registration should wire core runtime hooks."""
    import pytest

    pytest.importorskip("tradingagents.ext_loader")
    import tradingagents.ext_loader as ext_loader

    ext_loader._factory_resolver = None
    ext_loader._model_catalog_hook = None
    ext_loader._cli_command_hook = None

    from tradingagents_providers.catalog import register

    register()

    assert ext_loader.get_factory_resolver() is not None
    assert ext_loader.get_model_catalog_hook() is not None
    assert ext_loader.get_cli_command_hook() is not None


def test_factory_hook_handles_plugin_provider_and_forwards_api_key(monkeypatch):
    """TradingAgents factory should route installed plugin providers."""
    import pytest

    pytest.importorskip("tradingagents.ext_loader")
    import tradingagents.ext_loader as ext_loader
    import tradingagents_providers.catalog as catalog

    ext_loader._factory_resolver = None
    catalog.register()

    monkeypatch.setattr(
        catalog,
        "_create_openai_compatible_client",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        catalog,
        "_create_codex_responses_client",
        lambda **kwargs: kwargs,
    )

    from tradingagents.llm_clients.factory import create_llm_client

    client = create_llm_client(
        "xiaomi",
        "mimo-v2.5-pro",
        api_key="test-key",
        reasoning_effort="high",
    )

    assert client["provider"] == "xiaomi"
    assert client["api_key"] == "test-key"
    assert client["base_url"] == "https://api.xiaomimimo.com/v1"
    assert client["reasoning_effort"] == "high"

    minimax_client = create_llm_client("minimax", "MiniMax-M2.7", api_key="mm-key")

    assert minimax_client["provider"] == "minimax"
    assert minimax_client["api_key"] == "mm-key"
    assert minimax_client["base_url"] == "https://api.minimax.io/v1"


def test_oauth_runtime_provider_uses_resolved_credentials(monkeypatch):
    """Login-backed providers should resolve OAuth credentials at runtime."""
    import tradingagents_providers.oauth as oauth

    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import resolve_runtime_provider

    register()
    monkeypatch.setattr(
        oauth,
        "resolve_oauth_runtime_credentials",
        lambda provider: {
            "provider": provider,
            "api_key": "oauth-token",
            "base_url": "https://oauth.example.test/v1",
            "source": "oauth-test",
        },
    )

    runtime = resolve_runtime_provider("qwen-oauth", model="qwen3-coder-plus")

    assert runtime.provider == "qwen-oauth"
    assert runtime.api_key == "oauth-token"
    assert runtime.base_url == "https://oauth.example.test/v1"
    assert runtime.source == "oauth-test"


def test_anthropic_runtime_receives_oauth_base_url(monkeypatch):
    """MiniMax OAuth should pass its Anthropic-compatible base URL to the client."""
    import tradingagents_providers.catalog as catalog
    import tradingagents_providers.oauth as oauth

    catalog.register()
    monkeypatch.setattr(
        oauth,
        "resolve_oauth_runtime_credentials",
        lambda provider: {
            "provider": provider,
            "api_key": "mm-oauth-token",
            "base_url": "https://api.minimax.io/anthropic",
            "source": "oauth-test",
        },
    )
    monkeypatch.setattr(catalog, "_create_anthropic_client", lambda **kwargs: kwargs)

    client = catalog._factory_resolver("minimax-oauth", "MiniMax-M2.7")

    assert client["api_key"] == "mm-oauth-token"
    assert client["base_url"] == "https://api.minimax.io/anthropic"


def test_model_catalog_hook_handles_plugin_provider():
    """Core model catalog should consult installed provider hooks."""
    import pytest

    pytest.importorskip("tradingagents.ext_loader")
    import tradingagents.ext_loader as ext_loader

    ext_loader._model_catalog_hook = None

    from tradingagents_providers.catalog import register
    from tradingagents.llm_clients.model_catalog import get_model_options

    register()

    options = get_model_options("xiaomi", "quick")
    assert options
    assert ("mimo-v2.5-pro", "mimo-v2.5-pro") in options
    assert options[-1] == ("Custom model ID", "custom")


def test_bootstrap_patches_upstream_style_factory(tmp_path, monkeypatch):
    """The bootstrap factory patch should route plugin providers and fail loudly."""
    from types import ModuleType

    import tradingagents_providers.catalog as catalog
    import tradingagents_providers.bootstrap as bootstrap
    from tradingagents_providers.oauth import save_provider_auth_state

    module = ModuleType("tradingagents.llm_clients.factory")
    module.create_llm_client = lambda provider, model, base_url=None, **kwargs: {
        "provider": provider,
        "model": model,
        "fallback": True,
        **kwargs,
    }
    monkeypatch.setattr(
        catalog,
        "_create_openai_compatible_client",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        catalog,
        "_create_codex_responses_client",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setenv("TRADINGAGENTS_PROVIDERS_HOME", str(tmp_path))
    save_provider_auth_state(
        "openai-codex",
        {
            "provider": "openai-codex",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "access_token": "codex-token",
            "refresh_token": "codex-refresh",
        },
    )

    bootstrap._patch_factory(module)
    client = module.create_llm_client("xiaomi", "mimo-v2.5-pro", api_key="test-key")

    assert client["provider"] == "xiaomi"
    assert client["api_key"] == "test-key"
    assert client["base_url"] == "https://api.xiaomimimo.com/v1"

    fallback = module.create_llm_client("not-registered", "custom", api_key="key")
    assert fallback["fallback"] is True

    codex = module.create_llm_client("openai-codex", "gpt-5.3-codex")
    assert codex["api_key"] == "codex-token"
    assert codex["base_url"] == "https://chatgpt.com/backend-api/codex"


def test_bootstrap_patches_upstream_style_catalog():
    """Model catalog hook should return plugin models."""
    import tradingagents_providers.catalog as catalog

    # The model catalog hook should return models for xiaomi
    result = catalog._model_catalog_hook("xiaomi", "quick")
    assert result is not None
    assert any("mimo" in str(m).lower() for m in result)

    codex_quick = catalog._model_catalog_hook("openai-codex", "quick")
    codex_deep = catalog._model_catalog_hook("openai-codex", "deep")
    assert codex_quick == codex_deep
    assert codex_quick[0] == ("gpt-5.5", "gpt-5.5")
    assert ("gpt-5.4-mini", "gpt-5.4-mini") in codex_quick
    assert ("gpt-5.3-codex-spark", "gpt-5.3-codex-spark") in codex_quick
    assert ("gpt-5.3-codex", "gpt-5.3-codex") in codex_deep
    assert ("gpt-5.2-codex", "gpt-5.2-codex") in codex_deep
    assert ("gpt-5.1-codex-mini", "gpt-5.1-codex-mini") in codex_deep


def test_bootstrap_patches_cli_provider_picker(tmp_path, monkeypatch):
    """The upstream LLM provider picker should include runnable catalog providers."""
    import sys
    from types import ModuleType, SimpleNamespace

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    import tradingagents_providers.bootstrap as bootstrap

    monkeypatch.setenv("TRADINGAGENTS_PROVIDERS_HOME", str(tmp_path))
    captured = {}
    module = ModuleType("cli.utils")

    class Choice:
        def __init__(self, title, value):
            self.title = title
            self.value = value

    def select(message, choices, **kwargs):
        captured["message"] = message
        captured["choices"] = choices
        return SimpleNamespace(ask=lambda: choices[-1].value)

    module.questionary = SimpleNamespace(
        Choice=Choice,
        Style=lambda value: value,
        select=select,
    )
    module.get_api_key_env = lambda provider: None
    module.get_model_options = lambda provider, mode: []

    bootstrap._patch_cli_utils(module)
    selected_provider, selected_url = module.select_llm_provider()
    providers = {choice.value[0]: choice.title for choice in captured["choices"]}
    from tradingagents_providers.catalog import register
    from tradingagents_providers.providers import list_provider_profiles

    register()
    selectable_catalog_names = {
        profile.name
        for profile in list_provider_profiles()
        if bootstrap._provider_is_selectable_for_analysis(profile)
        if profile.api_mode
        in {
            "chat_completions",
            "anthropic_messages",
                "google_native",
                "azure_openai",
                "bedrock_converse",
                "codex_responses",
                "external_process",
            }
        }

    assert captured["message"] == "Select your LLM Provider:"
    assert selectable_catalog_names.issubset(providers)
    assert providers["xiaomi"] == "Xiaomi MiMo"
    assert providers["minimax"] == "MiniMax (Global)"
    assert providers["minimax-cn"] == "MiniMax (China)"
    assert "minimax-oauth" not in providers
    assert "openai-codex" not in providers
    assert "google-gemini-cli" not in providers
    assert selected_provider in providers
    assert selected_url is None or isinstance(selected_url, str)


def test_provider_picker_prioritizes_logged_in_runnable_oauth_provider(
    tmp_path,
    monkeypatch,
):
    """A saved OAuth login should be visible before the plain OpenAI option."""
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    import tradingagents_providers.bootstrap as bootstrap
    from tradingagents_providers.oauth import save_provider_auth_state

    monkeypatch.setenv("TRADINGAGENTS_PROVIDERS_HOME", str(tmp_path))
    save_provider_auth_state(
        "nous",
        {
            "provider": "nous",
            "access_token": "nous-token",
            "refresh_token": "nous-refresh",
        },
    )

    options = bootstrap._provider_picker_options()

    assert options[0][1] == "nous"
    assert options[0][0] == "Nous Research (logged in)"
    assert all(option[1] != "openai-codex" for option in options)
    assert ("OpenAI", "openai", "https://api.openai.com/v1") in options


def test_provider_picker_includes_logged_in_openai_codex(tmp_path, monkeypatch):
    """Logged-in OpenAI Codex should be selectable now that it has an adapter."""
    import sys

    for mod in list(sys.modules.keys()):
        if "tradingagents_providers" in mod:
            del sys.modules[mod]

    import tradingagents_providers.bootstrap as bootstrap
    from tradingagents_providers.oauth import save_provider_auth_state

    monkeypatch.setenv("TRADINGAGENTS_PROVIDERS_HOME", str(tmp_path))
    save_provider_auth_state(
        "openai-codex",
        {
            "provider": "openai-codex",
            "access_token": "codex-token",
            "refresh_token": "codex-refresh",
        },
    )

    options = bootstrap._provider_picker_options()

    assert options[0][1] == "openai-codex"
    assert options[0][0] == "OpenAI Codex (logged in)"


def test_cli_selections_append_plugin_thinking_level(monkeypatch):
    """Plugin provider thinking params should join the original analysis flow."""
    import os
    from types import ModuleType, SimpleNamespace

    import tradingagents_providers.bootstrap as bootstrap

    module = ModuleType("cli.main")
    module.get_user_selections = lambda: {"llm_provider": "xiaomi"}
    module.create_question_box = lambda title, body: f"{title}: {body}"
    module.console = SimpleNamespace(print=lambda value: None)

    class Choice:
        def __init__(self, title, value):
            self.title = title
            self.value = value

    module.questionary = SimpleNamespace(
        Choice=Choice,
        Style=lambda value: value,
        select=lambda *args, **kwargs: SimpleNamespace(ask=lambda: "high"),
    )
    monkeypatch.delenv("XIAOMI_THINKING_LEVEL", raising=False)

    bootstrap._patch_cli_get_user_selections(module)
    selections = module.get_user_selections()

    assert selections["xiaomi_thinking_level"] == "high"
    assert os.environ["XIAOMI_THINKING_LEVEL"] == "high"


def test_cli_selections_append_codex_thinking_level(monkeypatch):
    """Codex should expose the same low/medium/high thinking prompt."""
    import os
    from types import ModuleType, SimpleNamespace

    import tradingagents_providers.bootstrap as bootstrap

    module = ModuleType("cli.main")
    module.get_user_selections = lambda: {"llm_provider": "openai-codex"}
    module.create_question_box = lambda title, body: f"{title}: {body}"
    module.console = SimpleNamespace(print=lambda value: None)

    class Choice:
        def __init__(self, title, value):
            self.title = title
            self.value = value

    module.questionary = SimpleNamespace(
        Choice=Choice,
        Style=lambda value: value,
        select=lambda *args, **kwargs: SimpleNamespace(ask=lambda: "low"),
    )
    monkeypatch.delenv("OPENAI_CODEX_THINKING_LEVEL", raising=False)

    bootstrap._patch_cli_get_user_selections(module)
    selections = module.get_user_selections()

    assert selections["openai_codex_thinking_level"] == "low"
    assert os.environ["OPENAI_CODEX_THINKING_LEVEL"] == "low"


def test_thinking_level_metadata_and_resolution(monkeypatch):
    """Xiaomi and MiniMax should expose TradingAgents thinking kwargs."""
    from tradingagents_providers.catalog import (
        get_provider_thinking_config,
        get_thinking_level_kwargs,
        register,
    )

    register()

    xiaomi = get_provider_thinking_config("xiaomi")
    minimax = get_provider_thinking_config("minimax")
    codex = get_provider_thinking_config("openai-codex")

    assert xiaomi["config_key"] == "xiaomi_thinking_level"
    assert minimax["config_key"] == "minimax_thinking_level"
    assert codex["config_key"] == "openai_codex_thinking_level"
    assert get_thinking_level_kwargs(
        "xiaomi",
        {"xiaomi_thinking_level": "xhigh"},
    ) == {"reasoning_effort": "high"}
    assert get_thinking_level_kwargs(
        "openai-codex",
        {"openai_codex_thinking_level": "low"},
    ) == {"reasoning_effort": "low"}

    monkeypatch.setenv("MINIMAX_THINKING_LEVEL", "minimal")
    assert get_thinking_level_kwargs("minimax", {}) == {"reasoning_effort": "low"}


def test_bootstrap_patches_trading_graph_provider_kwargs(monkeypatch):
    """Unmodified upstream graphs should receive plugin thinking kwargs."""
    from types import ModuleType

    import tradingagents_providers.bootstrap as bootstrap

    module = ModuleType("tradingagents.graph.trading_graph")

    class TradingAgentsGraph:
        def __init__(self, config):
            self.config = config

        def _get_provider_kwargs(self):
            return {"existing": "value"}

    module.TradingAgentsGraph = TradingAgentsGraph

    bootstrap._patch_trading_graph(module)
    graph = module.TradingAgentsGraph(
        {"llm_provider": "xiaomi", "xiaomi_thinking_level": "medium"}
    )

    assert graph._get_provider_kwargs() == {
        "existing": "value",
        "reasoning_effort": "medium",
    }


def test_bootstrap_keeps_graph_patch_with_official_hooks(monkeypatch):
    """Official hook installs still need runtime compatibility patches."""
    import sys

    import tradingagents_providers.bootstrap as bootstrap

    monkeypatch.delattr(sys, bootstrap._INSTALL_FLAG, raising=False)
    monkeypatch.setattr(bootstrap, "_has_official_extension_loader", lambda: True)

    before = list(sys.meta_path)
    bootstrap.install()

    try:
        finder = sys.meta_path[0]
        assert finder._patch_targets == {
            "tradingagents.graph.trading_graph",
            "tradingagents.llm_clients.openai_client",
            "cli.main",
            "cli.utils",
        }
    finally:
        sys.meta_path[:] = before
        monkeypatch.delattr(sys, bootstrap._INSTALL_FLAG, raising=False)


def test_bootstrap_patches_minimax_reasoning_split_payload():
    """MiniMax provider-specific fields should go through OpenAI extra_body."""
    from types import ModuleType

    import tradingagents_providers.bootstrap as bootstrap

    module = ModuleType("tradingagents.llm_clients.openai_client")

    class MinimaxChatOpenAI:
        def _get_request_payload(self, input_, *, stop=None, **kwargs):
            payload = dict(kwargs)
            payload.setdefault("reasoning_split", True)
            return payload

    class OpenAIClient:
        def get_llm(self):
            return "llm"

    module.MinimaxChatOpenAI = MinimaxChatOpenAI
    module.OpenAIClient = OpenAIClient
    module.get_api_key_env = lambda provider: None

    bootstrap._patch_openai_client(module)

    payload = module.MinimaxChatOpenAI()._get_request_payload(["hi"])
    assert payload["extra_body"]["reasoning_split"] is True
    assert "reasoning_split" not in payload

    payload = module.MinimaxChatOpenAI()._get_request_payload(
        ["hi"],
        reasoning_split=False,
    )
    assert payload["extra_body"]["reasoning_split"] is False
    assert "reasoning_split" not in payload


def test_bootstrap_patches_upstream_style_api_key_env():
    """API-key lookup should work for plugin providers."""
    from tradingagents_providers.providers import get_provider_profile

    profile = get_provider_profile("xiaomi")
    assert profile is not None
    assert profile.api_key_env_vars == ("XIAOMI_API_KEY",)


def test_cli_hook_registers_provider_and_auth_commands():
    """The official CLI hook should register both providers and auth commands."""
    from typer.testing import CliRunner
    import typer

    from tradingagents_providers.catalog import _cli_command_hook

    app = typer.Typer()
    _cli_command_hook(app)
    runner = CliRunner()

    result = runner.invoke(app, ["providers", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "doctor" in result.output

    result = runner.invoke(app, ["auth", "--help"])
    assert result.exit_code == 0
    assert "add" in result.output
    assert "remove" in result.output


def test_cli_hook_patches_loaded_provider_picker(tmp_path, monkeypatch):
    """Official CLI hooks should patch the original provider picker too."""
    import sys
    from types import ModuleType, SimpleNamespace

    import typer

    from tradingagents_providers.catalog import _cli_command_hook
    monkeypatch.setenv("TRADINGAGENTS_PROVIDERS_HOME", str(tmp_path))

    cli_pkg = ModuleType("cli")
    cli_utils = ModuleType("cli.utils")
    cli_main = ModuleType("cli.main")
    captured = {}

    class Choice:
        def __init__(self, title, value):
            self.title = title
            self.value = value

    def select(message, choices, **kwargs):
        captured["message"] = message
        captured["choices"] = choices
        return SimpleNamespace(ask=lambda: choices[-1].value)

    cli_utils.questionary = SimpleNamespace(
        Choice=Choice,
        Style=lambda value: value,
        select=select,
    )
    cli_utils.select_llm_provider = lambda: ("openai", "https://api.openai.com/v1")
    cli_main.app = typer.Typer()
    cli_main.select_llm_provider = cli_utils.select_llm_provider

    monkeypatch.setitem(sys.modules, "cli", cli_pkg)
    monkeypatch.setitem(sys.modules, "cli.utils", cli_utils)
    monkeypatch.setitem(sys.modules, "cli.main", cli_main)

    _cli_command_hook(cli_main.app)
    selected_provider, _ = cli_main.select_llm_provider()
    providers = {choice.value[0]: choice.title for choice in captured["choices"]}

    assert captured["message"] == "Select your LLM Provider:"
    assert "xiaomi" in providers
    assert "openai-codex" not in providers
    assert selected_provider in providers


def test_bootstrap_cli_patch_preserves_bare_tradingagents():
    """Adding plugin subcommands should not break bare TradingAgents startup."""
    from types import ModuleType

    from typer.testing import CliRunner
    import typer

    import tradingagents_providers.bootstrap as bootstrap

    module = ModuleType("cli.main")
    module.app = typer.Typer()
    calls = []

    def analyze(checkpoint=False, clear_checkpoints=False):
        calls.append(
            {"checkpoint": checkpoint, "clear_checkpoints": clear_checkpoints}
        )

    module.analyze = analyze

    bootstrap._patch_cli_main(module)
    runner = CliRunner()

    result = runner.invoke(module.app, [])
    assert result.exit_code == 0
    assert calls == [{"checkpoint": False, "clear_checkpoints": False}]

    result = runner.invoke(module.app, ["--checkpoint"])
    assert result.exit_code == 0
    assert calls[-1] == {"checkpoint": True, "clear_checkpoints": False}

    result = runner.invoke(module.app, ["providers", "--help"])
    assert result.exit_code == 0
    result = runner.invoke(module.app, ["auth", "--help"])
    assert result.exit_code == 0


def test_auth_commands_save_and_report_api_key(tmp_path):
    """Hermes-style auth commands should configure API-key providers."""
    from typer.testing import CliRunner

    from tradingagents_providers.cli import auth_app

    runner = CliRunner()
    env_file = tmp_path / ".env"

    result = runner.invoke(
        auth_app,
        [
            "add",
            "xiaomi",
            "--key",
            "test-xiaomi-key",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0, f"Expected 0, got {result.exit_code}: {result.output}"
    assert "Saved XIAOMI_API_KEY" in result.output
    assert env_file.read_text() == "XIAOMI_API_KEY=test-xiaomi-key\n"

    result = runner.invoke(
        auth_app,
        ["list", "xiaomi", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "xiaomi (1 credentials)" in result.output
    assert "XIAOMI_API_KEY" in result.output

    result = runner.invoke(
        auth_app,
        ["remove", "xiaomi", "1", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0
    assert "Removed xiaomi credential XIAOMI_API_KEY" in result.output
    assert env_file.read_text() == ""


def test_auth_picker_lists_api_key_and_login_capable_providers():
    """Interactive auth should advertise API-key and implemented login flows."""
    from tradingagents_providers.cli import _known_auth_provider_names

    names = _known_auth_provider_names()

    assert "xiaomi" in names
    assert "minimax" in names
    assert "openai-codex" in names
    assert "nous" in names
    assert "google-gemini-cli" in names


def test_auth_oauth_provider_runs_login_flow(tmp_path, monkeypatch):
    """Typing an OAuth provider should dispatch to its login implementation."""
    from typer.testing import CliRunner

    import tradingagents_providers.cli as cli
    from tradingagents_providers.cli import auth_app

    runner = CliRunner()
    env_file = tmp_path / ".env"
    calls = []

    def fake_login(provider, **kwargs):
        calls.append((provider, kwargs))
        return {"provider": provider, "base_url": "https://example.test", "source": "oauth"}

    monkeypatch.setattr(cli, "login_provider", fake_login)

    result = runner.invoke(
        auth_app,
        [
            "add",
            "openai-codex",
            "--type",
            "oauth",
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert calls and calls[0][0] == "openai-codex"
    assert "Logged in to openai-codex." in result.output
    assert "runtime adapter" not in result.output


def test_thinking_level_cli_saves_provider_setting(tmp_path):
    """CLI should persist thinking levels for Xiaomi and MiniMax providers."""
    from typer.testing import CliRunner

    from tradingagents_providers.cli import providers_app

    runner = CliRunner()
    env_file = tmp_path / ".env"

    result = runner.invoke(
        providers_app,
        ["thinking-level", "xiaomi", "xhigh", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0, result.output
    assert "Saved XIAOMI_THINKING_LEVEL=high" in result.output
    assert env_file.read_text() == "XIAOMI_THINKING_LEVEL=high\n"

    result = runner.invoke(
        providers_app,
        ["thinking-level", "minimax", "minimal", "--env-file", str(env_file)],
    )

    assert result.exit_code == 0, result.output
    assert "Saved MINIMAX_THINKING_LEVEL=low" in result.output
    assert "MINIMAX_THINKING_LEVEL=low" in env_file.read_text()


def test_bare_auth_opens_interactive_setup(tmp_path, monkeypatch):
    """Bare auth should open the Hermes-style interactive setup flow."""
    from typer.testing import CliRunner

    from tradingagents_providers.cli import auth_app

    monkeypatch.delenv("XIAOMI_API_KEY", raising=False)
    runner = CliRunner()
    env_file = tmp_path / ".env"

    result = runner.invoke(
        auth_app,
        ["--env-file", str(env_file)],
        input="1\nxiaomi\n\ntest-xiaomi-key\n",
    )

    assert result.exit_code == 0
    assert "Credential Pool Status" in result.output
    assert "Add a credential" in result.output
    assert "Provider to add credential for" in result.output
    assert 'Added xiaomi credential #1: "api-key-1"' in result.output
    assert env_file.read_text() == "XIAOMI_API_KEY=test-xiaomi-key\n"


def test_bootstrap_works():
    """Bootstrap module should be importable and have correct structure."""
    from tradingagents_providers.bootstrap import (
        apply_bootstrap,
        is_official_hooks_available,
        is_bootstrap_applied,
    )

    # Module should have correct functions
    assert callable(apply_bootstrap)
    assert callable(is_official_hooks_available)
    assert callable(is_bootstrap_applied)


if __name__ == "__main__":
    # Run tests manually
    test_entry_point_registration()
    print("✓ Entry point registration")

    test_catalog_registers_providers()
    print("✓ Catalog registers providers")

    test_resolve_runtime_provider()
    print("✓ Runtime provider resolution")

    test_needs_adapter_raises()
    print("✓ NeedsAdapterError raised correctly")

    test_provider_status_counts()
    print("✓ Provider status counts")

    print("\nAll tests passed!")
