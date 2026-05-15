"""OpenAI Codex Responses client for TradingAgents."""

from __future__ import annotations

from typing import Any, Optional

from tradingagents.llm_clients.base_client import BaseLLMClient
from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI

_PASSTHROUGH_KWARGS = (
    "timeout",
    "max_retries",
    "reasoning_effort",
    "api_key",
    "callbacks",
    "http_client",
    "http_async_client",
)

_DEFAULT_CODEX_INSTRUCTIONS = "You are a helpful assistant."


class CodexResponsesChatOpenAI(NormalizedChatOpenAI):
    """ChatOpenAI Responses client with Codex backend defaults."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        _lift_system_input_to_instructions(payload)
        payload.setdefault("store", False)
        return payload


class CodexResponsesClient(BaseLLMClient):
    """Client for OAuth-backed OpenAI Codex Responses API."""

    provider = "openai-codex"

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "base_url": self.base_url,
            "use_responses_api": True,
            "streaming": True,
        }

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        return CodexResponsesChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        return True


def _lift_system_input_to_instructions(payload: dict[str, Any]) -> None:
    """Move system/developer Responses input items into Codex instructions."""
    input_items = payload.get("input")
    if not isinstance(input_items, list):
        payload.setdefault("instructions", _DEFAULT_CODEX_INSTRUCTIONS)
        return

    instruction_parts: list[str] = []
    filtered_items: list[Any] = []
    for item in input_items:
        if isinstance(item, dict) and item.get("role") in {"system", "developer"}:
            text = _stringify_responses_content(item.get("content"))
            if text:
                instruction_parts.append(text)
            continue
        filtered_items.append(item)

    if instruction_parts:
        existing = payload.get("instructions")
        if isinstance(existing, str) and existing.strip() and existing != _DEFAULT_CODEX_INSTRUCTIONS:
            instruction_parts.insert(0, existing.strip())
        payload["instructions"] = "\n\n".join(instruction_parts)
        payload["input"] = filtered_items
    else:
        payload.setdefault("instructions", _DEFAULT_CODEX_INSTRUCTIONS)


def _stringify_responses_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    pieces.append(text)
        return "\n".join(piece for piece in pieces if piece)
    return str(content)
