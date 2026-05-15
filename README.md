# TradingAgents Providers

Standalone LLM provider plugin for
[TradingAgents](https://github.com/TauricResearch/TradingAgents).

This package installs beside upstream TradingAgents. It is not a TradingAgents
fork and it does not replace the trading framework. After installation, it
adds provider discovery, runtime routing, and Hermes-style credential commands
to the existing `tradingagents` CLI.

## Install

Current GitHub install:

```bash
pip install git+https://github.com/TauricResearch/TradingAgents.git
pip install git+https://github.com/Changhochien/tradingagents-providers.git
```

After the package is published:

```bash
pip install tradingagents
pip install tradingagents-providers
```

## What The Plugin Adds

- Registers 37 LLM provider profiles with TradingAgents.
- Adds `tradingagents auth` for Hermes-style provider credential setup.
- Adds `tradingagents providers list` and `tradingagents providers doctor`.
- Adds provider thinking-level settings for Xiaomi MiMo and MiniMax.
- Routes plugin providers through TradingAgents' existing LLM clients.
- Auto-activates on current upstream TradingAgents with a small `.pth`
  bootstrap.
- Uses official TradingAgents extension hooks automatically when they exist.

## Verify Installation

```bash
tradingagents auth --help
tradingagents providers list
```

If the plugin is active, `tradingagents auth --help` shows credential commands
such as `add`, `list`, `remove`, `reset`, `strategy`, and `logout`.

The plugin also extends the original interactive analysis flow. When you run
`tradingagents`, Step 6 still asks `Select your LLM Provider:`, but the picker
contains the installed provider catalog in addition to upstream options. Saved
OAuth logins are moved to the top and marked, for example:

```text
OpenAI Codex (logged in; needs adapter)
OpenAI
Google
...
```

Select `OpenAI Codex`, `MiniMax (OAuth)`, `Xiaomi MiMo`, etc. directly from
that provider picker. Selecting plain `OpenAI` means the upstream OpenAI API-key
provider and will still prompt for `OPENAI_API_KEY`.

## Configure A Provider

Run the interactive auth flow:

```bash
tradingagents auth
```

The menu follows the Hermes credential-pool shape:

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

Non-interactive example:

```bash
tradingagents auth add xiaomi --api-key "$XIAOMI_API_KEY"
tradingagents providers thinking-level xiaomi high
tradingagents auth list xiaomi
tradingagents providers doctor xiaomi
```

Expected readiness output after adding a key:

```text
xiaomi: ready (chat_completions)
```

Credentials are currently saved to the selected `.env` file. OAuth token pools
and multi-key rotation are planned, but not yet persisted as a real credential
pool.

Login-backed providers are handled through `tradingagents auth`:

```bash
tradingagents auth add nous --type oauth
tradingagents auth add minimax-oauth --type oauth
tradingagents auth add qwen-oauth --type oauth
tradingagents auth add openai-codex --type oauth
tradingagents auth add google-gemini-cli --type oauth
```

`nous`, `qwen-oauth`, `minimax-oauth`, `openai-codex`, and
`google-gemini-cli` can resolve runtime credentials for TradingAgents clients
after login. `openai-codex` uses the OAuth-backed Codex Responses API.
`google-gemini-cli` imports and refreshes the standard Gemini CLI file at
`~/.gemini/oauth_creds.json` and runs through Cloud Code Assist. `qwen-oauth`
imports and refreshes the standard Qwen CLI file at `~/.qwen/oauth_creds.json`;
run the relevant CLI auth first if that file does not exist.

## Thinking Level

Xiaomi MiMo, MiniMax, and MiniMax China can persist a thinking level alongside
the API key:

```bash
tradingagents providers thinking-level xiaomi high
tradingagents providers thinking-level minimax medium
tradingagents providers thinking-level minimax-cn low
```

Accepted levels are `low`, `medium`, `high`, plus Hermes-compatible aliases:
`minimal` maps to `low`, `xhigh` maps to `high`, and `none` removes the saved
setting. The plugin writes provider-specific variables such as
`XIAOMI_THINKING_LEVEL` and forwards them to TradingAgents as
`reasoning_effort`.

During the original interactive `tradingagents` run, these providers also get a
plugin-added thinking-level prompt after upstream Step 8. This keeps the addon in
the original CLI flow instead of requiring a separate setup command first.

## How It Works

The package exposes a provider catalog through Python entry points:

```toml
[project.entry-points."tradingagents.model_providers"]
catalog = "tradingagents_providers.catalog:register"
```

When TradingAgents exposes official extension hooks, the package registers:

- a factory resolver;
- a model catalog hook;
- a CLI command hook.

For current upstream TradingAgents releases without those hooks, the wheel
installs `tradingagents_providers_autoactivate.pth`. That bootstrap lazily
patches only the TradingAgents integration modules needed for provider factory,
model catalog, API-key lookup, and CLI command registration.

## Provider Status

The catalog includes 37 providers:

- `ready`: can run through existing TradingAgents clients.
- `needs_adapter`: registered, but blocked until a runtime adapter exists.
- `needs_oauth`: registered, but blocked until OAuth/login support exists.

Blocked providers fail loudly instead of pretending to work.

## Package Structure

```text
pyproject.toml
tradingagents_providers.pth
src/tradingagents_providers/
  bootstrap.py
  catalog.py
  cli.py
  providers/
    provider_profiles.py
    provider_registry.py
    runtime.py
tests/
```

## Development

```bash
pip install -e ".[dev]"
uvx ruff check src tests
pytest tests -q
```

## License

MIT
