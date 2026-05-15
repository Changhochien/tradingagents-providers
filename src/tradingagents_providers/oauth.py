"""OAuth and external-login helpers for TradingAgents provider plugins."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

AUTH_STORE_VERSION = 1
DEFAULT_NOUS_PORTAL_URL = "https://portal.nousresearch.com"
DEFAULT_NOUS_INFERENCE_URL = "https://inference-api.nousresearch.com/v1"
DEFAULT_NOUS_CLIENT_ID = "hermes-cli"
DEFAULT_NOUS_SCOPE = "inference:mint_agent_key"
DEFAULT_AGENT_KEY_MIN_TTL_SECONDS = 30 * 60
ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120

DEFAULT_QWEN_BASE_URL = "https://portal.qwen.ai/v1"
QWEN_OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56"
QWEN_OAUTH_TOKEN_URL = "https://chat.qwen.ai/api/v1/oauth2/token"
QWEN_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120

MINIMAX_OAUTH_CLIENT_ID = "78257093-7e40-4613-99e0-527b14b39113"
MINIMAX_OAUTH_SCOPE = "group_id profile model.completion"
MINIMAX_OAUTH_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:user_code"
MINIMAX_OAUTH_GLOBAL_BASE = "https://api.minimax.io"
MINIMAX_OAUTH_CN_BASE = "https://api.minimaxi.com"
MINIMAX_OAUTH_GLOBAL_INFERENCE = "https://api.minimax.io/anthropic"
MINIMAX_OAUTH_CN_INFERENCE = "https://api.minimaxi.com/anthropic"
MINIMAX_OAUTH_REFRESH_SKEW_SECONDS = 60

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"

LOGIN_CAPABLE_PROVIDERS = frozenset(
    {"nous", "qwen-oauth", "minimax-oauth", "openai-codex", "google-gemini-cli"}
)
RUNTIME_OAUTH_PROVIDERS = frozenset(
    {"nous", "qwen-oauth", "minimax-oauth", "openai-codex", "google-gemini-cli"}
)


class AuthError(Exception):
    """Raised when provider login state cannot be used."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        code: str = "auth_error",
        relogin_required: bool = False,
    ):
        self.provider = provider
        self.code = code
        self.relogin_required = relogin_required
        super().__init__(message)


def can_login_provider(provider: str) -> bool:
    return provider.lower() in LOGIN_CAPABLE_PROVIDERS


def can_run_oauth_provider(provider: str) -> bool:
    return provider.lower() in RUNTIME_OAUTH_PROVIDERS


def get_auth_store_path() -> Path:
    root = os.getenv("TRADINGAGENTS_PROVIDERS_HOME", "").strip()
    if root:
        return Path(root).expanduser() / "auth.json"
    return Path.home() / ".tradingagents" / "providers" / "auth.json"


def _load_auth_store() -> dict[str, Any]:
    path = get_auth_store_path()
    if not path.exists():
        return {"version": AUTH_STORE_VERSION, "providers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(
            f"Failed to read provider auth store {path}: {exc}",
            provider="auth-store",
            code="auth_store_read_failed",
        ) from exc
    if not isinstance(data, dict):
        return {"version": AUTH_STORE_VERSION, "providers": {}}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        data["providers"] = {}
    data.setdefault("version", AUTH_STORE_VERSION)
    return data


def _save_auth_store(data: dict[str, Any]) -> Path:
    path = get_auth_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    fd = os.open(
        str(tmp),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        stat.S_IRUSR | stat.S_IWUSR,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def get_provider_auth_state(provider: str) -> dict[str, Any] | None:
    state = _load_auth_store().get("providers", {}).get(provider)
    return dict(state) if isinstance(state, dict) else None


def save_provider_auth_state(provider: str, state: dict[str, Any]) -> Path:
    store = _load_auth_store()
    providers = store.setdefault("providers", {})
    providers[provider] = dict(state)
    return _save_auth_store(store)


def clear_provider_auth_state(provider: str) -> bool:
    store = _load_auth_store()
    providers = store.setdefault("providers", {})
    existed = provider in providers
    providers.pop(provider, None)
    if existed:
        _save_auth_store(store)
    return existed


def _json_or_error(response: requests.Response, provider: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AuthError(
            f"{provider} returned non-JSON response with status {response.status_code}.",
            provider=provider,
            code="invalid_json",
        ) from exc
    if not isinstance(payload, dict):
        raise AuthError(
            f"{provider} returned an invalid response.",
            provider=provider,
            code="invalid_response",
        )
    return payload


def _parse_iso_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_expiring(expires_at: Any, skew_seconds: int) -> bool:
    epoch = _parse_iso_epoch(expires_at)
    if epoch is None:
        return True
    return time.time() + max(0, int(skew_seconds)) >= epoch


def _expires_at_from_ttl(seconds: Any) -> str:
    try:
        ttl = int(seconds)
    except (TypeError, ValueError):
        ttl = 3600
    return datetime.fromtimestamp(
        time.time() + max(1, ttl),
        tz=timezone.utc,
    ).isoformat()


def login_provider(
    provider: str,
    *,
    no_browser: bool = False,
    timeout_seconds: float = 15.0,
    region: str = "global",
) -> dict[str, Any]:
    provider = provider.lower()
    if provider == "nous":
        return login_nous(no_browser=no_browser, timeout_seconds=timeout_seconds)
    if provider == "minimax-oauth":
        return login_minimax_oauth(
            no_browser=no_browser,
            timeout_seconds=timeout_seconds,
            region=region,
        )
    if provider == "openai-codex":
        return login_openai_codex(no_browser=no_browser, timeout_seconds=timeout_seconds)
    if provider == "google-gemini-cli":
        from tradingagents_providers.google_oauth import import_gemini_cli_credentials

        state = import_gemini_cli_credentials()
        return {
            "provider": "google-gemini-cli",
            "source": "google-oauth",
            "base_url": state.get("base_url"),
        }
    if provider == "qwen-oauth":
        return import_qwen_cli_credentials()
    raise AuthError(
        f"{provider} does not expose a plugin-managed login flow.",
        provider=provider,
        code="unsupported_provider",
    )


def resolve_oauth_runtime_credentials(provider: str) -> dict[str, Any]:
    provider = provider.lower()
    if provider == "nous":
        return resolve_nous_runtime_credentials()
    if provider == "qwen-oauth":
        return resolve_qwen_runtime_credentials()
    if provider == "minimax-oauth":
        return resolve_minimax_oauth_runtime_credentials()
    if provider == "openai-codex":
        return resolve_codex_runtime_credentials()
    if provider == "google-gemini-cli":
        from tradingagents_providers.google_oauth import resolve_google_gemini_cli_credentials

        return resolve_google_gemini_cli_credentials()
    raise AuthError(
        f"{provider} login may be available, but no runtime adapter is implemented.",
        provider=provider,
        code="runtime_adapter_missing",
    )


def get_auth_status(provider: str) -> dict[str, Any]:
    provider = provider.lower()
    try:
        if provider in RUNTIME_OAUTH_PROVIDERS:
            creds = resolve_oauth_runtime_credentials(provider)
            return {
                "provider": provider,
                "logged_in": True,
                "source": creds.get("source"),
                "base_url": creds.get("base_url"),
                "auth_store": str(get_auth_store_path()),
            }
        state = get_provider_auth_state(provider)
        return {
            "provider": provider,
            "logged_in": bool(state and state.get("access_token")),
            "source": "auth-store" if state else None,
            "auth_store": str(get_auth_store_path()),
        }
    except AuthError as exc:
        return {
            "provider": provider,
            "logged_in": False,
            "error": str(exc),
            "code": exc.code,
            "auth_store": str(get_auth_store_path()),
        }


def login_nous(*, no_browser: bool = False, timeout_seconds: float = 15.0) -> dict:
    portal = os.getenv("NOUS_PORTAL_BASE_URL", DEFAULT_NOUS_PORTAL_URL).rstrip("/")
    inference = os.getenv("NOUS_INFERENCE_BASE_URL", DEFAULT_NOUS_INFERENCE_URL).rstrip("/")
    session = requests.Session()
    response = session.post(
        f"{portal}/api/oauth/device/code",
        data={"client_id": DEFAULT_NOUS_CLIENT_ID, "scope": DEFAULT_NOUS_SCOPE},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    device = _json_or_error(response, "nous")
    for field in ("device_code", "user_code", "verification_uri", "expires_in"):
        if field not in device:
            raise AuthError(
                f"Nous device-code response missing {field}.",
                provider="nous",
                code="device_response_incomplete",
            )

    verification_url = device.get("verification_uri_complete") or device["verification_uri"]
    print("To continue, open this URL and approve the request:")
    print(f"  {verification_url}")
    print(f"User code: {device['user_code']}")
    if not no_browser:
        webbrowser.open(str(verification_url))

    deadline = time.monotonic() + int(device.get("expires_in") or 900)
    interval = max(1, int(device.get("interval") or 2))
    token_payload = None
    while time.monotonic() < deadline:
        time.sleep(interval)
        poll = session.post(
            f"{portal}/api/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": DEFAULT_NOUS_CLIENT_ID,
                "device_code": device["device_code"],
            },
            timeout=timeout_seconds,
        )
        if poll.status_code == 200:
            token_payload = _json_or_error(poll, "nous")
            break
        payload = _json_or_error(poll, "nous")
        if payload.get("error") == "authorization_pending":
            continue
        if payload.get("error") == "slow_down":
            interval = min(interval + 1, 30)
            continue
        raise AuthError(
            str(payload.get("error_description") or payload.get("error") or "Nous login failed."),
            provider="nous",
            code=str(payload.get("error") or "login_failed"),
        )

    if token_payload is None:
        raise AuthError("Nous login timed out.", provider="nous", code="timeout")

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "provider": "nous",
        "portal_base_url": portal,
        "inference_base_url": token_payload.get("inference_base_url") or inference,
        "client_id": DEFAULT_NOUS_CLIENT_ID,
        "access_token": token_payload["access_token"],
        "refresh_token": token_payload.get("refresh_token"),
        "token_type": token_payload.get("token_type", "Bearer"),
        "scope": token_payload.get("scope", DEFAULT_NOUS_SCOPE),
        "obtained_at": now,
        "expires_at": _expires_at_from_ttl(token_payload.get("expires_in")),
    }
    save_provider_auth_state("nous", state)
    return resolve_nous_runtime_credentials()


def _refresh_nous_state(state: dict[str, Any]) -> dict[str, Any]:
    if not _is_expiring(state.get("expires_at"), ACCESS_TOKEN_REFRESH_SKEW_SECONDS):
        return state
    refresh_token = str(state.get("refresh_token") or "").strip()
    if not refresh_token:
        raise AuthError(
            "Nous session expired and no refresh token is available.",
            provider="nous",
            code="refresh_token_missing",
            relogin_required=True,
        )
    portal = str(state.get("portal_base_url") or DEFAULT_NOUS_PORTAL_URL).rstrip("/")
    response = requests.post(
        f"{portal}/api/oauth/token",
        headers={"x-nous-refresh-token": refresh_token},
        data={"grant_type": "refresh_token", "client_id": state.get("client_id") or DEFAULT_NOUS_CLIENT_ID},
        timeout=15,
    )
    if response.status_code != 200:
        raise AuthError(
            "Nous token refresh failed. Re-run `tradingagents auth add nous --type oauth`.",
            provider="nous",
            code="refresh_failed",
            relogin_required=True,
        )
    payload = _json_or_error(response, "nous")
    state = dict(state)
    state["access_token"] = payload["access_token"]
    state["refresh_token"] = payload.get("refresh_token") or refresh_token
    state["token_type"] = payload.get("token_type", state.get("token_type", "Bearer"))
    state["expires_at"] = _expires_at_from_ttl(payload.get("expires_in"))
    if payload.get("inference_base_url"):
        state["inference_base_url"] = payload["inference_base_url"]
    save_provider_auth_state("nous", state)
    return state


def resolve_nous_runtime_credentials() -> dict[str, Any]:
    state = get_provider_auth_state("nous")
    if not state:
        raise AuthError(
            "Not logged into Nous. Run `tradingagents auth add nous --type oauth`.",
            provider="nous",
            code="not_logged_in",
            relogin_required=True,
        )
    state = _refresh_nous_state(state)
    if not _agent_key_is_usable(state):
        portal = str(state.get("portal_base_url") or DEFAULT_NOUS_PORTAL_URL).rstrip("/")
        response = requests.post(
            f"{portal}/api/oauth/agent-key",
            headers={"Authorization": f"Bearer {state['access_token']}"},
            json={"min_ttl_seconds": DEFAULT_AGENT_KEY_MIN_TTL_SECONDS},
            timeout=15,
        )
        if response.status_code != 200:
            raise AuthError(
                "Nous agent-key mint failed. Re-run `tradingagents auth add nous --type oauth`.",
                provider="nous",
                code="agent_key_failed",
                relogin_required=response.status_code in {401, 403},
            )
        minted = _json_or_error(response, "nous")
        state = dict(state)
        state["agent_key"] = minted["api_key"]
        state["agent_key_id"] = minted.get("key_id")
        state["agent_key_expires_at"] = minted.get("expires_at")
        state["agent_key_expires_in"] = minted.get("expires_in")
        if minted.get("inference_base_url"):
            state["inference_base_url"] = minted["inference_base_url"]
        save_provider_auth_state("nous", state)
    return {
        "provider": "nous",
        "base_url": str(state.get("inference_base_url") or DEFAULT_NOUS_INFERENCE_URL).rstrip("/"),
        "api_key": state["agent_key"],
        "source": "oauth",
        "auth_store": str(get_auth_store_path()),
    }


def _agent_key_is_usable(state: dict[str, Any]) -> bool:
    key = state.get("agent_key")
    if not isinstance(key, str) or not key.strip():
        return False
    return not _is_expiring(state.get("agent_key_expires_at"), DEFAULT_AGENT_KEY_MIN_TTL_SECONDS)


def _qwen_cli_auth_path() -> Path:
    return Path.home() / ".qwen" / "oauth_creds.json"


def import_qwen_cli_credentials() -> dict[str, Any]:
    tokens = _read_qwen_cli_tokens()
    if not str(tokens.get("access_token", "") or "").strip():
        raise AuthError(
            "Qwen CLI credentials are missing access_token. Run `qwen auth qwen-oauth` first.",
            provider="qwen-oauth",
            code="access_token_missing",
        )
    return {
        "provider": "qwen-oauth",
        "base_url": DEFAULT_QWEN_BASE_URL,
        "api_key": tokens["access_token"],
        "source": "qwen-cli",
        "auth_file": str(_qwen_cli_auth_path()),
    }


def _read_qwen_cli_tokens() -> dict[str, Any]:
    path = _qwen_cli_auth_path()
    if not path.exists():
        raise AuthError(
            "Qwen CLI credentials not found. Run `qwen auth qwen-oauth` first.",
            provider="qwen-oauth",
            code="not_logged_in",
            relogin_required=True,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(
            f"Failed to read Qwen CLI credentials from {path}: {exc}",
            provider="qwen-oauth",
            code="read_failed",
        ) from exc
    if not isinstance(data, dict):
        raise AuthError("Invalid Qwen CLI credentials.", provider="qwen-oauth", code="invalid")
    return data


def _qwen_access_token_is_expiring(expiry_date_ms: Any) -> bool:
    try:
        expiry_ms = int(expiry_date_ms)
    except (TypeError, ValueError):
        return True
    return (time.time() + QWEN_ACCESS_TOKEN_REFRESH_SKEW_SECONDS) * 1000 >= expiry_ms


def _refresh_qwen_cli_tokens(tokens: dict[str, Any]) -> dict[str, Any]:
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        raise AuthError(
            "Qwen OAuth refresh token missing. Re-run `qwen auth qwen-oauth`.",
            provider="qwen-oauth",
            code="refresh_token_missing",
            relogin_required=True,
        )
    response = requests.post(
        QWEN_OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": QWEN_OAUTH_CLIENT_ID,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise AuthError(
            "Qwen OAuth refresh failed. Re-run `qwen auth qwen-oauth`.",
            provider="qwen-oauth",
            code="refresh_failed",
            relogin_required=True,
        )
    payload = _json_or_error(response, "qwen-oauth")
    expires_in = int(payload.get("expires_in") or 6 * 60 * 60)
    refreshed = {
        "access_token": str(payload.get("access_token") or "").strip(),
        "refresh_token": str(payload.get("refresh_token") or refresh_token).strip(),
        "token_type": str(payload.get("token_type") or tokens.get("token_type") or "Bearer"),
        "resource_url": str(payload.get("resource_url") or tokens.get("resource_url") or "portal.qwen.ai"),
        "expiry_date": int(time.time() * 1000) + max(1, expires_in) * 1000,
    }
    _save_qwen_cli_tokens(refreshed)
    return refreshed


def _save_qwen_cli_tokens(tokens: dict[str, Any]) -> None:
    path = _qwen_cli_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(tokens, indent=2, sort_keys=True) + "\n")


def resolve_qwen_runtime_credentials() -> dict[str, Any]:
    tokens = _read_qwen_cli_tokens()
    if _qwen_access_token_is_expiring(tokens.get("expiry_date")):
        tokens = _refresh_qwen_cli_tokens(tokens)
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        raise AuthError(
            "Qwen OAuth access token missing. Re-run `qwen auth qwen-oauth`.",
            provider="qwen-oauth",
            code="access_token_missing",
            relogin_required=True,
        )
    return {
        "provider": "qwen-oauth",
        "base_url": os.getenv("TRADINGAGENTS_QWEN_BASE_URL", DEFAULT_QWEN_BASE_URL).rstrip("/"),
        "api_key": access_token,
        "source": "qwen-cli",
        "auth_file": str(_qwen_cli_auth_path()),
    }


def login_minimax_oauth(
    *,
    no_browser: bool = False,
    timeout_seconds: float = 15.0,
    region: str = "global",
) -> dict[str, Any]:
    portal = MINIMAX_OAUTH_CN_BASE if region == "cn" else MINIMAX_OAUTH_GLOBAL_BASE
    inference = MINIMAX_OAUTH_CN_INFERENCE if region == "cn" else MINIMAX_OAUTH_GLOBAL_INFERENCE
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    response = requests.post(
        f"{portal}/oauth/code",
        data={
            "response_type": "code",
            "client_id": MINIMAX_OAUTH_CLIENT_ID,
            "scope": MINIMAX_OAUTH_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
        headers={"Accept": "application/json", "x-request-id": str(uuid.uuid4())},
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise AuthError("MiniMax OAuth authorization failed.", provider="minimax-oauth", code="authorization_failed")
    code_data = _json_or_error(response, "minimax-oauth")
    verification_url = str(code_data["verification_uri"])
    print("To continue, open this URL and approve the request:")
    print(f"  {verification_url}")
    if code_data.get("user_code"):
        print(f"User code: {code_data['user_code']}")
    if not no_browser:
        webbrowser.open(verification_url)

    token_data = _minimax_poll_token(portal, code_data, verifier, timeout_seconds)
    now = datetime.now(timezone.utc)
    expires_at = _minimax_expiry_iso(int(token_data["expired_in"]), now=now)
    state_data = {
        "provider": "minimax-oauth",
        "region": region,
        "portal_base_url": portal,
        "inference_base_url": inference,
        "client_id": MINIMAX_OAUTH_CLIENT_ID,
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "token_type": token_data.get("token_type", "Bearer"),
        "obtained_at": now.isoformat(),
        "expires_at": expires_at,
    }
    save_provider_auth_state("minimax-oauth", state_data)
    return resolve_minimax_oauth_runtime_credentials()


def _minimax_poll_token(
    portal: str,
    code_data: dict[str, Any],
    verifier: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    raw_expiry = int(code_data["expired_in"])
    now_ms = int(time.time() * 1000)
    deadline = raw_expiry / 1000.0 if raw_expiry > now_ms // 2 else time.time() + max(1, raw_expiry)
    interval = max(2.0, int(code_data.get("interval") or 2000) / 1000.0)
    while time.time() < deadline:
        response = requests.post(
            f"{portal}/oauth/token",
            data={
                "grant_type": MINIMAX_OAUTH_GRANT_TYPE,
                "client_id": MINIMAX_OAUTH_CLIENT_ID,
                "user_code": code_data["user_code"],
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
        )
        payload = _json_or_error(response, "minimax-oauth")
        if response.status_code != 200:
            raise AuthError("MiniMax OAuth token exchange failed.", provider="minimax-oauth", code="token_failed")
        if payload.get("status") == "success":
            return payload
        if payload.get("status") == "error":
            raise AuthError("MiniMax OAuth was denied.", provider="minimax-oauth", code="authorization_denied")
        time.sleep(interval)
    raise AuthError("MiniMax OAuth timed out.", provider="minimax-oauth", code="timeout")


def _minimax_expiry_iso(expired_in: int, *, now: datetime) -> str:
    now_ms = int(now.timestamp() * 1000)
    epoch = expired_in / 1000.0 if expired_in > now_ms // 2 else now.timestamp() + max(1, expired_in)
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def resolve_minimax_oauth_runtime_credentials() -> dict[str, Any]:
    state = get_provider_auth_state("minimax-oauth")
    if not state:
        raise AuthError(
            "Not logged into MiniMax OAuth. Run `tradingagents auth add minimax-oauth --type oauth`.",
            provider="minimax-oauth",
            code="not_logged_in",
            relogin_required=True,
        )
    if _is_expiring(state.get("expires_at"), MINIMAX_OAUTH_REFRESH_SKEW_SECONDS):
        state = _refresh_minimax_oauth_state(state)
    return {
        "provider": "minimax-oauth",
        "api_key": state["access_token"],
        "base_url": str(state["inference_base_url"]).rstrip("/"),
        "source": "oauth",
        "auth_store": str(get_auth_store_path()),
    }


def _refresh_minimax_oauth_state(state: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{state['portal_base_url']}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": state["client_id"],
            "refresh_token": state["refresh_token"],
        },
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if response.status_code != 200:
        raise AuthError(
            "MiniMax OAuth refresh failed. Re-run login.",
            provider="minimax-oauth",
            code="refresh_failed",
            relogin_required=True,
        )
    payload = _json_or_error(response, "minimax-oauth")
    if payload.get("status") != "success":
        raise AuthError("MiniMax OAuth refresh failed.", provider="minimax-oauth", code="refresh_failed")
    new_state = dict(state)
    new_state["access_token"] = payload["access_token"]
    new_state["refresh_token"] = payload.get("refresh_token") or state["refresh_token"]
    new_state["expires_at"] = _minimax_expiry_iso(
        int(payload["expired_in"]),
        now=datetime.now(timezone.utc),
    )
    save_provider_auth_state("minimax-oauth", new_state)
    return new_state


def resolve_codex_runtime_credentials() -> dict[str, Any]:
    state = get_provider_auth_state("openai-codex")
    if not state:
        raise AuthError(
            "Not logged into OpenAI Codex. Run `tradingagents auth add openai-codex --type oauth`.",
            provider="openai-codex",
            code="not_logged_in",
            relogin_required=True,
        )

    access_token = state.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AuthError(
            "OpenAI Codex login is missing an access token. Re-run login.",
            provider="openai-codex",
            code="missing_access_token",
            relogin_required=True,
        )

    if _codex_access_token_is_expiring(access_token):
        state = _refresh_codex_oauth_state(state)
        access_token = state["access_token"]

    return {
        "provider": "openai-codex",
        "api_key": access_token,
        "base_url": str(
            os.getenv("TRADINGAGENTS_CODEX_BASE_URL")
            or state.get("base_url")
            or DEFAULT_CODEX_BASE_URL
        ).rstrip("/"),
        "source": "oauth",
        "auth_store": str(get_auth_store_path()),
    }


def _codex_access_token_is_expiring(token: str, skew_seconds: int = ACCESS_TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return False
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)):
            return False
        return time.time() + max(0, int(skew_seconds)) >= float(exp)
    except Exception:
        return False


def _refresh_codex_oauth_state(state: dict[str, Any]) -> dict[str, Any]:
    refresh_token = state.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise AuthError(
            "OpenAI Codex login is expired and has no refresh token. Re-run login.",
            provider="openai-codex",
            code="missing_refresh_token",
            relogin_required=True,
        )

    response = requests.post(
        CODEX_OAUTH_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CODEX_OAUTH_CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    if response.status_code != 200:
        raise AuthError(
            "OpenAI Codex token refresh failed. Re-run login.",
            provider="openai-codex",
            code="refresh_failed",
            relogin_required=response.status_code in {400, 401, 403},
        )

    payload = _json_or_error(response, "openai-codex")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AuthError(
            "OpenAI Codex token refresh did not return an access token.",
            provider="openai-codex",
            code="refresh_missing_access_token",
            relogin_required=True,
        )

    new_state = dict(state)
    new_state["access_token"] = access_token
    if isinstance(payload.get("refresh_token"), str) and payload["refresh_token"].strip():
        new_state["refresh_token"] = payload["refresh_token"]
    new_state["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    save_provider_auth_state("openai-codex", new_state)
    return new_state


def login_openai_codex(*, no_browser: bool = False, timeout_seconds: float = 15.0) -> dict[str, Any]:
    issuer = "https://auth.openai.com"
    response = requests.post(
        f"{issuer}/api/accounts/deviceauth/usercode",
        json={"client_id": CODEX_OAUTH_CLIENT_ID},
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise AuthError("Codex device-code request failed.", provider="openai-codex", code="device_failed")
    device = _json_or_error(response, "openai-codex")
    print("To continue, open this URL and approve the request:")
    print(f"  {issuer}/codex/device")
    print(f"User code: {device['user_code']}")
    if not no_browser:
        webbrowser.open(f"{issuer}/codex/device")

    deadline = time.monotonic() + 15 * 60
    interval = max(3, int(device.get("interval") or 5))
    code_payload = None
    while time.monotonic() < deadline:
        time.sleep(interval)
        poll = requests.post(
            f"{issuer}/api/accounts/deviceauth/token",
            json={"device_auth_id": device["device_auth_id"], "user_code": device["user_code"]},
            timeout=timeout_seconds,
        )
        if poll.status_code == 200:
            code_payload = _json_or_error(poll, "openai-codex")
            break
        if poll.status_code in {403, 404}:
            continue
        raise AuthError("Codex device-code polling failed.", provider="openai-codex", code="poll_failed")
    if code_payload is None:
        raise AuthError("Codex login timed out.", provider="openai-codex", code="timeout")

    token = requests.post(
        CODEX_OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code_payload["authorization_code"],
            "redirect_uri": f"{issuer}/deviceauth/callback",
            "client_id": CODEX_OAUTH_CLIENT_ID,
            "code_verifier": code_payload["code_verifier"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout_seconds,
    )
    if token.status_code != 200:
        raise AuthError("Codex token exchange failed.", provider="openai-codex", code="token_failed")
    payload = _json_or_error(token, "openai-codex")
    state = {
        "provider": "openai-codex",
        "base_url": DEFAULT_CODEX_BASE_URL,
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "auth_mode": "chatgpt",
        "obtained_at": datetime.now(timezone.utc).isoformat(),
    }
    save_provider_auth_state("openai-codex", state)
    return {"provider": "openai-codex", "source": "oauth", "base_url": DEFAULT_CODEX_BASE_URL}
