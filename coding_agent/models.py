"""Model adapters: agent-facing response contract and provider configuration."""
import argparse
import json
from .actions import AgentAction, strict_json_loads
from dataclasses import dataclass, field
import math
import os
from typing import Protocol
from urllib.parse import urlsplit

import requests


@dataclass(frozen=True)
class ModelResponse:
    content: str
    metadata: dict = field(default_factory=dict)
    action: AgentAction | None = None
    action_error: str | None = None
    assistant_message: dict | None = None
    tool_call_ids: tuple[str, ...] = ()


class Model(Protocol):
    def respond(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse: ...


class ModelError(requests.RequestException):
    """A provider returned an unusable response; classified as a model failure."""


def validate_settings(timeout, max_output_tokens):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("model timeout must be finite and positive")
    if max_output_tokens is not None and (type(max_output_tokens) is not int or max_output_tokens <= 0):
        raise ValueError("max_output_tokens must be a positive integer")


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Model URL must be HTTP(S), without credentials, query, or fragment")
    return url


def post_json(url, payload, timeout, headers=None):
    with requests.Session() as session:
        if urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}:
            session.trust_env = False
        try:
            kwargs = {"json": payload, "timeout": (5, timeout)}
            if headers:
                kwargs["headers"] = headers
            response = session.post(url, **kwargs)
        except requests.ReadTimeout as exc:
            raise requests.ReadTimeout(
                f"Model did not return a response within {timeout:g}s; "
                "increase --model-timeout or check server load and generation speed") from exc
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelError("Model endpoint returned invalid JSON") from exc
        if not isinstance(data, dict) or "error" in data:
            # Do not echo provider payloads, which can contain credentials or prompt text.
            raise ModelError("Model endpoint returned an error or invalid response object")
        return data


class OllamaModel:
    provider = "ollama"

    def __init__(self, model="qwen3:8b", url="http://127.0.0.1:11434/api/chat",
                 timeout=180, think=None, max_output_tokens=None, json_mode=True):
        validate_settings(timeout, max_output_tokens)
        if think is not None and not isinstance(think, bool):
            raise ValueError("think must be a boolean or None")
        self.model, self.url, self.timeout = model, validate_url(url), timeout
        self.think, self.max_output_tokens, self.json_mode = think, max_output_tokens, json_mode

    def respond(self, messages, tools=None):
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"temperature": 0}}
        if self.json_mode:
            payload["format"] = "json"
        if self.think is not None:
            payload["think"] = self.think
        if self.max_output_tokens is not None:
            payload["options"]["num_predict"] = self.max_output_tokens
        data = post_json(self.url, payload, self.timeout)
        try:
            content = data["message"]["content"]
            if not isinstance(content, str):
                raise TypeError()
        except (KeyError, TypeError) as exc:
            raise ModelError("Ollama response must contain string message.content") from exc
        metrics = ("done_reason", "total_duration", "load_duration", "prompt_eval_count",
                   "prompt_eval_duration", "eval_count", "eval_duration")
        return ModelResponse(content, {key: data[key] for key in metrics if key in data})


class OpenAICompatibleModel:
    """Non-streaming Chat Completions adapter; not a universal provider API."""
    provider = "openai-compatible"

    def __init__(self, model, base_url, timeout=180, api_key=None, max_output_tokens=None,
                 json_mode=True, token_limit_field="max_completion_tokens", native_tools=False):
        validate_settings(timeout, max_output_tokens)
        if token_limit_field not in {"max_completion_tokens", "max_tokens"}:
            raise ValueError("Unsupported token limit field")
        if not model:
            raise ValueError("An explicit model is required")
        self.model, self.timeout = model, timeout
        self.url = validate_url(base_url).rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self.max_output_tokens, self.json_mode = max_output_tokens, json_mode and not native_tools
        self.token_limit_field = token_limit_field
        self.native_tools = native_tools

    def respond(self, messages, tools=None):
        payload = {"model": self.model, "messages": messages, "stream": False}
        # Temperature is deliberately omitted: some reasoning models reject it.
        if self.native_tools:
            if not tools:
                raise ModelError("Native tool mode requires tool definitions")
            payload["tools"] = [{"type": "function", "function": {
                "name": spec["name"], "description": spec["description"],
                "parameters": spec["input_schema"]}} for spec in tools]
            # DeepSeek thinking mode rejects forced/required tool choices.
            # Prompt for one call; enforce cardinality locally regardless.
            payload["tool_choice"] = "auto"
        elif self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.max_output_tokens is not None:
            payload[self.token_limit_field] = self.max_output_tokens
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        data = post_json(self.url, payload, self.timeout, headers)
        try:
            choice = data["choices"][0]
            reason = choice["finish_reason"]
            message = choice["message"]
            if not isinstance(message, dict) or message.get("refusal") or reason == "content_filter":
                raise TypeError()
        except (KeyError, TypeError, IndexError) as exc:
            raise ModelError("Expected a usable Chat Completions response") from exc
        metadata = {"done_reason": reason, "finish_reason": reason}
        usage = data.get("usage")
        if isinstance(usage, dict):
            metadata["usage"] = {key: usage[key] for key in
                ("prompt_tokens", "completion_tokens", "total_tokens") if key in usage}
        if self.native_tools:
            return self._native_response(message, reason, metadata)
        content = message.get("content")
        if reason == "length" and content is None:
            content = ""
        if not isinstance(content, str) or message.get("tool_calls") or reason not in {"stop", "length"}:
            raise ModelError("Expected Chat Completions text; enable --native-tools for tool calls")
        return ModelResponse(content, metadata)

    def _native_response(self, message, reason, metadata):
        if reason not in {"stop", "tool_calls", "length"}:
            raise ModelError("Unsupported native tool completion reason")
        # Preserve provider reasoning in memory for multi-turn protocol continuity,
        # but never place it in response content, metadata, or diagnostic previews.
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelError("Native assistant content must be text or null")
        assistant = {"role": "assistant", "content": content or ""}
        if "reasoning_content" in message:
            assistant["reasoning_content"] = message["reasoning_content"]
        calls = message.get("tool_calls") or []
        if reason == "length":
            return ModelResponse(json.dumps(calls), metadata, assistant_message=assistant)
        if not isinstance(calls, list):
            raise ModelError("Malformed native tool calls")
        if not calls:
            return ModelResponse(message.get("content") or "", metadata,
                action_error="A native function call is required, not an ordinary text answer",
                assistant_message=assistant)
        ids = []
        for call in calls:
            if (not isinstance(call, dict) or call.get("type") != "function"
                    or not isinstance(call.get("id"), str) or not call["id"]
                    or call["id"] in ids or not isinstance(call.get("function"), dict)
                    or not isinstance(call["function"].get("name"), str)
                    or not isinstance(call["function"].get("arguments"), str)):
                raise ModelError("Malformed native call envelope or missing/duplicate call ID")
            ids.append(call["id"])
        assistant["tool_calls"] = calls
        preview = json.dumps(calls)
        if len(calls) != 1:
            return ModelResponse(preview, metadata,
                action_error="Exactly one function call per response is supported; no calls were executed",
                assistant_message=assistant, tool_call_ids=tuple(ids))
        function = calls[0]["function"]
        try:
            args = strict_json_loads(function["arguments"])
            if not isinstance(args, dict):
                raise ValueError("Function arguments must be a JSON object")
        except (ValueError, TypeError) as exc:
            return ModelResponse(preview, metadata, action_error=str(exc),
                                 assistant_message=assistant, tool_call_ids=tuple(ids))
        return ModelResponse(preview, metadata, AgentAction(function["name"], args, ids[0]),
                             assistant_message=assistant, tool_call_ids=tuple(ids))


def resolve_model_name(provider, model):
    if provider not in {"ollama", "openai-compatible"}:
        raise ValueError(f"Unknown model provider: {provider}")
    if model:
        return model
    if provider == "ollama":
        return "qwen3:8b"
    raise ValueError("--model is required for openai-compatible")


def create_model(model=None, *, provider="ollama", base_url=None, api_key_env=None,
                 timeout=180, think=None, max_output_tokens=None, json_mode=True,
                 token_limit_field=None, native_tools=False):
    model = resolve_model_name(provider, model)
    if provider == "ollama":
        if native_tools:
            raise ValueError("--native-tools is currently supported only by openai-compatible")
        if api_key_env is not None or token_limit_field is not None:
            raise ValueError("--api-key-env and --token-limit-field are only for openai-compatible")
        url = validate_url(base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
        return OllamaModel(model, url, timeout, think, max_output_tokens, json_mode)
    if think is not None:
        raise ValueError("--think/--no-think is Ollama-specific; omit it for openai-compatible")
    if not base_url:
        raise ValueError("--base-url is required for openai-compatible (including its API prefix, e.g. /v1)")
    local = urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
    # Never forward a hosted-provider key to a local server implicitly.
    api_key = os.environ.get(api_key_env) if api_key_env else (None if local else os.environ.get("OPENAI_API_KEY"))
    if api_key_env is not None and not api_key:
        raise ValueError("The environment variable selected by --api-key-env is empty or unset")
    return OpenAICompatibleModel(model, base_url, timeout, api_key, max_output_tokens,
                                 json_mode, token_limit_field or "max_completion_tokens", native_tools)


def add_model_arguments(parser):
    parser.add_argument("--provider", choices=["ollama", "openai-compatible"], default=os.environ.get("MODEL_PROVIDER", "ollama"))
    parser.add_argument("--model", default=os.environ.get("MODEL_NAME"), help="Model ID (default: qwen3:8b for Ollama; required otherwise)")
    parser.add_argument("--base-url", default=os.environ.get("MODEL_BASE_URL"), help="Server base URL; include /v1 for Chat Completions servers")
    parser.add_argument("--api-key-env", default=os.environ.get("MODEL_API_KEY_ENV"), help="API-key environment variable (compatible default: OPENAI_API_KEY)")
    parser.add_argument("--model-timeout", type=float, default=os.environ.get("MODEL_TIMEOUT", "180"), help="Model response timeout in seconds")
    parser.add_argument("--think", action=argparse.BooleanOptionalAction, default=None,
                        help="Ollama thinking control; omitted preserves model default")
    parser.add_argument("--max-output-tokens", type=int, default=os.environ.get("MODEL_MAX_OUTPUT_TOKENS"), help="Optional positive provider generation budget")
    parser.add_argument("--json-mode", action=argparse.BooleanOptionalAction, default=True,
                        help="Request provider JSON mode; disable if unsupported")
    parser.add_argument("--native-tools", action=argparse.BooleanOptionalAction, default=False,
                        help="Use provider function calls instead of JSON action text (compatible only)")
    parser.add_argument("--token-limit-field", choices=["max_completion_tokens", "max_tokens"],
                        default=os.environ.get("MODEL_TOKEN_LIMIT_FIELD"),
                        help="Compatible API token-budget field (default: max_completion_tokens)")


def model_options(args):
    return {"provider": args.provider, "base_url": args.base_url, "api_key_env": args.api_key_env,
            "timeout": args.model_timeout, "think": args.think, "max_output_tokens": args.max_output_tokens,
            "json_mode": args.json_mode, "token_limit_field": args.token_limit_field,
            "native_tools": args.native_tools}
