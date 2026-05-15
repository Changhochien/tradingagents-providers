"""Google Gemini CLI OAuth helpers for Cloud Code Assist."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from tradingagents_providers.oauth import (
    AuthError,
    get_auth_store_path,
    get_provider_auth_state,
    save_provider_auth_state,
)

DEFAULT_GEMINI_CLOUDCODE_BASE_URL = "cloudcode-pa://google"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GEMINI_CLI_OAUTH_PATH = Path.home() / ".gemini" / "oauth_creds.json"
REFRESH_SKEW_SECONDS = 60

_DEFAULT_CLIENT_ID = (
    "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j"
    ".apps.googleusercontent.com"
)
_DEFAULT_CLIENT_SECRET = "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"


def import_gemini_cli_credentials() -> dict[str, Any]:
    path = Path(os.getenv("GEMINI_OAUTH_CREDS_PATH", "") or GEMINI_CLI_OAUTH_PATH).expanduser()
    if not path.is_file():
        raise AuthError(
            f"Gemini CLI OAuth credentials not found at {path}. Run Gemini CLI login first.",
            provider="google-gemini-cli",
            code="not_logged_in",
            relogin_required=True,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(
            f"Failed to read Gemini CLI OAuth credentials at {path}.",
            provider="google-gemini-cli",
            code="invalid_credentials_file",
        ) from exc

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise AuthError(
            "Gemini CLI OAuth credentials are missing access_token or refresh_token.",
            provider="google-gemini-cli",
            code="invalid_credentials",
            relogin_required=True,
        )

    state = {
        "provider": "google-gemini-cli",
        "base_url": DEFAULT_GEMINI_CLOUDCODE_BASE_URL,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expiry_date": payload.get("expiry_date"),
        "token_type": payload.get("token_type", "Bearer"),
        "scope": payload.get("scope"),
        "source_file": str(path),
    }
    save_provider_auth_state("google-gemini-cli", state)
    return state


def resolve_google_gemini_cli_credentials(*, force_refresh: bool = False) -> dict[str, Any]:
    state = get_provider_auth_state("google-gemini-cli")
    if not state:
        state = import_gemini_cli_credentials()

    access_token = state.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        state = import_gemini_cli_credentials()
        access_token = state.get("access_token")

    if force_refresh or _is_expiring_ms(state.get("expiry_date")):
        state = _refresh_google_state(state)
        access_token = state["access_token"]

    return {
        "provider": "google-gemini-cli",
        "base_url": DEFAULT_GEMINI_CLOUDCODE_BASE_URL,
        "api_key": access_token,
        "source": "google-oauth",
        "expires_at_ms": state.get("expiry_date"),
        "auth_store": str(get_auth_store_path()),
        "project_id": state.get("project_id", ""),
        "managed_project_id": state.get("managed_project_id", ""),
    }


def save_google_project_ids(project_id: str = "", managed_project_id: str = "") -> None:
    state = get_provider_auth_state("google-gemini-cli")
    if not state:
        return
    if project_id:
        state["project_id"] = project_id
    if managed_project_id:
        state["managed_project_id"] = managed_project_id
    save_provider_auth_state("google-gemini-cli", state)


def _is_expiring_ms(expiry_date: Any) -> bool:
    try:
        expiry_ms = int(expiry_date)
    except (TypeError, ValueError):
        return True
    return (time.time() + REFRESH_SKEW_SECONDS) * 1000 >= expiry_ms


def _refresh_google_state(state: dict[str, Any]) -> dict[str, Any]:
    refresh_token = state.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise AuthError(
            "Google Gemini CLI OAuth login is expired and has no refresh token.",
            provider="google-gemini-cli",
            code="missing_refresh_token",
            relogin_required=True,
        )

    response = requests.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": os.getenv("TRADINGAGENTS_GEMINI_CLIENT_ID") or _DEFAULT_CLIENT_ID,
            "client_secret": os.getenv("TRADINGAGENTS_GEMINI_CLIENT_SECRET") or _DEFAULT_CLIENT_SECRET,
        },
        headers={"Accept": "application/json"},
        timeout=20,
    )
    if response.status_code != 200:
        raise AuthError(
            "Google Gemini CLI OAuth token refresh failed. Re-run Gemini CLI login.",
            provider="google-gemini-cli",
            code="refresh_failed",
            relogin_required=response.status_code in {400, 401, 403},
        )
    payload = response.json()
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AuthError(
            "Google Gemini CLI OAuth refresh did not return an access token.",
            provider="google-gemini-cli",
            code="refresh_missing_access_token",
            relogin_required=True,
        )

    new_state = dict(state)
    new_state["access_token"] = access_token
    if isinstance(payload.get("refresh_token"), str) and payload["refresh_token"].strip():
        new_state["refresh_token"] = payload["refresh_token"]
    expires_in = int(payload.get("expires_in") or 3600)
    new_state["expiry_date"] = int((time.time() + max(60, expires_in)) * 1000)
    save_provider_auth_state("google-gemini-cli", new_state)
    return new_state
