"""Standalone ``tradingagents providers`` CLI commands."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import typer

from tradingagents_providers.catalog import get_provider_thinking_config, register
from tradingagents_providers.providers import (
    NeedsAdapterError,
    NeedsOAuthError,
    ProviderRuntimeError,
    get_provider_profile,
    list_provider_profiles,
    resolve_runtime_provider,
)
from tradingagents_providers.oauth import (
    AuthError,
    can_login_provider,
    clear_provider_auth_state,
    get_auth_status,
    login_provider,
)

providers_app = typer.Typer(
    name="providers",
    help="Manage TradingAgents LLM providers",
    add_completion=True,
)
auth_app = typer.Typer(
    name="auth",
    help="Manage TradingAgents provider credentials",
    add_completion=True,
    invoke_without_command=True,
    no_args_is_help=False,
)

def register_cli_commands(app: typer.Typer) -> None:
    """Register provider commands on a TradingAgents Typer app."""
    app.add_typer(providers_app, name="providers")
    app.add_typer(auth_app, name="auth")


def register_auth_commands(app: typer.Typer) -> None:
    """Register only auth commands on a TradingAgents Typer app."""
    app.add_typer(auth_app, name="auth")


def _load_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return

    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        os.environ.setdefault(key, value)


def _env_file_values(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _set_env_value(env_file: Path, key: str, value: str) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    replacement = f"{key}={value}"
    updated = False

    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = replacement
            updated = True
            break

    if not updated:
        lines.append(replacement)

    env_file.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def _remove_env_value(env_file: Path, key: str) -> bool:
    removed = False
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    kept_lines = []

    for line in lines:
        if line.strip().startswith(f"{key}="):
            removed = True
            continue
        kept_lines.append(line)

    if removed:
        env_file.write_text(("\n".join(kept_lines) + "\n") if kept_lines else "")

    os.environ.pop(key, None)
    return removed


def _prompt_secret(prompt: str) -> str:
    if not sys.stdin.isatty():
        return input(prompt + " ").strip()

    try:
        import questionary

        return questionary.password(prompt).ask() or ""
    except ImportError:
        return getpass.getpass(prompt + " ") or ""


def _prompt_line(prompt: str) -> str:
    return input(prompt).strip()


def _print_table(rows: list[tuple[str, str, str, str]]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Name")
        table.add_column("Display")
        table.add_column("API Mode")
        table.add_column("Runtime")
        for row in rows:
            table.add_row(*row)
        Console().print(table)
    except ImportError:
        for row in rows:
            print("\t".join(row))


def _save_provider_credentials(
    provider: str,
    api_key: str | None,
    base_url: str | None,
    env_file: Path,
    *,
    announce_saved: bool = True,
) -> None:
    register()
    profile = get_provider_profile(provider)
    if profile is None:
        raise typer.BadParameter(f"Unknown provider: {provider}")

    if profile.auth_type in {"oauth_device_code", "oauth_external"}:
        typer.echo(
            f"{profile.display_name} uses login-based auth. "
            f"Run `tradingagents auth add {profile.name} --type oauth`."
        )
        raise typer.Exit(1)

    if profile.runtime_status == "needs_adapter":
        typer.echo(
            f"{profile.display_name} requires an adapter before it can run "
            f"({profile.api_mode})."
        )
        raise typer.Exit(1)

    if profile.auth_type == "none":
        typer.echo(f"{profile.display_name} does not require an API key.")
        raise typer.Exit(0)

    if not profile.api_key_env_vars:
        typer.echo(f"{profile.display_name} does not declare an API-key variable.")
        raise typer.Exit(1)

    env_var = profile.api_key_env_vars[0]
    key = api_key or os.environ.get(env_var) or _prompt_secret(f"Paste your {env_var}:")
    if not key:
        typer.echo(f"Skipped. {env_var} was not saved.")
        raise typer.Exit(1)

    _set_env_value(env_file, env_var, key)
    if announce_saved:
        typer.echo(f"Saved {env_var} to {env_file}")

    if base_url and profile.base_url_env_var:
        _set_env_value(env_file, profile.base_url_env_var, base_url)
        if announce_saved:
            typer.echo(f"Saved {profile.base_url_env_var} to {env_file}")


def _save_thinking_level(provider: str, level: str, env_file: Path) -> None:
    register()
    thinking = get_provider_thinking_config(provider)
    if thinking is None:
        raise typer.BadParameter(
            f"{provider} does not expose a plugin-managed thinking level."
        )

    normalized = level.strip().lower()
    if normalized == "minimal":
        normalized = "low"
    elif normalized == "xhigh":
        normalized = "high"

    if normalized == "none":
        env_var = str(thinking["env_var"])
        if _remove_env_value(env_file, env_var):
            typer.echo(f"Removed {env_var} from {env_file}")
            return
        typer.echo(f"{env_var} was not set in {env_file}")
        return

    allowed = tuple(thinking.get("levels") or ("low", "medium", "high"))
    if normalized not in allowed:
        valid = ", ".join((*allowed, "minimal", "xhigh", "none"))
        raise typer.BadParameter(f"Invalid level {level!r}. Use one of: {valid}.")

    env_var = str(thinking["env_var"])
    _set_env_value(env_file, env_var, normalized)
    typer.echo(f"Saved {env_var}={normalized} to {env_file}")


def _provider_status(provider: str, env_file: Path) -> str:
    _load_env_file(env_file)

    try:
        runtime = resolve_runtime_provider(provider)
        if runtime.auth_type == "api_key" and not runtime.api_key:
            return "missing API key"
        return f"ready ({runtime.api_mode})"
    except NeedsAdapterError as exc:
        return f"needs adapter: {exc.adapter}"
    except NeedsOAuthError:
        return "needs OAuth"
    except ProviderRuntimeError as exc:
        return exc.reason


def _credential_entries(
    env_file: Path,
    provider: str | None = None,
) -> list[tuple[str, str, str, str]]:
    register()
    _load_env_file(env_file)
    env_values = _env_file_values(env_file)
    entries = []

    for profile in sorted(list_provider_profiles(), key=lambda item: item.name):
        if provider and profile.name != provider:
            continue
        if profile.auth_type == "api_key":
            for env_var in profile.api_key_env_vars:
                value = env_values.get(env_var) or os.environ.get(env_var)
                if not value:
                    continue
                source = "manual" if env_var in env_values else "env"
                entries.append((profile.name, env_var, "api_key", source))
        elif can_login_provider(profile.name):
            status = get_auth_status(profile.name)
            if status.get("logged_in"):
                source = str(status.get("source") or "oauth")
                entries.append((profile.name, profile.name, profile.auth_type, source))

    return entries


def _credential_count(provider: str, env_file: Path) -> int:
    return len(_credential_entries(env_file, provider))


def _print_credential_pool_status(env_file: Path) -> None:
    entries = _credential_entries(env_file)
    by_provider: dict[str, list[tuple[str, str, str, str]]] = {}
    for entry in entries:
        by_provider.setdefault(entry[0], []).append(entry)

    typer.echo("Credential Pool Status")
    typer.echo("=" * 50)
    for provider in sorted(by_provider):
        provider_entries = by_provider[provider]
        typer.echo(f"{provider} ({len(provider_entries)} credentials):")
        for index, (_, label, auth_type, source) in enumerate(provider_entries, 1):
            marker = " ←" if index == 1 else ""
            typer.echo(f"  #{index:<2} {label:<22} {auth_type:<8} {source}{marker}")
    typer.echo("")


def _known_auth_provider_names() -> list[str]:
    register()
    return [
        profile.name
        for profile in sorted(list_provider_profiles(), key=lambda item: item.name)
        if profile.auth_type == "api_key" or can_login_provider(profile.name)
    ]


def _pick_provider(prompt: str) -> str | None:
    names = _known_auth_provider_names()
    typer.echo("")
    typer.echo(f"Known providers: {', '.join(names)}")
    provider = _prompt_line(f"{prompt}: ")
    return provider or None


def _run_login_flow(
    provider: str,
    *,
    no_browser: bool = False,
    timeout_seconds: float = 15.0,
    region: str = "global",
) -> None:
    try:
        result = login_provider(
            provider,
            no_browser=no_browser,
            timeout_seconds=timeout_seconds,
            region=region,
        )
    except AuthError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    typer.echo(f"Logged in to {provider}.")
    if result.get("base_url"):
        typer.echo(f"Runtime base URL: {result['base_url']}")


def _interactive_add(env_file: Path) -> None:
    provider = _pick_provider("Provider to add credential for")
    if not provider:
        typer.echo("Cancelled.")
        return

    register()
    profile = get_provider_profile(provider)
    if profile is None:
        typer.echo(f"Unknown provider: {provider}")
        return
    if profile.auth_type in {"oauth_device_code", "oauth_external"}:
        _run_login_flow(profile.name)
        return
    if profile.runtime_status == "needs_adapter":
        typer.echo(
            f"{profile.display_name} is registered, but it needs an adapter "
            f"before it can run ({profile.api_mode})."
        )
        return

    auth_type = "api_key"
    label = _prompt_line("Label / account name (optional): ")
    if auth_type != "api_key":
        typer.echo(f"{auth_type} credentials are not implemented yet.")
        return

    before_count = _credential_count(provider, env_file)
    _save_provider_credentials(provider, None, None, env_file, announce_saved=False)
    display_label = label or f"api-key-{before_count + 1}"
    typer.echo(f'Added {provider} credential #{before_count + 1}: "{display_label}"')


def _interactive_remove(env_file: Path) -> None:
    provider = _pick_provider("Provider to remove credential from")
    if not provider:
        typer.echo("Cancelled.")
        return

    entries = _credential_entries(env_file, provider)
    if not entries:
        typer.echo(f"No credentials for {provider}.")
        return

    for index, (_, label, auth_type, source) in enumerate(entries, 1):
        typer.echo(f"  #{index:<2} {label:<22} {auth_type:<8} {source}")

    target = _prompt_line("Remove #, id, or label (blank to cancel): ")
    if not target:
        typer.echo("Cancelled.")
        return

    selected = entries[0]
    if target.isdigit():
        selected_index = int(target) - 1
        if selected_index < 0 or selected_index >= len(entries):
            typer.echo(f"No credential #{target} for {provider}.")
            return
        selected = entries[selected_index]
    else:
        matches = [entry for entry in entries if entry[1] == target]
        if not matches:
            typer.echo(f"No credential matching {target!r} for {provider}.")
            return
        selected = matches[0]

    _, env_var, _, _ = selected
    if _remove_env_value(env_file, env_var):
        typer.echo(f"Removed {provider} credential {env_var}")
    else:
        typer.echo(f"{env_var} came from the process environment; unset it there.")


def _interactive_reset() -> None:
    provider = _pick_provider("Provider to reset cooldowns for")
    if not provider:
        typer.echo("Cancelled.")
        return
    typer.echo(f"Reset status on 0 {provider} credentials")


def _interactive_strategy() -> None:
    provider = _pick_provider("Provider to set rotation strategy for")
    if not provider:
        typer.echo("Cancelled.")
        return

    strategies = [
        ("fill_first", "Use first key until exhausted, then next"),
        ("round_robin", "Cycle through keys evenly"),
        ("least_used", "Always pick the least-used key"),
        ("random", "Random selection"),
    ]

    typer.echo(f"Current strategy for {provider}: fill_first")
    for index, (name, description) in enumerate(strategies, 1):
        typer.echo(f"  {index}. {name:<12} - {description}")

    choice = _prompt_line("Strategy [1-4]: ")
    if not choice:
        typer.echo("Cancelled.")
        return
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(strategies):
        typer.echo("Invalid strategy.")
        return

    strategy = strategies[int(choice) - 1][0]
    typer.echo(f"Set {provider} strategy to: {strategy}")


def _interactive_auth(env_file: Path) -> None:
    register()
    _print_credential_pool_status(env_file)
    typer.echo("What would you like to do?")
    typer.echo("  1. Add a credential")
    typer.echo("  2. Remove a credential")
    typer.echo("  3. Reset cooldowns for a provider")
    typer.echo("  4. Set rotation strategy for a provider")
    typer.echo("  5. Exit")
    choice = _prompt_line("\nChoice: ")
    typer.echo("")

    if choice in {"", "5", "q", "quit", "exit"}:
        return
    if choice == "1":
        _interactive_add(env_file)
    elif choice == "2":
        _interactive_remove(env_file)
    elif choice == "3":
        _interactive_reset()
    elif choice == "4":
        _interactive_strategy()
    else:
        typer.echo("Invalid choice.")


@auth_app.callback()
def auth_root(
    ctx: typer.Context,
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to read or update.",
    ),
) -> None:
    """Open the interactive auth setup flow when no auth subcommand is provided."""
    if ctx.invoked_subcommand is None:
        _interactive_auth(env_file)


@providers_app.command("list")
def list_providers() -> None:
    """List installed provider profiles."""
    register()
    rows = [
        (
            profile.name,
            profile.display_name,
            profile.api_mode,
            profile.runtime_status,
        )
        for profile in sorted(list_provider_profiles(), key=lambda item: item.name)
    ]
    _print_table(rows)


@providers_app.command("setup")
def setup_provider(
    provider: str = typer.Argument(..., help="Provider name, for example minimax"),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "--key",
        help="API key to save. If omitted, you will be prompted.",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Optional provider base URL override to save.",
    ),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to update.",
    ),
) -> None:
    """Configure API-key providers by writing their environment variables."""
    _save_provider_credentials(provider, api_key, base_url, env_file)


@providers_app.command("doctor")
def doctor_provider(
    provider: str | None = typer.Argument(None, help="Provider name to check"),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to read before checking providers.",
    ),
) -> None:
    """Check provider runtime availability."""
    register()
    _load_env_file(env_file)

    profiles = list_provider_profiles()
    names = [provider] if provider else [profile.name for profile in profiles]

    for name in names:
        status = _provider_status(name, env_file)
        typer.echo(f"{name}: {status}")


@providers_app.command("thinking-level")
def provider_thinking_level(
    provider: str = typer.Argument(..., help="Provider name, for example xiaomi"),
    level: str = typer.Argument(
        ...,
        help="Thinking level: low, medium, high, minimal, xhigh, or none",
    ),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to update.",
    ),
) -> None:
    """Set a provider thinking level."""
    _save_thinking_level(provider, level, env_file)


@auth_app.command("add")
def auth_add(
    provider: str = typer.Argument(..., help="Provider name, for example minimax"),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "--key",
        help="API key to save. If omitted, you will be prompted.",
    ),
    auth_type: str = typer.Option(
        "api_key",
        "--type",
        help="Authentication type. Currently only api_key is supported.",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Optional provider base URL override to save.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Do not open a browser automatically for OAuth login.",
    ),
    timeout_seconds: float = typer.Option(
        15.0,
        "--timeout",
        help="Network timeout in seconds for OAuth login requests.",
    ),
    region: str = typer.Option(
        "global",
        "--region",
        help="OAuth provider region when supported, for example global or cn.",
    ),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to update.",
    ),
) -> None:
    """Add provider credentials. Hermes-style alias for provider setup."""
    requested_auth_type = auth_type.lower()
    register()
    profile = get_provider_profile(provider)
    if profile is None:
        raise typer.BadParameter(f"Unknown provider: {provider}")

    if (
        requested_auth_type in {"oauth", "oauth_device_code", "oauth_external"}
        or profile.auth_type in {"oauth_device_code", "oauth_external"}
    ):
        if not can_login_provider(profile.name):
            typer.echo(f"{profile.display_name} does not have a plugin login flow yet.")
            raise typer.Exit(1)
        _run_login_flow(
            profile.name,
            no_browser=no_browser,
            timeout_seconds=timeout_seconds,
            region=region,
        )
        return

    if requested_auth_type not in {"api_key", "key"}:
        typer.echo(f"Unsupported auth type: {auth_type}. Use api_key.")
        raise typer.Exit(1)
    _save_provider_credentials(provider, api_key, base_url, env_file)


@auth_app.command("thinking-level")
def auth_thinking_level(
    provider: str = typer.Argument(..., help="Provider name, for example xiaomi"),
    level: str = typer.Argument(
        ...,
        help="Thinking level: low, medium, high, minimal, xhigh, or none",
    ),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to update.",
    ),
) -> None:
    """Set a provider thinking level. Hermes-style alias for providers."""
    _save_thinking_level(provider, level, env_file)


@auth_app.command("setup")
def auth_setup(
    provider: str = typer.Argument(..., help="Provider name, for example minimax"),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "--key",
        help="API key to save. If omitted, you will be prompted.",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Optional provider base URL override to save.",
    ),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to update.",
    ),
) -> None:
    """Set up provider credentials."""
    _save_provider_credentials(provider, api_key, base_url, env_file)


@auth_app.command("status")
def auth_status(
    provider: str | None = typer.Argument(None, help="Provider name to check"),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to read before checking providers.",
    ),
) -> None:
    """Show provider auth status."""
    register()
    profiles = list_provider_profiles()
    names = [provider] if provider else [profile.name for profile in profiles]
    for name in names:
        profile = get_provider_profile(name)
        if profile and profile.auth_type in {"oauth_device_code", "oauth_external"}:
            status = get_auth_status(profile.name)
            state = "logged in" if status.get("logged_in") else "not logged in"
            detail = f" ({status.get('source')})" if status.get("source") else ""
            typer.echo(f"{name}: {state}{detail}")
        else:
            typer.echo(f"{name}: {_provider_status(name, env_file)}")


@auth_app.command("list")
def auth_list(
    provider: str | None = typer.Argument(None, help="Provider name to show"),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to read before listing credentials.",
    ),
) -> None:
    """List configured provider credentials."""
    if provider:
        entries = _credential_entries(env_file, provider)
        if not entries:
            typer.echo(f"No credentials for {provider}.")
            return

        typer.echo(f"{provider} ({len(entries)} credentials):")
        for index, (_, label, auth_type, source) in enumerate(entries, 1):
            marker = " ←" if index == 1 else ""
            typer.echo(f"  #{index:<2} {label:<22} {auth_type:<8} {source}{marker}")
        return

    _print_credential_pool_status(env_file)


@auth_app.command("remove")
def auth_remove(
    provider: str = typer.Argument(..., help="Provider name to remove credentials for"),
    target: str = typer.Argument("1", help="Credential number or env var to remove"),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to update.",
    ),
) -> None:
    """Remove a configured provider credential."""
    entries = _credential_entries(env_file, provider)
    if not entries:
        typer.echo(f"No credentials for {provider}.")
        raise typer.Exit(1)

    selected = entries[0]
    if target.isdigit():
        selected_index = int(target) - 1
        if selected_index < 0 or selected_index >= len(entries):
            typer.echo(f"No credential #{target} for {provider}.")
            raise typer.Exit(1)
        selected = entries[selected_index]
    else:
        matches = [entry for entry in entries if entry[1] == target]
        if not matches:
            typer.echo(f"No credential matching {target!r} for {provider}.")
            raise typer.Exit(1)
        selected = matches[0]

    _, env_var, _, _ = selected
    if _remove_env_value(env_file, env_var):
        typer.echo(f"Removed {provider} credential {env_var}")
        return

    typer.echo(f"{env_var} came from the process environment; unset it there.")
    raise typer.Exit(1)


@auth_app.command("reset")
def auth_reset(
    provider: str = typer.Argument(..., help="Provider name to reset"),
) -> None:
    """Reset provider credential cooldowns."""
    typer.echo(f"Reset status on 0 {provider} credentials")


@auth_app.command("strategy")
def auth_strategy(
    provider: str = typer.Argument(..., help="Provider name to configure"),
    strategy: str = typer.Argument(
        ..., help="Rotation strategy: fill_first, round_robin, least_used, random"
    ),
) -> None:
    """Set provider credential rotation strategy."""
    valid = {"fill_first", "round_robin", "least_used", "random"}
    if strategy not in valid:
        typer.echo(f"Invalid strategy: {strategy}")
        typer.echo(f"Valid strategies: {', '.join(sorted(valid))}")
        raise typer.Exit(1)
    typer.echo(f"Set {provider} strategy to: {strategy}")


@auth_app.command("logout")
def auth_logout(
    provider: str = typer.Argument(..., help="Provider name to log out"),
    env_file: Path = typer.Option(
        Path(".env"),
        "--env-file",
        help="Environment file to update.",
    ),
) -> None:
    """Remove all local credentials for a provider."""
    entries = _credential_entries(env_file, provider)
    profile = get_provider_profile(provider)
    if profile and profile.auth_type in {"oauth_device_code", "oauth_external"}:
        if clear_provider_auth_state(profile.name):
            typer.echo(f"Removed {profile.name} login state")
        else:
            typer.echo(f"No login state for {profile.name}.")
        return

    if not entries:
        typer.echo(f"No credentials for {provider}.")
        return

    removed = 0
    for _, env_var, _, source in entries:
        if source == "manual" and _remove_env_value(env_file, env_var):
            removed += 1

    typer.echo(f"Removed {removed} {provider} credentials")
