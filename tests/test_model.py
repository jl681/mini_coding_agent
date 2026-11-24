"""Model transport settings and actionable timeout failures."""
import pytest
import requests

from coding_agent.core import OllamaModel


@pytest.mark.parametrize("url,bypass", [
    ("http://127.0.0.1:11434/api/chat", True),
    ("http://localhost:11434/api/chat", True),
    ("http://[::1]:11434/api/chat", True),
    ("https://model.example/api/chat", False),
])
def test_model_transport_configuration(monkeypatch, url, bypass):
    captured = {}

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            captured["closed"] = True

        def post(self, endpoint, **kwargs):
            captured.update(endpoint=endpoint, bypass=not self.trust_env, **kwargs)
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "response"}}

    monkeypatch.setattr(requests, "Session", Session)
    assert OllamaModel(url=url, timeout=600).respond([]).content == "response"
    assert captured["bypass"] is bypass
    assert captured["timeout"] == (5, 600)
    assert captured["closed"]
    assert captured["json"]["stream"] is False


def test_timeout_diagnostic_and_cleanup(monkeypatch):
    closed = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def post(self, *args, **kwargs):
            raise requests.ReadTimeout("Timed out")

    monkeypatch.setattr(requests, "Session", Session)
    with pytest.raises(requests.ReadTimeout, match="600s.*--model-timeout"):
        OllamaModel(timeout=600).respond([])
    assert closed == [True]


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        OllamaModel(timeout=timeout)


@pytest.mark.parametrize("think", [None, True, False])
def test_thinking_request_setting(monkeypatch, think):
    payloads = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            payloads.append(kwargs["json"])
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "{}"}}

    monkeypatch.setattr(requests, "Session", Session)
    OllamaModel(think=think).respond([])
    if think is None:
        assert "think" not in payloads[0]
    else:
        assert payloads[0]["think"] is think
    assert "think" not in payloads[0]["options"]


def test_ollama_metadata_preserved_without_thinking_text(monkeypatch):
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "{}", "thinking": "private reasoning"},
                    "done_reason": "length", "eval_count": 1024, "eval_duration": 123,
                    "prompt_eval_count": 800, "extra": "ignored"}

    monkeypatch.setattr(requests, "Session", Session)
    response = OllamaModel().respond([])
    assert response.content == "{}"
    assert response.metadata == {"done_reason": "length", "eval_count": 1024,
                                 "eval_duration": 123, "prompt_eval_count": 800}
