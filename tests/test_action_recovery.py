"""Ambiguous action responses never execute; recovery feedback is actionable."""
import json

import pytest

from coding_agent.core import CodingAgent, ModelResponse
from coding_agent.events import JsonlRecorder
from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments


@pytest.mark.parametrize("raw", [
    '{"action":"change","args":{}}\n{"action":"change","args":{}}',
    '{"action":"change","args":{}}\nHere is my explanation.',
    '```json\n{"action":"change","args":{}}\n```',
    '{"action":"change","action":"change","args":{}}',
    '{"action":"change","args":{},"explanation":"extra"}',
    '{"action":"change","args":{"value":NaN}}',
])
def test_ambiguous_response_rejected_then_corrected(raw):
    calls = []
    responses = iter([raw, '{"action":"change","args":{}}',
                      '{"action":"finish","args":{"summary":"Done"}}'])

    class Model:
        def respond(self, messages):
            if len(messages) == 4:
                feedback = json.loads(messages[-1]["content"].removeprefix("Tool observation: "))
                assert "exactly ONE JSON object" in feedback["error"]["recovery"]
                assert "No action" in feedback["error"]["recovery"]
                assert calls == []
            return ModelResponse(next(responses))

    registry = ToolRegistry([FunctionTool("change", "Change", string_arguments(),
                                         lambda args: calls.append(args) or {"ok": True})])
    result = CodingAgent(Model(), registry).run("Change")
    assert result["termination_reason"] == "finished"
    assert calls == [{}]
    assert result["trace"][0]["action"] == "invalid_action"


@pytest.mark.parametrize("enabled", [False, True])
def test_rejected_preview_is_opt_in_and_bounded(tmp_path, enabled):
    marker = "sensitive-debug-marker"
    raw = '{"action":"finish","args":{"summary":"Done"}}\n' + marker + "x" * 3000

    class Model:
        def respond(self, messages):
            return ModelResponse(raw)

    path = tmp_path / "events.jsonl"
    with JsonlRecorder(path) as recorder:
        result = CodingAgent(Model(), ToolRegistry([]), max_steps=1, recorder=recorder,
                             log_rejected_responses=enabled).run("Try")
    events = [json.loads(line) for line in path.read_text().splitlines()]
    rejected = next(e for e in events if e["event"] == "action_rejected")
    assert result["termination_reason"] == "step_limit"
    if enabled:
        assert rejected["preview_truncated"]
        assert len(rejected["response_preview"]) == 1000
        assert marker in rejected["error_context"]
        assert "error_offset" in rejected
    else:
        assert "response_preview" not in rejected
        assert marker not in path.read_text()
    assert marker not in json.dumps(result)


def test_preview_flag_requires_log_file(tmp_path, monkeypatch, capsys):
    from coding_agent import cli
    monkeypatch.setattr("sys.argv", ["agent", "--workspace", str(tmp_path), "--log-rejected-responses", "Try"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "requires --log-file" in capsys.readouterr().err


@pytest.mark.parametrize("entry", ["agent", "benchmark"])
def test_cli_preview_flag_reaches_recorder(tmp_path, monkeypatch, capsys, entry):
    from coding_agent import cli
    from benchmarks import evaluate
    responses = iter(['{"action":"finish","args":{}} trailing-debug-marker',
                      '{"action":"finish","args":{"summary":"Done"}}'])

    class Model:
        def respond(self, messages):
            return ModelResponse(next(responses))

    path = tmp_path / "events.jsonl"
    args = [entry, "--log-file", str(path), "--log-rejected-responses"]
    if entry == "agent":
        monkeypatch.setattr(cli, "create_model", lambda *a, **k: Model())
        args += ["--workspace", str(tmp_path), "Try"]
        main = cli.main
    else:
        monkeypatch.setattr(evaluate, "create_model", lambda *a, **k: Model())
        args += ["--task", "add_bug", "--runs", "1", "--output", str(tmp_path / "report.json")]
        main = evaluate.main
    monkeypatch.setattr("sys.argv", args)
    main()
    event = next(json.loads(line) for line in path.read_text().splitlines()
                 if json.loads(line)["event"] == "action_rejected")
    assert "trailing-debug-marker" in event["response_preview"]
    assert "trailing-debug-marker" not in capsys.readouterr().out
