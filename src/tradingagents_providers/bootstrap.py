"""Runtime compatibility bootstrap for unmodified TradingAgents installs.

TradingAgents upstream does not yet expose provider extension hooks.  This
module is intentionally small and lazy: the installed ``.pth`` file imports
only this module, and this module patches TradingAgents modules only after
those modules are imported by the application.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
from types import ModuleType
from typing import Callable

_PATCH_TARGETS = {
    "tradingagents.llm_clients.factory",
    "tradingagents.llm_clients.model_catalog",
    "tradingagents.llm_clients.api_key_env",
    "tradingagents.llm_clients.openai_client",
    "tradingagents.graph.trading_graph",
    "cli.main",
    "cli.utils",
}
_INSTALL_FLAG = "_tradingagents_providers_bootstrap_installed"
_PATCH_FLAG = "_tradingagents_providers_patched"
_CLI_ROOT_CALLBACK_FLAG = "_tradingagents_providers_root_callback_patched"
_CLI_SELECTIONS_FLAG = "_tradingagents_providers_selections_patched"


def install() -> None:
    """Install the import hook that patches TradingAgents compatibility points."""
    if getattr(sys, _INSTALL_FLAG, False):
        return

    patch_targets = (
        {
            "tradingagents.graph.trading_graph",
            "tradingagents.llm_clients.openai_client",
            "cli.main",
            "cli.utils",
        }
        if _has_official_extension_loader()
        else _PATCH_TARGETS
    )
    if not patch_targets:
        return

    setattr(sys, _INSTALL_FLAG, True)

    finder = _TradingAgentsProvidersFinder(patch_targets)
    sys.meta_path.insert(0, finder)

    for module_name in list(patch_targets):
        module = sys.modules.get(module_name)
        if module is not None:
            _patch_module(module_name, module)


def apply_bootstrap() -> bool:
    """Apply the compatibility bootstrap.

    This is the public entry point used by the packaged ``.pth`` file. It is a
    boolean wrapper around ``install()`` so tests and smoke checks can tell
    whether activation was attempted in this process.
    """
    was_installed = getattr(sys, _INSTALL_FLAG, False)
    install()
    return not was_installed and getattr(sys, _INSTALL_FLAG, False)


def is_bootstrap_applied() -> bool:
    """Return whether the bootstrap import hook has been installed."""
    return bool(getattr(sys, _INSTALL_FLAG, False))


def is_official_hooks_available() -> bool:
    """Return whether TradingAgents exposes official extension hooks."""
    return _has_official_extension_loader()


def get_patched_modules() -> list[str]:
    """Return already-imported TradingAgents modules patched by this bootstrap."""
    return [
        module_name
        for module_name in sorted(_PATCH_TARGETS)
        if getattr(sys.modules.get(module_name), _PATCH_FLAG, False)
    ]


def patch_loaded_cli_modules(app=None) -> None:
    """Patch already-imported TradingAgents CLI modules.

    This is used by official TradingAgents CLI hooks. In that path the plugin can
    be discovered through entry points even when the wheel's .pth bootstrap did
    not run, so command registration alone is not enough.
    """
    cli_utils = sys.modules.get("cli.utils")
    if cli_utils is not None:
        _patch_cli_utils(cli_utils)

    cli_main = sys.modules.get("cli.main")
    if cli_main is None:
        return
    if app is not None and getattr(cli_main, "app", None) is None:
        cli_main.app = app
    _patch_imported_cli_utils_functions(cli_main)
    _patch_cli_get_user_selections(cli_main)


def _has_official_extension_loader() -> bool:
    try:
        return importlib.util.find_spec("tradingagents.ext_loader") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


class _TradingAgentsProvidersFinder(importlib.abc.MetaPathFinder):
    """Wrap selected TradingAgents loaders so modules are patched after import."""

    _resolving = False

    def __init__(self, patch_targets: set[str] | None = None):
        self._patch_targets = patch_targets or _PATCH_TARGETS

    def find_spec(self, fullname: str, path, target=None):
        if fullname not in self._patch_targets or self._resolving:
            return None

        self._resolving = True
        try:
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            self._resolving = False

        if spec is None or spec.loader is None:
            return spec

        spec.loader = _PatchAfterLoad(spec.loader, fullname)
        return spec


class _PatchAfterLoad(importlib.abc.Loader):
    """Delegate loading, then patch the loaded module."""

    def __init__(self, wrapped_loader: importlib.abc.Loader, fullname: str):
        self._wrapped_loader = wrapped_loader
        self._fullname = fullname

    def create_module(self, spec):
        create_module = getattr(self._wrapped_loader, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self._wrapped_loader.exec_module(module)
        _patch_module(self._fullname, module)


def _patch_module(module_name: str, module: ModuleType) -> None:
    patchers: dict[str, Callable[[ModuleType], None]] = {
        "tradingagents.llm_clients.factory": _patch_factory,
        "tradingagents.llm_clients.model_catalog": _patch_model_catalog,
        "tradingagents.llm_clients.api_key_env": _patch_api_key_env,
        "tradingagents.llm_clients.openai_client": _patch_openai_client,
        "tradingagents.graph.trading_graph": _patch_trading_graph,
        "cli.main": _patch_cli_main,
        "cli.utils": _patch_cli_utils,
    }
    patcher = patchers.get(module_name)
    if patcher is not None:
        patcher(module)


def _register_catalog() -> None:
    from tradingagents_providers.catalog import register

    register()


def _patch_factory(module: ModuleType) -> None:
    if getattr(module, _PATCH_FLAG, False) or not hasattr(module, "create_llm_client"):
        return

    original_create_llm_client = module.create_llm_client

    def create_llm_client(provider: str, model: str, base_url=None, **kwargs):
        _register_catalog()

        from tradingagents_providers.catalog import _factory_resolver
        from tradingagents_providers.providers import get_provider_profile

        if get_provider_profile(provider) is not None:
            return _factory_resolver(provider, model, base_url, **kwargs)

        return original_create_llm_client(provider, model, base_url, **kwargs)

    module.create_llm_client = create_llm_client
    setattr(module, _PATCH_FLAG, True)


def _patch_model_catalog(module: ModuleType) -> None:
    if getattr(module, _PATCH_FLAG, False):
        return

    original_get_model_options = getattr(module, "get_model_options", None)
    original_get_known_models = getattr(module, "get_known_models", None)

    if original_get_model_options is not None:

        def get_model_options(provider: str, mode: str = "quick"):
            _register_catalog()

            from tradingagents_providers.providers import get_provider_profile

            profile = get_provider_profile(provider)
            if profile is not None:
                return list(
                    profile.deep_models if mode == "deep" else profile.quick_models
                )
            return original_get_model_options(provider, mode)

        module.get_model_options = get_model_options

    if original_get_known_models is not None:

        def get_known_models():
            known = dict(original_get_known_models())
            _register_catalog()

            from tradingagents_providers.providers import list_provider_profiles

            for profile in list_provider_profiles():
                values = {
                    value for _, value in profile.quick_models + profile.deep_models
                }
                known[profile.name] = sorted(values)
            return known

        module.get_known_models = get_known_models

    setattr(module, _PATCH_FLAG, True)


def _patch_api_key_env(module: ModuleType) -> None:
    if getattr(module, _PATCH_FLAG, False):
        return

    original_get_api_key_env = getattr(module, "get_api_key_env", None)

    def get_api_key_env(provider: str):
        _register_catalog()

        from tradingagents_providers.providers import get_provider_profile

        profile = get_provider_profile(provider)
        if profile is not None:
            if profile.auth_type == "none":
                return None
            if profile.api_key_env_vars:
                return profile.api_key_env_vars[0]
        if original_get_api_key_env is not None:
            return original_get_api_key_env(provider)
        return None

    module.get_api_key_env = get_api_key_env

    mapping = getattr(module, "PROVIDER_API_KEY_ENV", None)
    if isinstance(mapping, dict):
        _register_catalog()
        from tradingagents_providers.providers import list_provider_profiles

        for profile in list_provider_profiles():
            if profile.api_key_env_vars:
                mapping.setdefault(profile.name, profile.api_key_env_vars[0])

    setattr(module, _PATCH_FLAG, True)


def _patch_openai_client(module: ModuleType) -> None:
    if getattr(module, _PATCH_FLAG, False):
        return

    patched_client = _patch_openai_client_api_key_env(module)
    patched_minimax = _patch_minimax_reasoning_split(module)
    if patched_client or patched_minimax:
        setattr(module, _PATCH_FLAG, True)


def _patch_openai_client_api_key_env(module: ModuleType) -> bool:
    client_cls = getattr(module, "OpenAIClient", None)
    if client_cls is None or not hasattr(client_cls, "get_llm"):
        return False

    original_get_llm = client_cls.get_llm

    def get_llm(self):
        explicit_api_key = getattr(self, "kwargs", {}).get("api_key")
        provider = getattr(self, "provider", None)
        if not explicit_api_key or not provider:
            return original_get_llm(self)

        env_var = module.get_api_key_env(provider)
        if not env_var:
            return original_get_llm(self)

        import os

        previous = os.environ.get(env_var)
        os.environ[env_var] = explicit_api_key
        try:
            return original_get_llm(self)
        finally:
            if previous is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = previous

    client_cls.get_llm = get_llm
    return True


def _patch_minimax_reasoning_split(module: ModuleType) -> bool:
    chat_cls = getattr(module, "MinimaxChatOpenAI", None)
    flag = "_tradingagents_providers_reasoning_split_patched"
    if chat_cls is None or getattr(chat_cls, flag, False):
        return False

    original_get_request_payload = getattr(chat_cls, "_get_request_payload", None)
    if original_get_request_payload is None:
        return False

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = original_get_request_payload(self, input_, stop=stop, **kwargs)
        reasoning_split = payload.pop("reasoning_split", None)
        if reasoning_split is None:
            reasoning_split = True

        extra_body = payload.get("extra_body")
        if not isinstance(extra_body, dict):
            extra_body = {}
        extra_body.setdefault("reasoning_split", reasoning_split)
        payload["extra_body"] = extra_body
        return payload

    chat_cls._get_request_payload = _get_request_payload
    setattr(chat_cls, flag, True)
    return True


def _patch_trading_graph(module: ModuleType) -> None:
    graph_cls = getattr(module, "TradingAgentsGraph", None)
    if graph_cls is None or getattr(graph_cls, _PATCH_FLAG, False):
        return

    original_get_provider_kwargs = getattr(graph_cls, "_get_provider_kwargs", None)
    if original_get_provider_kwargs is None:
        return

    def _get_provider_kwargs(self):
        kwargs = dict(original_get_provider_kwargs(self) or {})
        config = getattr(self, "config", {}) or {}
        provider = str(config.get("llm_provider", "")).lower()
        if provider:
            from tradingagents_providers.catalog import get_thinking_level_kwargs

            kwargs.update(get_thinking_level_kwargs(provider, config))
        return kwargs

    graph_cls._get_provider_kwargs = _get_provider_kwargs
    setattr(graph_cls, _PATCH_FLAG, True)
    setattr(module, _PATCH_FLAG, True)


def _patch_cli_utils(module: ModuleType) -> None:
    if getattr(module, _PATCH_FLAG, False):
        return

    _patch_select_llm_provider(module)

    try:
        from tradingagents.llm_clients.api_key_env import get_api_key_env
        from tradingagents.llm_clients.model_catalog import get_model_options
    except ImportError:
        setattr(module, _PATCH_FLAG, True)
        return

    module.get_api_key_env = get_api_key_env
    module.get_model_options = get_model_options
    setattr(module, _PATCH_FLAG, True)


def _patch_cli_main(module: ModuleType) -> None:
    app = getattr(module, "app", None)
    if app is None:
        return

    _patch_cli_root_callback(module, app)
    _register_cli_groups_once(app)
    _patch_imported_cli_utils_functions(module)
    _patch_cli_get_user_selections(module)

    setattr(app, _PATCH_FLAG, True)
    setattr(module, _PATCH_FLAG, True)


def _patch_imported_cli_utils_functions(module: ModuleType) -> None:
    try:
        import cli.utils as cli_utils
    except ImportError:
        return

    _patch_select_llm_provider(cli_utils)
    module.select_llm_provider = cli_utils.select_llm_provider


def _patch_cli_get_user_selections(module: ModuleType) -> None:
    if getattr(module, _CLI_SELECTIONS_FLAG, False):
        return
    original_get_user_selections = getattr(module, "get_user_selections", None)
    if original_get_user_selections is None:
        return

    def get_user_selections():
        selections = dict(original_get_user_selections() or {})
        _add_plugin_provider_params(module, selections)
        return selections

    module.get_user_selections = get_user_selections
    setattr(module, _CLI_SELECTIONS_FLAG, True)


def _add_plugin_provider_params(module: ModuleType, selections: dict) -> None:
    provider = str(selections.get("llm_provider") or "").strip().lower()
    if not provider:
        return

    try:
        from tradingagents_providers.catalog import get_provider_thinking_config, register
    except Exception:
        return

    register()
    thinking = get_provider_thinking_config(provider)
    if not thinking:
        return

    config_key = str(thinking.get("config_key") or "").strip()
    env_var = str(thinking.get("env_var") or "").strip()
    if config_key and selections.get(config_key):
        return

    level = _ask_plugin_thinking_level(module, provider, thinking)
    if level is None:
        return
    if config_key:
        selections[config_key] = level
    if env_var:
        import os

        os.environ[env_var] = level


def _ask_plugin_thinking_level(
    module: ModuleType,
    provider: str,
    thinking: dict,
) -> str | None:
    questionary = getattr(module, "questionary", None)
    if questionary is None:
        return None

    console = getattr(module, "console", None)
    create_question_box = getattr(module, "create_question_box", None)
    if console is not None and create_question_box is not None:
        console.print(
            create_question_box(
                "Step 8: Thinking Level",
                f"Configure {provider} thinking level",
            )
        )

    levels = tuple(thinking.get("levels") or ("low", "medium", "high"))
    choices = [
        questionary.Choice("Provider default", "none"),
        *[
            questionary.Choice(level.title(), level)
            for level in levels
        ],
    ]
    return questionary.select(
        "Select Thinking Level:",
        choices=choices,
        style=questionary.Style(
            [
                ("selected", "fg:cyan noinherit"),
                ("highlighted", "fg:cyan noinherit"),
                ("pointer", "fg:cyan noinherit"),
            ]
        ),
    ).ask()


def _patch_select_llm_provider(module: ModuleType) -> None:
    if getattr(module, "_tradingagents_providers_select_llm_provider_patched", False):
        return
    if not hasattr(module, "questionary"):
        return

    def select_llm_provider():
        questionary = module.questionary
        console = getattr(module, "console", None)
        choices = [
            questionary.Choice(display, value=(provider_key, base_url))
            for display, provider_key, base_url in _provider_picker_options()
        ]
        choice = questionary.select(
            "Select your LLM Provider:",
            choices=choices,
            instruction="\n- Use arrow keys to navigate\n- Press Enter to select",
            style=questionary.Style(
                [
                    ("selected", "fg:magenta noinherit"),
                    ("highlighted", "fg:magenta noinherit"),
                    ("pointer", "fg:magenta noinherit"),
                ]
            ),
        ).ask()

        if choice is None:
            if console is not None:
                console.print("\n[red]No LLM provider selected. Exiting...[/red]")
            raise SystemExit(1)

        return choice

    module.select_llm_provider = select_llm_provider
    setattr(module, "_tradingagents_providers_select_llm_provider_patched", True)


def _provider_picker_options() -> list[tuple[str, str, str | None]]:
    import os

    ollama_url = os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
    options: list[tuple[int, str, str, str | None]] = [
        (20, "OpenAI", "openai", "https://api.openai.com/v1"),
        (20, "Google", "google", None),
        (20, "Anthropic", "anthropic", "https://api.anthropic.com/"),
        (20, "xAI", "xai", "https://api.x.ai/v1"),
        (20, "DeepSeek", "deepseek", "https://api.deepseek.com"),
        (20, "Qwen", "qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        (20, "GLM", "glm", "https://open.bigmodel.cn/api/paas/v4/"),
        (20, "MiniMax (Global)", "minimax", "https://api.minimax.io/v1"),
        (20, "OpenRouter", "openrouter", "https://openrouter.ai/api/v1"),
        (20, "Azure OpenAI", "azure", None),
        (20, "Ollama", "ollama", ollama_url),
    ]
    seen = {provider for _, _, provider, _ in options}

    _register_catalog()
    from tradingagents_providers.providers import list_provider_profiles

    for profile in sorted(list_provider_profiles(), key=lambda item: item.display_name):
        if profile.name in seen:
            continue
        if not _provider_is_selectable_for_analysis(profile):
            continue
        if profile.api_mode not in {
            "chat_completions",
            "anthropic_messages",
            "google_native",
            "azure_openai",
            "bedrock_converse",
            "codex_responses",
            "external_process",
        }:
            continue
        is_logged_in = _provider_has_saved_login(profile.name)
        display_name = _provider_picker_display_name(profile, is_logged_in)
        priority = 0 if is_logged_in else 30
        options.append((priority, display_name, profile.name, profile.base_url))
        seen.add(profile.name)

    return [
        (display, provider, base_url)
        for _, (_, display, provider, base_url) in sorted(
            enumerate(options),
            key=lambda item: (item[1][0], item[0]),
        )
    ]


def _provider_is_selectable_for_analysis(profile) -> bool:
    """Return whether an interactive analysis run can use this provider now."""
    if profile.runtime_status != "ready":
        return False
    if profile.auth_type in {"oauth_device_code", "oauth_external"}:
        return _provider_has_saved_login(profile.name)
    return True


def _provider_picker_display_name(profile, is_logged_in: bool) -> str:
    markers: list[str] = []
    if is_logged_in:
        markers.append("logged in")
    if profile.runtime_status != "ready":
        markers.append(profile.runtime_status.replace("_", " "))
    if not markers:
        return profile.display_name
    return f"{profile.display_name} ({'; '.join(markers)})"


def _provider_has_saved_login(provider: str) -> bool:
    try:
        from tradingagents_providers.oauth import get_provider_auth_state

        state = get_provider_auth_state(provider)
    except Exception:
        return False
    return bool(state and state.get("access_token"))


def _patch_cli_root_callback(module: ModuleType, app) -> None:
    if getattr(app, _CLI_ROOT_CALLBACK_FLAG, False):
        return
    if getattr(app, "registered_callback", None) is not None:
        return

    analyze = getattr(module, "analyze", None)
    if analyze is None:
        return

    import typer

    globals().setdefault("typer", typer)

    @app.callback(invoke_without_command=True)
    def tradingagents_providers_root(
        ctx: typer.Context,
        checkpoint: bool = typer.Option(
            False,
            "--checkpoint",
            help="Enable checkpoint/resume: save state after each node so a crashed run can resume.",
        ),
        clear_checkpoints: bool = typer.Option(
            False,
            "--clear-checkpoints",
            help="Delete all saved checkpoints before running (force fresh start).",
        ),
    ) -> None:
        if ctx.invoked_subcommand is None:
            analyze(checkpoint=checkpoint, clear_checkpoints=clear_checkpoints)

    setattr(app, _CLI_ROOT_CALLBACK_FLAG, True)


def _register_cli_groups_once(app) -> None:
    from tradingagents_providers.cli import auth_app, providers_app

    if not _has_registered_cli_group(app, "providers"):
        app.add_typer(providers_app, name="providers")
    if not _has_registered_cli_group(app, "auth"):
        app.add_typer(auth_app, name="auth")


def _has_registered_cli_group(app, name: str) -> bool:
    return any(
        getattr(group, "name", None) == name
        for group in getattr(app, "registered_groups", [])
    )
