"""Provider wire formats, configuration boundaries, and shared agent semantics."""
import json
import sys

import pytest
import requests

from coding_agent.core import CodingAgent
from coding_agent.models import (
    ModelError, OllamaModel, OpenAICompatibleModel, create_model,
)
from coding_agent.tools import ToolRegistry


def mock_http(monkeypatch, data):
    calls = []

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def post(self, url, **kwargs):
            calls.append({"url": url, "trust_env": self.trust_env, **kwargs})
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return data

    monkeypatch.setattr(requests, "Session", Session)
    return calls


def completion(content='{"action":"finish","args":{"summary":"Done"}}', reason="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


def test_compatible_request_normalization_and_agent_interchangeability(monkeypatch):
    calls = mock_http(monkeypatch, completion())
    model = OpenAICompatibleModel("some-model", "https://model.example/v1/", api_key="secret",
                                  timeout=30, max_output_tokens=512)
    result = CodingAgent(model, ToolRegistry([])).run("Finish")
    assert result["termination_reason"] == "finished"
    request = calls[0]
    assert request["url"] == "https://model.example/v1/chat/completions"
    assert request["headers"] == {"Authorization": "Bearer secret"}
    assert request["timeout"] == (5, 30)
    assert request["json"]["model"] == "some-model"
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert request["json"]["max_completion_tokens"] == 512
    assert "think" not in request["json"]
    assert "temperature" not in request["json"]
    response = model.respond([])
    assert response.metadata == {"done_reason": "stop", "finish_reason": "stop",
                                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


def test_local_compatible_no_json_mode_legacy_budget(monkeypatch):
    calls = mock_http(monkeypatch, completion())
    model = OpenAICompatibleModel("local-model", "http://localhost:1234/v1", json_mode=False,
                                  max_output_tokens=128, token_limit_field="max_tokens")
    model.respond([])
    request = calls[0]
    assert not request["trust_env"]
    assert "headers" not in request
    assert request["json"]["max_tokens"] == 128
    assert "max_completion_tokens" not in request["json"]
    assert "response_format" not in request["json"]


def test_compatible_truncation_never_executes_valid_looking_action(monkeypatch):
    mock_http(monkeypatch, completion(reason="length"))
    result = CodingAgent(OpenAICompatibleModel("model", "http://localhost/v1"),
                         ToolRegistry([]), max_steps=1).run("Finish")
    assert result["termination_reason"] == "step_limit"
    assert "token limit" in result["trace"][0]["result"]["error"]["message"]


def test_compatible_length_with_null_content(monkeypatch):
    mock_http(monkeypatch, completion(content=None, reason="length"))
    response = OpenAICompatibleModel("model", "http://localhost/v1").respond([])
    assert response.content == ""
    assert response.metadata["done_reason"] == "length"


@pytest.mark.parametrize("data", [
    {"choices": []}, {}, {"error": {"message": "secret error"}},
    completion(content=None), completion(reason="content_filter"),
    {"choices": [{"message": {"content": "{}", "tool_calls": [{"function": {}}]}, "finish_reason": "stop"}]},
    {"choices": [{"message": {"content": "{}", "refusal": "No"}, "finish_reason": "stop"}]},
])
def test_unusable_responses_are_model_errors(monkeypatch, data):
    mock_http(monkeypatch, data)
    with pytest.raises(ModelError) as exc:
        OpenAICompatibleModel("model", "http://localhost/v1").respond([])
    assert isinstance(exc.value, requests.RequestException)
    assert "secret error" not in str(exc.value)


def test_ollama_budget_and_json_toggle(monkeypatch):
    calls = mock_http(monkeypatch, {"message": {"content": "{}"}})
    model = create_model("gemma3:4b", base_url="http://localhost:11434/",
                         max_output_tokens=256, json_mode=False)
    assert isinstance(model, OllamaModel)
    model.respond([])
    assert calls[0]["url"] == "http://localhost:11434/api/chat"
    assert calls[0]["json"]["options"]["num_predict"] == 256
    assert "format" not in calls[0]["json"]
    assert "think" not in calls[0]["json"]


def test_factory_authentication_and_defaults(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "hosted-secret")
    monkeypatch.setenv("CUSTOM_MODEL_KEY", "custom-secret")
    calls = mock_http(monkeypatch, completion())
    assert create_model().model == "qwen3:8b"
    create_model("remote", provider="openai-compatible", base_url="https://model.example/v1").respond([])
    assert calls[-1]["headers"]["Authorization"] == "Bearer hosted-secret"
    create_model("local", provider="openai-compatible", base_url="http://localhost:1234/v1").respond([])
    assert "headers" not in calls[-1]
    create_model("local", provider="openai-compatible", base_url="http://localhost:1234/v1",
                 api_key_env="CUSTOM_MODEL_KEY").respond([])
    assert calls[-1]["headers"]["Authorization"] == "Bearer custom-secret"


@pytest.mark.parametrize("kwargs", [
    {"provider": "unknown"},
    {"provider": "openai-compatible"},
    {"provider": "openai-compatible", "model": "m"},
    {"provider": "openai-compatible", "model": "m", "base_url": "http://localhost/v1", "think": False},
    {"provider": "openai-compatible", "model": "m", "base_url": "http://localhost/v1", "api_key_env": "MISSING_MODEL_TEST_KEY"},
    {"api_key_env": "KEY"}, {"token_limit_field": "max_tokens"},
    {"max_output_tokens": 0}, {"max_output_tokens": -1}, {"max_output_tokens": True},
    {"base_url": "http://user:secret@host"}, {"base_url": "http://host?api_key=secret"},
])
def test_unsupported_or_invalid_configuration_fails_before_network(kwargs, monkeypatch):
    monkeypatch.delenv("MISSING_MODEL_TEST_KEY", raising=False)
    with pytest.raises(ValueError):
        create_model(**kwargs)


def test_cli_compatible_provider_options(tmp_path, monkeypatch, capsys):
    from coding_agent import cli
    calls = mock_http(monkeypatch, completion())
    monkeypatch.setattr(sys, "argv", ["agent", "--workspace", str(tmp_path), "--provider", "openai-compatible",
        "--model", "local-model", "--base-url", "http://localhost:1234/v1", "--no-json-mode",
        "--max-output-tokens", "100", "--token-limit-field", "max_tokens", "Finish"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["termination_reason"] == "finished"
    assert calls[0]["json"]["max_tokens"] == 100


def test_evaluator_provider_options_and_report(tmp_path, monkeypatch, capsys):
    from benchmarks import evaluate as module
    calls = mock_http(monkeypatch, completion())
    path = tmp_path / "report.json"
    log = tmp_path / "events.jsonl"
    monkeypatch.setattr(sys, "argv", ["evaluate", "--task", "add_bug", "--runs", "1", "--provider", "openai-compatible",
        "--model", "local-model", "--base-url", "http://localhost:1234/v1", "--max-output-tokens", "100",
        "--output", str(path), "--log-file", str(log)])
    module.main()
    report = json.loads(path.read_text())
    assert report["provider"] == "openai-compatible"
    assert report["model"] == "local-model"
    assert report["max_output_tokens"] == 100
    assert calls[0]["json"]["max_completion_tokens"] == 100
    start = json.loads(log.read_text().splitlines()[0])
    assert start["provider"] == "openai-compatible"
    assert start["max_output_tokens"] == 100
    assert "secret" not in log.read_text()
    assert json.loads(capsys.readouterr().out)["provider"] == "openai-compatible"
