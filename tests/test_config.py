"""Literal .env parsing, precedence, and direct DeepSeek configuration."""
import json
import os
import sys

import pytest
import requests

from coding_agent.config import load_env_file


def test_literal_parsing_and_environment_precedence(tmp_path, monkeypatch):
    for key in ("TEST_ENV_KEY", "TEST_ENV_QUOTED", "TEST_ENV_EMPTY", "TEST_ENV_LITERAL"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.setenv("TEST_ENV_KEY", "shell-wins")
    path = tmp_path / ".env"
    path.write_text('''# comment
TEST_ENV_KEY=file-value
export TEST_ENV_QUOTED="value with spaces # literal"
TEST_ENV_EMPTY=
TEST_ENV_LITERAL='$(echo never-executed)'
''')
    load_env_file(path)
    assert os.environ["TEST_ENV_KEY"] == "shell-wins"
    assert os.environ["TEST_ENV_QUOTED"] == "value with spaces # literal"
    assert os.environ["TEST_ENV_EMPTY"] == ""
    assert os.environ["TEST_ENV_LITERAL"] == "$(echo never-executed)"


@pytest.mark.parametrize("line", ["INVALID LINE", "BAD-KEY=secret", 'KEY="secret', "KEY=two words"])
def test_invalid_file_does_not_expose_values_or_apply_partial_changes(tmp_path, monkeypatch, line):
    monkeypatch.delenv("TEST_ENV_PARTIAL", raising=False)
    path = tmp_path / ".env"
    path.write_text("TEST_ENV_PARTIAL=secret\n" + line)
    with pytest.raises(ValueError) as exc:
        load_env_file(path)
    assert "secret" not in str(exc.value)
    assert "TEST_ENV_PARTIAL" not in os.environ


def test_optional_and_required_missing_file(tmp_path):
    path = tmp_path / "missing.env"
    load_env_file(path)
    with pytest.raises(OSError):
        load_env_file(path, required=True)


@pytest.fixture
def clean_model_env(monkeypatch):
    names = {name for name in os.environ if name.startswith("MODEL_")}
    names.update({"DEEPSEEK_API_KEY", "MODEL_PROVIDER", "MODEL_NAME", "MODEL_BASE_URL",
                  "MODEL_API_KEY_ENV", "MODEL_TOKEN_LIMIT_FIELD", "MODEL_MAX_OUTPUT_TOKENS", "MODEL_TIMEOUT"})
    for name in names:
        # Register restoration even when a key was absent before the test.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.mark.parametrize("entry", ["agent", "benchmark"])
def test_deepseek_env_to_http_and_no_key_in_outputs(tmp_path, monkeypatch, capsys, clean_model_env, entry):
    from coding_agent import cli
    from benchmarks import evaluate
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('''DEEPSEEK_API_KEY=test-secret-key
MODEL_PROVIDER=openai-compatible
MODEL_NAME=deepseek-flash
MODEL_BASE_URL=https://api.deepseek.com
MODEL_API_KEY_ENV=DEEPSEEK_API_KEY
MODEL_TOKEN_LIMIT_FIELD=max_tokens
MODEL_MAX_OUTPUT_TOKENS=4096
''')
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": '{"action":"finish","args":{"summary":"Done"}}'}}]}

    monkeypatch.setattr(requests, "Session", Session)
    log = tmp_path / "events.jsonl"
    args = [entry, "--log-file", str(log)]
    if entry == "agent":
        args += ["--workspace", str(tmp_path), "Finish"]
        main = cli.main
    else:
        args += ["--task", "add_bug", "--runs", "1", "--output", str(tmp_path / "report.json")]
        main = evaluate.main
    monkeypatch.setattr(sys, "argv", args)
    main()
    url, request = calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-secret-key"
    assert request["json"]["model"] == "deepseek-flash"
    assert request["json"]["max_tokens"] == 4096
    assert "max_completion_tokens" not in request["json"]
    assert "test-secret-key" not in capsys.readouterr().out
    assert "test-secret-key" not in log.read_text()
    if entry == "benchmark":
        assert "test-secret-key" not in (tmp_path / "report.json").read_text()


def test_explicit_env_file_and_cli_override(tmp_path, monkeypatch, capsys, clean_model_env):
    from coding_agent import cli
    from coding_agent.models import ModelResponse
    path = tmp_path / "custom.env"
    path.write_text("MODEL_NAME=file-model\nMODEL_PROVIDER=ollama\n")
    settings = []

    class Model:
        def __init__(self, name, **options):
            settings.append(name)

        def respond(self, messages):
            return ModelResponse('{"action":"finish","args":{"summary":"Done"}}')

    monkeypatch.setattr(cli, "create_model", Model)
    monkeypatch.setattr(sys, "argv", ["agent", "--env-file", str(path), "--model", "cli-model",
                                      "--workspace", str(tmp_path), "Finish"])
    cli.main()
    assert settings == ["cli-model"]
    assert json.loads(capsys.readouterr().out)["termination_reason"] == "finished"
