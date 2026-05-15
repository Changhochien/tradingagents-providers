# TradingAgents Provider Extension Specs

This document records the provider-extension work as a sequence of specs. The
goal is to keep the package installable beside an unmodified upstream
TradingAgents release, while still giving users a Hermes-like provider and auth
experience.

The specs are cumulative:

1. Spec 1 defines provider profiles, discovery, and setup commands.
2. Spec 2 defines runtime provider resolution and adapter status.
3. Spec 3 defines the standalone package boundary and upstream extension hook.
4. Spec 4 defines the production best-practice contract for running beside the
   original upstream system without maintaining a fork.

---

## Spec 1: Provider Profile Registry and CLI Setup

### Problem

TradingAgents has fixed provider support spread across the LLM factory, model
catalog, API-key lookup, and CLI helpers. Adding providers one by one in core
does not scale, and it makes external provider packages hard to install.

### Goal

Introduce a declarative provider registry that can describe OpenAI-compatible
and native providers in one place, then expose that registry through provider
management commands.

### Required Behavior

- A provider is described by a `ProviderProfile`.
- Profiles include identity, aliases, auth type, API mode, base URL, API-key
  env vars, signup URL, and model options.
- Provider discovery is lazy and idempotent.
- Built-in provider profiles can be registered without importing every provider
  at process startup.
- Users can list, set up, and diagnose provider profiles from the CLI.

### Implemented Surface

Files:

- `tradingagents-providers/src/tradingagents_providers/providers/provider_profiles.py`
- `tradingagents-providers/src/tradingagents_providers/providers/provider_registry.py`
- `tradingagents-providers/src/tradingagents_providers/catalog.py`
- `tradingagents-providers/src/tradingagents_providers/cli.py`
- `tradingagents-providers/src/tradingagents_providers/bootstrap.py`

Commands:

```bash
tradingagents providers list
tradingagents providers setup <provider>
tradingagents providers doctor [provider]
```

Profile shape:

```python
ProviderProfile(
    name="xiaomi",
    display_name="Xiaomi MiMo",
    api_mode="chat_completions",
    auth_type="api_key",
    api_key_env_vars=("XIAOMI_API_KEY",),
    base_url="https://api.xiaomimimo.com/v1",
    base_url_env_var="XIAOMI_BASE_URL",
)
```

### Acceptance Criteria

- `list_provider_profiles()` returns built-in providers.
- `get_provider_profile(alias)` resolves aliases to canonical providers.
- `tradingagents providers list` shows provider metadata.
- `tradingagents providers setup <provider>` can save API-key credentials.
- Tests cover registry behavior, built-in providers, user plugin discovery, and
  setup command behavior.

### Non-Goals

- Full OAuth token lifecycle.
- Multi-key credential rotation.
- Provider-specific adapters for every native API mode.

---

## Spec 2: Runtime Provider Architecture

### Problem

The registry can describe providers, but the runtime still needs to know if a
provider can actually run, which credentials to use, which endpoint to call, and
which adapter is required.

### Goal

Add a runtime resolution layer that converts a provider profile and user input
into an executable runtime provider, or a precise blocking error.

### Required Behavior

- Resolve provider names and aliases through the profile registry.
- Resolve base URL in this order:
  1. explicit runtime argument;
  2. provider base URL env var;
  3. profile default base URL.
- Resolve API keys in this order:
  1. explicit runtime argument;
  2. first configured provider API-key env var with a value.
- Support providers that do not need keys.
- Block providers that require unavailable adapters.
- Block providers that require unavailable OAuth flows.
- Keep unknown provider fallback for explicit custom OpenAI-compatible use.

### Implemented Surface

Files:

- `tradingagents-providers/src/tradingagents_providers/providers/runtime.py`
- `tradingagents-providers/src/tradingagents_providers/providers/provider_profiles.py`
- `tradingagents-providers/src/tradingagents_providers/catalog.py`

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
    runtime_status="ready",
)
```

Runtime status:

- `ready`: can run with available TradingAgents clients.
- `needs_adapter`: profile is known, but the required adapter is missing.
- `needs_oauth`: profile is known, but login/token flow is not available yet.
- `metadata_only`: profile is informational and should not be executed.

### Acceptance Criteria

- Ready OpenAI-compatible providers resolve without custom code.
- Explicit API key and base URL override profile defaults.
- Missing adapter providers raise `NeedsAdapterError`.
- OAuth providers raise `NeedsOAuthError`.
- CLI doctor reports useful runtime status.

### Non-Goals

- Silently treating OAuth providers as API-key providers.
- Pretending unsupported API modes can run.
- Inventing endpoints that are not proven by the upstream provider project.

---

## Spec 3: Standalone Provider Package and Extension Hook

### Problem

The first two specs started as an in-tree extension, but users should not need
a TradingAgents fork to try new providers. The provider package must install as
a separate Python distribution.

### Goal

Split provider support into `tradingagents-providers`, while defining a small
official hook surface that future TradingAgents core can adopt.

### Required Behavior

- `tradingagents-providers` is a standalone package.
- Provider profiles live in the package, not in a patched core tree.
- Package registration is exposed through Python entry points.
- If TradingAgents exposes official extension hooks, use those hooks.
- If TradingAgents does not expose hooks yet, support current upstream with a
  compatibility bootstrap.
- The original TradingAgents package remains installable and upgradeable.

### Implemented Surface

Standalone package:

```text
tradingagents-providers/
  pyproject.toml
  src/tradingagents_providers/
    catalog.py
    bootstrap.py
    cli.py
    providers/
      provider_profiles.py
      provider_registry.py
      runtime.py
```

Entry point:

```toml
[project.entry-points."tradingagents.model_providers"]
catalog = "tradingagents_providers.catalog:register"
```

Official hook shape:

```python
set_factory_resolver(...)
set_model_catalog_hook(...)
set_cli_command_hook(...)
```

Compatibility bootstrap:

- Installs a `.pth` file.
- Imports `tradingagents_providers.bootstrap` at Python startup.
- Installs a lazy import hook only if official TradingAgents hooks are absent.
- Patches selected upstream modules after they are imported.

### Acceptance Criteria

- A fresh upstream TradingAgents install plus `tradingagents-providers` exposes:
  - plugin provider factory support;
  - plugin model catalog support;
  - plugin API-key env lookup;
  - `tradingagents providers ...`;
  - `tradingagents auth ...`.
- The package can be installed from the repository subdirectory.
- Tests can simulate upstream modules that have no extension hooks.
- No upstream source files need to be edited for users to try the plugin.

### Non-Goals

- Replacing the upstream TradingAgents package.
- Maintaining a long-lived fork as the distribution vehicle.
- Requiring users to clone this repository manually.

---

## Spec 4: Side-by-Side Production Contract

### Problem

The plugin now works beside upstream, but production quality needs a stricter
contract. A compatibility package that patches an application at import time can
be useful, but it must be narrow, predictable, reversible, and easy to test
against a fresh upstream release.

### Goal

Make `tradingagents-providers` the best-practice companion package for upstream
TradingAgents:

```bash
pip install tradingagents
pip install tradingagents-providers
tradingagents auth
```

The provider package should extend upstream behavior without owning upstream
code, masking upstream errors, or making upgrades fragile.

### Design Principles

1. Prefer official hooks over compatibility patching.
2. Patch only named, stable integration points.
3. Make patching idempotent.
4. Preserve original upstream behavior for unknown providers.
5. Avoid broad global state changes.
6. Keep user secrets in explicit stores, never in source files.
7. Test from clean installs, not only from the repository checkout.
8. Fail loudly when a provider needs unsupported OAuth or adapter work.

### Package Boundaries

`tradingagents-providers` owns:

- provider profiles;
- provider runtime resolution;
- provider auth CLI extensions;
- model catalog additions;
- provider factory routing for registered profiles;
- compatibility bootstrap for upstream releases without official hooks.

Upstream TradingAgents owns:

- trading graph behavior;
- analyst agents and dataflow;
- CLI application lifecycle;
- existing first-party providers;
- existing config defaults;
- existing user workflows that do not use plugin providers.

The provider package must not edit upstream files at install time. It may only
extend behavior at runtime through official hooks or the documented bootstrap.

### Official Hook Mode

When upstream exposes `tradingagents.ext_loader`, the package must use it and
must not install compatibility patch behavior.

Expected hook use:

```python
from tradingagents.ext_loader import (
    set_cli_command_hook,
    set_factory_resolver,
    set_model_catalog_hook,
)
```

Required semantics:

- `set_factory_resolver()` accepts a resolver that returns a client for known
  plugin providers and `None` for unknown providers.
- `set_model_catalog_hook()` accepts a resolver that returns model options for
  known plugin providers and `None` for unknown providers.
- `set_cli_command_hook()` accepts a function that adds Typer commands to the
  existing app.
- Core calls extension hooks lazily, not at package import time.

### Compatibility Bootstrap Mode

When upstream does not expose official hooks, the package may use the `.pth`
bootstrap. The bootstrap is allowed to patch only these modules:

```text
tradingagents.llm_clients.factory
tradingagents.llm_clients.model_catalog
tradingagents.llm_clients.api_key_env
tradingagents.llm_clients.openai_client
cli.main
cli.utils
```

Rules:

- Patching must happen after the target module is imported.
- Every patch must set a private patch flag and be safe to call more than once.
- Wrapped functions must delegate to original upstream functions for unknown
  providers.
- The bootstrap must disable itself when official hooks are present.
- The bootstrap must not import expensive TradingAgents modules at Python
  startup.
- The bootstrap must not mutate unrelated modules, user config, or environment
  variables during installation.

### Factory Contract

For a registered plugin provider:

1. Resolve the provider through `resolve_runtime_provider()`.
2. If the runtime API mode is supported, create the matching TradingAgents LLM
   client.
3. If the provider needs OAuth or an adapter, raise the typed runtime error.
4. If the provider is unknown, return control to upstream.

Supported API modes for this production contract:

- `chat_completions`
- `anthropic_messages`
- `google_native`
- `azure_openai`, once upstream adapter behavior is verified

Unknown API modes must not be guessed.

### Model Catalog Contract

For a registered plugin provider:

- `get_model_options(provider, "quick")` returns profile quick models.
- `get_model_options(provider, "deep")` returns profile deep models.
- model lists always include `("Custom model ID", "custom")`.
- `get_known_models()` includes plugin providers without removing upstream
  providers.

For unknown providers, upstream catalog behavior is preserved.

### API-Key Contract

API-key setup must remain boring and predictable:

- The first provider env var is the default save target.
- `.env` support remains for compatibility with current TradingAgents.
- Runtime resolution can also read process environment variables.
- Explicit `api_key=` passed to factory calls wins over `.env` and process env.
- Explicit base URL wins over env URL and profile URL.
- A plugin provider cannot borrow another provider's API key by accident.

### Auth UX Contract

`tradingagents auth` should follow the Hermes-style flow:

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

Current implementation:

- API-key credentials are saved in the selected `.env` file.
- `auth list` displays configured credentials by provider.
- `auth remove` removes credentials from the selected `.env` file.
- `auth reset` exists for UX compatibility and is a no-op until a real pool is
  implemented.
- Strategy selection exists for UX compatibility and is not persisted until a
  real pool is implemented.

Next implementation step:

- Add a real credential pool store under `~/.tradingagents/auth.json`.
- Store multiple API keys per provider.
- Persist rotation strategy.
- Track cooldown and exhausted status.
- Keep `.env` as import/export and compatibility fallback.

### OAuth Contract

OAuth providers must remain explicit. A provider should be marked `needs_oauth`
until the package has a real, tested auth implementation.

Rules:

- Do not invent OAuth endpoints.
- Do not copy Hermes endpoints unless they are provider-supported and verified.
- Device-code and browser-login flows must store tokens in a locked auth store.
- Tokens must not be written to project `.env`.
- Logout must remove local token state.
- Expired tokens must either refresh or produce a clear re-auth message.

### Installation Contract

Supported install paths:

```bash
pip install git+https://github.com/TauricResearch/TradingAgents.git
pip install tradingagents-providers
```

Repository test install:

```bash
pip install "git+https://github.com/Changhochien/tradingagents-providers.git"
```

Development install:

```bash
pip install -e /path/to/upstream/TradingAgents
pip install -e "/path/to/repo/tradingagents-providers[cli]"
```

The root repository install and the provider subdirectory install are different
packages. Users who only want providers should install the provider subdirectory
package.

### Verification Matrix

Every production change must pass these checks:

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

Expected smoke result:

- `tradingagents auth` opens the Hermes-style menu.
- Xiaomi can be added as an API-key provider.
- `auth list xiaomi` shows one credential.
- `providers doctor xiaomi` reports `ready (chat_completions)`.

### Release Checklist

- Clean install tested against latest upstream TradingAgents.
- Clean install tested against the test GitHub branch.
- README contains the exact install command.
- Provider count and status counts are asserted in tests.
- At least one OpenAI-compatible provider is smoke-tested through the factory.
- At least one native provider path is covered by tests if enabled.
- `needs_oauth` and unsupported adapter providers are blocked with clear errors.
- Bootstrap patch targets are documented and unchanged unless reviewed.
- No changes are required in upstream TradingAgents source files.

### Future Upstream Path

The best long-term outcome is for upstream TradingAgents to adopt the official
extension hook surface. When that happens:

1. Keep `tradingagents-providers` as the provider catalog and auth package.
2. Detect official hooks and disable compatibility patching automatically.
3. Retain bootstrap support only for older upstream versions.
4. Publish a compatibility table mapping provider package versions to upstream
   TradingAgents versions.

### Open Decisions

- Whether the persistent auth store should live in `~/.tradingagents/auth.json`
  or follow a platform keyring by default.
- Whether `.env` should remain the default save target after a real credential
  pool exists.
- Whether provider profiles should be exported as a stable public API for
  third-party provider packages.
- Which OAuth providers should be implemented first.
- Whether strategy and cooldown behavior should mirror Hermes exactly or remain
  TradingAgents-specific.
