"""Profile selection, overrides, configuration boundaries, and provenance."""
import json
import sys

import pytest

from coding_agent.config import load_profile
from coding_agent.models import ModelResponse


def write_profiles(path, settings):
    path.write_text(json.dumps({"profiles": {"test": settings}}))


def local_settings(**extra):
    return {"provider": "ollama", "model": "profile-model", "think": False,
            "max_output_tokens": 256, "max_steps": 7, "mcp_search": False, **extra}


def test_library_loader_and_no_mutation(tmp_path):
    path = tmp_path / "profiles.json"
    write_profiles(path, local_settings())
    settings = load_profile("test", path)
    assert settings["model"] == "profile-model"
    assert settings["model_timeout"] == 180
    settings["model"] = "changed"
    assert load_profile("test", path)["model"] == "profile-model"


@pytest.mark.parametrize("settings", [
    {"provider": "ollama"}, local_settings(api_key="secret"),
    local_settings(provider="unknown"), local_settings(max_steps=0),
    local_settings(max_output_tokens=True), local_settings(mcp_search="false"),
    local_settings(think="false"), local_settings(model_timeout=-1),
    local_settings(provider="openai-compatible"), local_settings(token_limit_field="unknown"),
])
def test_invalid_profiles_rejected_without_echoing_values(tmp_path, settings):
    path = tmp_path / "profiles.json"
    write_profiles(path, settings)
    with pytest.raises(ValueError) as exc:
        load_profile("test", path)
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("entry", ["agent", "benchmark"])
def test_profile_overrides_environment_and_cli_overrides_profile(tmp_path, monkeypatch, capsys, entry):
    from coding_agent import cli
    from benchmarks import evaluate
    monkeypatch.chdir(tmp_path)
    write_profiles(tmp_path / "profiles.json", local_settings())
    # An older direct-API .env must not contaminate a local profile.
    (tmp_path / ".env").write_text("MODEL_PROVIDER=openai-compatible\nMODEL_BASE_URL=https://other.example/v1\n"
                                   "MODEL_API_KEY_ENV=MISSING_KEY\nMODEL_TOKEN_LIMIT_FIELD=max_tokens\n")
    # Restore environment changes made by the loader after the test.
    for key in ("MODEL_PROVIDER", "MODEL_BASE_URL", "MODEL_API_KEY_ENV", "MODEL_TOKEN_LIMIT_FIELD"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    captured = []

    class Model:
        def __init__(self, name, **options):
            captured.append((name, options))

        def respond(self, messages):
            return ModelResponse('{"action":"finish","args":{"summary":"Done"}}')

    log = tmp_path / "events.jsonl"
    args = [entry, "--profile", "test", "--model", "cli-model", "--max-steps", "3", "--log-file", str(log)]
    if entry == "agent":
        monkeypatch.setattr(cli, "create_model", Model)
        args += ["--workspace", str(tmp_path), "Finish"]
        main = cli.main
    else:
        monkeypatch.setattr(evaluate, "create_model", Model)
        args += ["--task", "add_bug", "--runs", "1", "--output", str(tmp_path / "report.json")]
        main = evaluate.main
    monkeypatch.setattr(sys, "argv", args)
    main()
    name, options = captured[0]
    assert name == "cli-model"
    assert options["provider"] == "ollama"
    assert options["base_url"] is None
    assert options["api_key_env"] is None
    assert options["token_limit_field"] is None
    assert options["think"] is False
    start = json.loads(log.read_text().splitlines()[0])
    assert start["profile"] == "test"
    assert len(start["profile_hash"]) == 64
    assert start["max_steps"] == 3
    if entry == "benchmark":
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["profile_hash"] == start["profile_hash"]
        assert report["profile"] == "test"
        assert report["max_steps"] == 3
    assert "MISSING_KEY" not in capsys.readouterr().out


def test_profile_mcp_can_be_explicitly_disabled(tmp_path, monkeypatch, capsys):
    from coding_agent import cli
    path = tmp_path / "settings.json"
    write_profiles(path, local_settings(mcp_search=True))
    monkeypatch.setattr(cli, "create_model", lambda *a, **k: type("Model", (), {
        "respond": lambda self, messages: ModelResponse('{"action":"finish","args":{"summary":"Done"}}')})())
    monkeypatch.setattr(cli, "MCPClient", lambda *a, **k: pytest.fail("MCP must be disabled"))
    monkeypatch.setattr(sys, "argv", ["agent", "--profiles-file", str(path), "--profile", "test",
                                      "--no-mcp-search", "--workspace", str(tmp_path), "Finish"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["termination_reason"] == "finished"


def test_missing_profile_is_clear_cli_error(tmp_path, monkeypatch, capsys):
    from coding_agent import cli
    path = tmp_path / "settings.json"
    write_profiles(path, local_settings())
    monkeypatch.setattr(sys, "argv", ["agent", "--profiles-file", str(path), "--profile", "missing",
                                      "--workspace", str(tmp_path), "Finish"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "Selected profile does not exist" in capsys.readouterr().err


def test_malformed_profile_file_does_not_echo_contents(tmp_path, monkeypatch, capsys):
    from coding_agent import cli
    path = tmp_path / "broken.json"
    path.write_text('{"api_key":"do-not-print-secret"')
    monkeypatch.setattr(sys, "argv", ["agent", "--profiles-file", str(path), "--profile", "test",
                                      "--workspace", str(tmp_path), "Finish"])
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().err
    assert "Cannot read or parse profile file" in output
    assert "do-not-print-secret" not in output
