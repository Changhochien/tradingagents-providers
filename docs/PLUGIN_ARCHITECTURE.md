# TradingAgents Provider Extension Architecture

This document describes the current provider extension architecture. The older
proposal has been folded into an installable side-by-side package:
`tradingagents-providers`.

The implementation specs and production contract are tracked in
[PROVIDER_EXTENSION_SPECS.md](PROVIDER_EXTENSION_SPECS.md).

## Current Goal

Users should be able to install upstream TradingAgents, then install this
provider package without maintaining a TradingAgents fork:

```bash
pip install git+https://github.com/TauricResearch/TradingAgents.git
pip install tradingagents-providers
tradingagents auth
```

For repository testing before publishing:

```bash
pip install "git+https://github.com/Changhochien/tradingagents-providers.git"
```

The package extends TradingAgents at runtime through official extension hooks
when they exist, or through a narrow compatibility bootstrap for current
upstream releases that do not yet expose those hooks.

## Package Layout

```text
tradingagents-providers/
  pyproject.toml
  README.md
  src/tradingagents_providers/
    __init__.py
    _autoactivate.pth
    bootstrap.py
    catalog.py
    cli.py
    providers/
      __init__.py
      provider_profiles.py
      provider_registry.py
      runtime.py
  tests/
    test_fresh_install.py
```

The original TradingAgents source is not edited by the package installer.

## Provider Profile Model

Provider metadata lives in `ProviderProfile`. A profile describes how a
provider is discovered, authenticated, routed, and shown in model pickers.

```python
ProviderProfile(
    name="xiaomi",
    display_name="Xiaomi MiMo",
    aliases=("mimo", "xiaomi-mimo"),
    api_mode="chat_completions",
    runtime_status="ready",
    auth_type="api_key",
    api_key_env_vars=("XIAOMI_API_KEY",),
    base_url="https://api.xiaomimimo.com/v1",
    base_url_env_var="XIAOMI_BASE_URL",
    models=("mimo-v2.5-pro", "mimo-v2.5"),
)
```

Important fields:

- `name`: canonical provider key.
- `aliases`: alternate user-facing names.
- `api_mode`: runtime route, such as `chat_completions`,
  `anthropic_messages`, or `google_native`.
- `runtime_status`: whether the provider can run today.
- `auth_type`: `api_key`, `oauth_external`, `oauth_device_code`,
  `external_process`, or `none`.
- `api_key_env_vars`: env vars checked in priority order.
- `base_url`: default endpoint.
- `base_url_env_var`: optional endpoint override.
- `quick_models` and `deep_models`: model catalog entries.

## Registry

The provider registry is local to the package:

```text
tradingagents_providers.providers.provider_registry
```

It supports:

- idempotent provider registration;
- alias resolution;
- listing registered providers;
- lazy catalog registration through `tradingagents_providers.catalog.register()`.

The package currently registers 37 providers:

- 34 `ready` providers;
- 0 `needs_oauth` providers;
- 3 `needs_adapter` providers.

Ready login-backed providers:

```text
google-gemini-cli
minimax-oauth
nous
openai-codex
qwen-oauth
```

Known `needs_adapter` providers:

```text
bedrock
copilot
copilot-acp
```

Providers marked `needs_adapter` are intentionally blocked at runtime until the
required support exists. The package should never pretend these providers work
by guessing endpoints or credentials.

## Runtime Resolution

Runtime resolution turns a provider name plus optional explicit runtime inputs
into a `RuntimeProvider`.

Resolution order:

1. Provider name and aliases are resolved through the registry.
2. Explicit base URL wins over env URL and profile default.
3. Provider base URL env var wins over profile default.
4. Explicit API key wins over env vars.
5. Provider API-key env vars are checked in profile order.
6. Unknown providers use the legacy custom OpenAI-compatible fallback.

Runtime object:

```python
RuntimeProvider(
    provider="xiaomi",
    requested_provider="xiaomi",
    api_mode="chat_completions",
    auth_type="api_key",
    base_url="https://api.xiaomimimo.com/v1",
    api_key="...",
    source="profile",
    model="mimo-v2.5-pro",
    runtime_status="ready",
)
```

Blocking errors are typed:

- `NeedsAdapterError`
- `NeedsOAuthError`
- `ProviderRuntimeError`
- `ProviderNotFoundError`

## TradingAgents Integration

The package integrates with TradingAgents in two modes.

### Official Hook Mode

Future or forked TradingAgents versions may expose:

```python
set_factory_resolver(...)
set_model_catalog_hook(...)
set_cli_command_hook(...)
```

When `tradingagents.ext_loader` is present, `catalog.register()` registers these
hooks and the compatibility bootstrap disables itself.

### Compatibility Bootstrap Mode

Current upstream TradingAgents does not expose official extension hooks. For
that case, the wheel installs:

```text
tradingagents_providers_autoactivate.pth
```

That `.pth` file imports `tradingagents_providers.bootstrap`, which installs a
lazy import hook. The hook patches only these modules after they are imported:

```text
tradingagents.llm_clients.factory
tradingagents.llm_clients.model_catalog
tradingagents.llm_clients.api_key_env
tradingagents.llm_clients.openai_client
cli.main
cli.utils
```

The bootstrap rules:

- patch only known integration points;
- make every patch idempotent;
- delegate unknown providers back to upstream behavior;
- avoid importing expensive TradingAgents modules at Python startup;
- disable itself when official hooks exist.

## Factory Behavior

For registered plugin providers, the factory path is:

```text
create_llm_client(provider, model, base_url, **kwargs)
  -> resolve_runtime_provider(...)
  -> create the matching TradingAgents client
```

Current supported routes:

- `chat_completions` -> `OpenAIClient`
- `anthropic_messages` -> `AnthropicClient`
- `google_native` -> `GoogleClient`

If a provider is not registered, the wrapped factory delegates to the original
TradingAgents factory.

## Model Catalog Behavior

For registered plugin providers:

- `get_model_options(provider, "quick")` returns profile quick models.
- `get_model_options(provider, "deep")` returns profile deep models.
- `get_known_models()` includes plugin provider model IDs.
- every provider model list includes `("Custom model ID", "custom")`.

Unknown providers keep upstream catalog behavior.

## API-Key Behavior

API-key setup and runtime resolution follow predictable precedence:

1. explicit `api_key=` argument;
2. provider-specific env vars;
3. no key.

Base URL precedence:

1. explicit `base_url=`;
2. provider-specific base URL env var;
3. profile default base URL.

The package never reuses one provider's API key for another provider.

## CLI Surface

Provider commands:

```bash
tradingagents providers list
tradingagents providers setup <provider>
tradingagents providers doctor [provider]
```

Auth commands:

```bash
tradingagents auth
tradingagents auth add <provider> --api-key ...
tradingagents auth setup <provider> --api-key ...
tradingagents auth list [provider]
tradingagents auth remove <provider> [target]
tradingagents auth reset <provider>
tradingagents auth logout <provider>
tradingagents auth status [provider]
```

Bare `tradingagents auth` follows the Hermes-style flow:

```text
Credential Pool Status
==================================================

What would you like to do?
  1. Add a credential
  2. Remove a credential
  3. Reset cooldowns for a provider
  4. Set rotation strategy for a provider
  5. Exit
```

Current auth storage:

- API keys are saved to the selected `.env` file.
- `auth list` shows configured credentials by provider.
- `auth remove` removes credentials from the selected `.env` file.
- reset and strategy commands are UX-compatible placeholders until a real
  credential pool exists.

Next auth milestone:

- add `~/.tradingagents/auth.json`;
- store multiple credentials per provider;
- persist rotation strategy;
- track cooldown and exhausted status;
- keep `.env` as a compatibility fallback.

## Provider Categories

### Ready

Ready providers can run through the existing TradingAgents clients. Most are
OpenAI-compatible chat completions providers.

Examples:

- OpenAI
- Anthropic
- Google / Gemini
- xAI
- DeepSeek
- OpenRouter
- Ollama
- NVIDIA NIM
- MiniMax
- Xiaomi MiMo

### OAuth Runtime

OAuth providers are registered only when the login flow and runtime credential
resolution are implemented. The package must not invent OAuth endpoints. OAuth
support should be added only with verified provider flows and locked token
storage.

### Needs Adapter

Adapter providers are registered for discovery and user guidance, but blocked
until the relevant runtime adapter exists.

## Verification

Required local checks:

```bash
uvx ruff check tradingagents-providers/src tradingagents-providers/tests
pytest tradingagents-providers/tests -q
```

Fresh upstream smoke:

```bash
python -m venv /tmp/ta-provider-smoke
/tmp/ta-provider-smoke/bin/pip install git+https://github.com/TauricResearch/TradingAgents.git
cd /path/to/repo/tradingagents-providers
/tmp/ta-provider-smoke/bin/pip install ".[cli]"
printf '1\nxiaomi\n\ntest-key\n' | /tmp/ta-provider-smoke/bin/tradingagents auth --env-file /tmp/ta-provider-smoke.env
/tmp/ta-provider-smoke/bin/tradingagents auth list xiaomi --env-file /tmp/ta-provider-smoke.env
/tmp/ta-provider-smoke/bin/tradingagents providers doctor xiaomi --env-file /tmp/ta-provider-smoke.env
```

Expected result:

- `tradingagents auth` opens the Hermes-style menu.
- Xiaomi API-key setup succeeds.
- `auth list xiaomi` shows one credential.
- `providers doctor xiaomi` reports `ready (chat_completions)`.

## What Not To Do

- Do not require users to install a TradingAgents fork.
- Do not edit upstream source files during provider package installation.
- Do not broaden the bootstrap patch targets without a spec update.
- Do not hardcode new providers in multiple upstream files.
- Do not invent provider OAuth endpoints.
- Do not silently run providers marked `needs_oauth` or `needs_adapter`.
- Do not store OAuth tokens in project `.env` files.
