"""Thinking flags propagate from both entry points to model configuration."""
import json
import sys

import pytest


@pytest.mark.parametrize("flags,expected", [([], None), (["--think"], True), (["--no-think"], False)])
def test_agent_thinking_flag(tmp_path, monkeypatch, capsys, flags, expected):
    from coding_agent import cli
    settings = []

    class Model:
        def __init__(self, name, timeout, think):
            settings.append(think)

        def respond(self, messages):
            return '{"action":"finish","args":{"summary":"Done"}}'

    monkeypatch.setattr(cli, "OllamaModel", Model)
    monkeypatch.setattr(sys, "argv", ["agent", "--workspace", str(tmp_path), *flags, "Inspect"])
    cli.main()
    assert settings == [expected]
    assert json.loads(capsys.readouterr().out)["termination_reason"] == "finished"


@pytest.mark.parametrize("flags,expected", [([], None), (["--think"], True), (["--no-think"], False)])
def test_benchmark_thinking_flag_and_report(tmp_path, monkeypatch, capsys, flags, expected):
    from benchmarks import evaluate as module
    settings = []

    class Model:
        def __init__(self, name, timeout, think):
            settings.append(think)

        def respond(self, messages):
            return '{"action":"finish","args":{"summary":"Done"}}'

    output = tmp_path / "report.json"
    monkeypatch.setattr(module, "OllamaModel", Model)
    monkeypatch.setattr(sys, "argv", ["evaluate", "--task", "add_bug", "--runs", "1",
                                      "--output", str(output), *flags])
    module.main()
    assert settings == [expected]
    assert json.loads(output.read_text())["think"] is expected
    assert json.loads(capsys.readouterr().out)["think"] is expected
