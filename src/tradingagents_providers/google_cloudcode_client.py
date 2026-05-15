"""LangChain chat model for Google Gemini CLI's Cloud Code Assist backend."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Optional

import requests
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.utils import convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

from tradingagents.llm_clients.base_client import BaseLLMClient
from tradingagents_providers.google_oauth import save_google_project_ids

CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
MARKER_BASE_URL = "cloudcode-pa://google"


class GoogleCloudCodeChatModel(BaseChatModel):
    """Small ChatModel adapter for Code Assist's Gemini-compatible API."""

    model_name: str = Field(alias="model")
    access_token: str
    project_id: str = ""
    managed_project_id: str = ""
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: Any = None
    timeout: float = 120.0

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "google-gemini-cli"

    def bind_tools(
        self,
        tools: list[dict[str, Any] | type | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        converted = [convert_to_openai_tool(tool) for tool in tools]
        return self.model_copy(
            update={
                "tools": converted,
                "tool_choice": tool_choice or kwargs.get("tool_choice"),
            }
        )

    def with_structured_output(self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any):
        del schema, include_raw, kwargs
        raise NotImplementedError("google-gemini-cli does not expose structured output yet")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        openai_messages = convert_to_openai_messages(messages)
        inner = _build_gemini_request(
            messages=openai_messages,
            tools=kwargs.get("tools", self.tools),
            tool_choice=kwargs.get("tool_choice", self.tool_choice),
            stop=stop,
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens") or kwargs.get("max_output_tokens"),
            top_p=kwargs.get("top_p"),
            thinking_config=_thinking_config_from_kwargs(kwargs),
        )
        project_id = self._ensure_project_id()
        wrapped = {
            "project": project_id,
            "model": self.model_name,
            "user_prompt_id": str(uuid.uuid4()),
            "request": inner,
        }
        response = requests.post(
            f"{CODE_ASSIST_ENDPOINT}/v1internal:generateContent",
            json=wrapped,
            headers=_headers(self.access_token, self.model_name),
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(_format_code_assist_error(response))
        payload = response.json()
        message, response_metadata, usage_metadata = _translate_response(payload)
        generation = ChatGeneration(message=message, generation_info=response_metadata)
        return ChatResult(generations=[generation], llm_output={"token_usage": usage_metadata})

    def _ensure_project_id(self) -> str:
        if self.project_id:
            return self.project_id
        env_project = (
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GOOGLE_PROJECT_ID")
            or os.getenv("CLOUDSDK_CORE_PROJECT")
            or ""
        ).strip()
        if env_project:
            return env_project

        body = {
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        }
        response = requests.post(
            f"{CODE_ASSIST_ENDPOINT}/v1internal:loadCodeAssist",
            json=body,
            headers=_headers(self.access_token, self.model_name),
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(_format_code_assist_error(response))
        payload = response.json()
        project = str(payload.get("cloudaicompanionProject") or "").strip()
        if not project:
            current_tier = payload.get("currentTier") or {}
            if isinstance(current_tier, dict):
                project = str(current_tier.get("project") or "").strip()
        if not project:
            raise RuntimeError("Code Assist did not return a project id for this account.")

        self.project_id = project
        save_google_project_ids(project_id=project)
        return project


class GoogleCloudCodeClient(BaseLLMClient):
    """Client wrapper matching TradingAgents' provider-client interface."""

    provider = "google-gemini-cli"

    def get_llm(self) -> Any:
        return GoogleCloudCodeChatModel(
            model=self.model,
            access_token=str(self.kwargs.get("api_key") or ""),
            project_id=str(self.kwargs.get("project_id") or ""),
            managed_project_id=str(self.kwargs.get("managed_project_id") or ""),
            timeout=float(self.kwargs.get("timeout") or 120.0),
        )

    def validate_model(self) -> bool:
        return True


def _headers(access_token: str, model: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": f"google-api-nodejs-client/9.15.1 (gzip) model/{model}",
        "X-Goog-Api-Client": "gl-node/24.0.0",
        "x-activity-request-id": str(uuid.uuid4()),
    }


def _coerce_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
        return "\n".join(pieces)
    return str(content)


def _build_gemini_request(
    *,
    messages: list[dict[str, Any]],
    tools: Any = None,
    tool_choice: Any = None,
    stop: list[str] | None = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    thinking_config: Any = None,
) -> dict[str, Any]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role")
        if role == "system":
            text = _coerce_text(message.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role in {"tool", "function"}:
            name = str(message.get("name") or message.get("tool_call_id") or "tool")
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": {"output": _coerce_text(message.get("content"))},
                            }
                        }
                    ],
                }
            )
            continue

        parts: list[dict[str, Any]] = []
        text = _coerce_text(message.get("content"))
        if text:
            parts.append({"text": text})
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            fn = tool_call.get("function") or {}
            args = fn.get("arguments") or "{}"
            try:
                parsed_args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                parsed_args = {"_raw": args}
            parts.append(
                {
                    "functionCall": {
                        "name": str(fn.get("name") or ""),
                        "args": parsed_args if isinstance(parsed_args, dict) else {"value": parsed_args},
                    },
                    "thoughtSignature": "skip_thought_signature_validator",
                }
            )
        if parts:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})

    request: dict[str, Any] = {"contents": contents}
    if system_parts:
        request["systemInstruction"] = {
            "role": "system",
            "parts": [{"text": "\n".join(system_parts)}],
        }

    gemini_tools = _convert_tools(tools)
    if gemini_tools:
        request["tools"] = gemini_tools
    tool_config = _convert_tool_choice(tool_choice)
    if tool_config:
        request["toolConfig"] = tool_config

    generation_config: dict[str, Any] = {}
    if isinstance(temperature, (int, float)):
        generation_config["temperature"] = float(temperature)
    if isinstance(max_tokens, int) and max_tokens > 0:
        generation_config["maxOutputTokens"] = max_tokens
    if isinstance(top_p, (int, float)):
        generation_config["topP"] = float(top_p)
    if stop:
        generation_config["stopSequences"] = [str(item) for item in stop if item]
    if isinstance(thinking_config, dict) and thinking_config:
        generation_config["thinkingConfig"] = thinking_config
    if generation_config:
        request["generationConfig"] = generation_config
    return request


def _convert_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list) or not tools:
        return []
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        declaration = {
            "name": str(fn["name"]),
            "description": str(fn.get("description") or ""),
            "parameters": _sanitize_schema(fn.get("parameters") or {}),
        }
        declarations.append(declaration)
    return [{"functionDeclarations": declarations}] if declarations else []


def _sanitize_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    allowed = {
        "type",
        "properties",
        "required",
        "description",
        "enum",
        "items",
        "anyOf",
        "format",
        "nullable",
    }
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _sanitize_schema(prop) for name, prop in value.items()}
        elif key == "items":
            out[key] = _sanitize_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            out[key] = [_sanitize_schema(item) for item in value]
        else:
            out[key] = value
    if not out:
        out = {"type": "object", "properties": {}}
    return out


def _convert_tool_choice(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice == "auto":
        return {"functionCallingConfig": {"mode": "AUTO"}}
    if tool_choice == "required":
        return {"functionCallingConfig": {"mode": "ANY"}}
    if tool_choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    return None


def _translate_response(payload: dict[str, Any]) -> tuple[AIMessage, dict[str, Any], dict[str, int]]:
    inner = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    candidates = inner.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    parts = ((candidate.get("content") or {}).get("parts") or []) if isinstance(candidate, dict) else []
    texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str) and not part.get("thought"):
            texts.append(part["text"])
        function_call = part.get("functionCall")
        if isinstance(function_call, dict) and function_call.get("name"):
            args = function_call.get("args") or {}
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": str(function_call["name"]),
                    "args": args if isinstance(args, dict) else {"value": args},
                }
            )
    usage_raw = inner.get("usageMetadata") or {}
    usage = {
        "input_tokens": int(usage_raw.get("promptTokenCount") or 0),
        "output_tokens": int(usage_raw.get("candidatesTokenCount") or 0),
        "total_tokens": int(usage_raw.get("totalTokenCount") or 0),
    }
    metadata = {
        "finish_reason": _map_finish_reason(str(candidate.get("finishReason") or "")),
        "model": payload.get("model"),
        "created": int(time.time()),
    }
    return AIMessage(content="".join(texts), tool_calls=tool_calls, response_metadata=metadata), metadata, usage


def _map_finish_reason(reason: str) -> str:
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
    }.get(reason.upper(), "stop")


def _thinking_config_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    raw = kwargs.get("thinking_config") or kwargs.get("thinkingConfig")
    return raw if isinstance(raw, dict) else None


def _format_code_assist_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return f"Code Assist HTTP {response.status_code}: {error['message']}"
    except ValueError:
        pass
    return f"Code Assist HTTP {response.status_code}: {response.text[:500]}"
