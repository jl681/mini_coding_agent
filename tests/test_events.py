"""Durable event ordering, failure diagnostics, and bounded observations."""
import json

import pytest
import requests

from coding_agent.core import CodingAgent, ModelResponse
from coding_agent.events import JsonlRecorder
from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments


FINISH = ModelResponse('{"action":"finish","args":{"summary":"Done"}}')


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def respond(self, messages):
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_events_flushed_in_order_before_close(tmp_path):
    path = tmp_path / "events.jsonl"
    tool = FunctionTool("echo", "Echo", string_arguments("message"), lambda args: args)
    responses = [ModelResponse('{"action":"echo","args":{"message":"hello"}}',
                               {"eval_count": 12, "done_reason": "stop"}), FINISH]
    with JsonlRecorder(path) as recorder:
        result = CodingAgent(ScriptedModel(responses), ToolRegistry([tool]), recorder=recorder,
                             event_context={"task": "demo", "trial": 1}).run("Echo hello")
        events = read_events(path)  # Still open: flush must make events visible.
        assert [e["event"] for e in events] == [
            "run_started", "model_request_started", "model_request_completed", "tool_started",
            "tool_completed", "model_request_started", "model_request_completed", "run_terminated"]
        assert {e["run_id"] for e in events} == {result["run_id"]}
        assert all(e["task"] == "demo" and e["trial"] == 1 for e in events)
        assert all(e["timestamp"] and e["schema_version"] == 1 for e in events)
        assert len(events[0]["prompt_sha256"]) == 64
        assert events[2]["metadata"] == {"eval_count": 12, "done_reason": "stop"}
        assert events[4]["is_error"] is False
        assert events[-1]["termination_reason"] == "finished"
        assert "prompt" not in events[0]


@pytest.mark.parametrize("error,reason", [(requests.ReadTimeout("Slow"), "error"),
                                         (KeyboardInterrupt(), "interrupted")])
def test_model_failure_persists_events_and_reraises(tmp_path, error, reason):
    path = tmp_path / "events.jsonl"
    with JsonlRecorder(path) as recorder:
        agent = CodingAgent(ScriptedModel([error]), ToolRegistry([]), recorder=recorder)
        with pytest.raises(type(error)):
            agent.run("Fail")
        events = read_events(path)
        assert [e["event"] for e in events] == [
            "run_started", "model_request_started", "model_request_failed", "run_terminated"]
        assert events[-2]["error_type"] == type(error).__name__
        assert events[-1]["termination_reason"] == reason
        assert agent.trace == []


def test_unexpected_tool_failure_is_distinct_from_model_failure(tmp_path):
    def fail(args):
        raise RuntimeError("Tool crashed")

    path = tmp_path / "events.jsonl"
    tool = FunctionTool("fail", "Fail", string_arguments(), fail)
    with JsonlRecorder(path) as recorder:
        agent = CodingAgent(ScriptedModel([ModelResponse('{"action":"fail","args":{}}')]),
                            ToolRegistry([tool]), recorder=recorder)
        with pytest.raises(RuntimeError, match="Tool crashed"):
            agent.run("Fail")
    events = read_events(path)
    assert events[-2]["event"] == "tool_failed"
    assert events[-2]["tool"] == "fail"
    assert events[-1]["termination_reason"] == "error"
    assert not any(e["event"] == "model_request_failed" for e in events)


def test_token_limit_rejects_action_and_records_separate_event(tmp_path):
    path = tmp_path / "events.jsonl"
    calls = []
    tool = FunctionTool("write", "Write", string_arguments(), lambda args: calls.append(args) or {})
    response = ModelResponse('{"action":"write","args":{}}', {"done_reason": "length", "eval_count": 1024})
    with JsonlRecorder(path) as recorder:
        result = CodingAgent(ScriptedModel([response]), ToolRegistry([tool]),
                             max_steps=1, recorder=recorder).run("Try")
    events = read_events(path)
    assert calls == []  # Even syntactically valid truncated output must not execute.
    assert any(e["event"] == "model_output_truncated" for e in events)
    assert result["termination_reason"] == "step_limit"
    assert "token limit" in result["trace"][0]["result"]["error"]["message"]
    assert events[-1]["termination_reason"] == "step_limit"


def test_append_and_bound_without_changing_tool_result(tmp_path):
    path = tmp_path / "events.jsonl"
    value = "x" * 5000
    tool = FunctionTool("large", "Large", string_arguments(), lambda args: {"text": value})
    for _ in range(2):
        responses = [ModelResponse('{"action":"large","args":{}}'), FINISH]
        with JsonlRecorder(path) as recorder:
            result = CodingAgent(ScriptedModel(responses), ToolRegistry([tool]), recorder=recorder).run("Go")
        assert result["trace"][0]["result"]["text"] == value
    events = read_events(path)
    assert len({e["run_id"] for e in events}) == 2
    outputs = [e for e in events if e["event"] == "tool_completed"]
    assert all(e["result"]["text"].endswith("[truncated]") for e in outputs)


def test_registry_error_observation_recorded_without_abort(tmp_path):
    path = tmp_path / "events.jsonl"
    responses = [ModelResponse('{"action":"unknown","args":{}}'), FINISH]
    with JsonlRecorder(path) as recorder:
        result = CodingAgent(ScriptedModel(responses), ToolRegistry([]), recorder=recorder).run("Try")
    event = next(e for e in read_events(path) if e["event"] == "tool_completed")
    assert event["is_error"]
    assert event["result"]["error"]["code"] == "unknown_tool"
    assert result["termination_reason"] == "finished"


def test_cli_log_file(tmp_path, monkeypatch, capsys):
    from coding_agent import cli
    path = tmp_path / "cli.jsonl"
    monkeypatch.setattr(cli, "create_model", lambda *args, **kwargs: ScriptedModel([FINISH]))
    monkeypatch.setattr("sys.argv", ["agent", "--workspace", str(tmp_path), "--log-file", str(path), "Go"])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert read_events(path)[-1]["run_id"] == result["run_id"]


def test_benchmark_trials_correlate_to_log(tmp_path):
    from benchmarks.evaluate import evaluate
    path = tmp_path / "benchmark.jsonl"
    report = evaluate(tasks=["add_bug"], runs=2, log_file=str(path),
                      model_factory=lambda task, run: ScriptedModel([FINISH]))
    starts = [e for e in read_events(path) if e["event"] == "run_started"]
    assert [e["trial"] for e in starts] == [1, 2]
    assert all(e["task"] == "add_bug" for e in starts)
    assert [r["run_id"] for r in report["results"]] == [e["run_id"] for e in starts]


def test_benchmark_failure_run_id_matches_persisted_events(tmp_path):
    from benchmarks.evaluate import evaluate
    path = tmp_path / "failed.jsonl"
    report = evaluate(tasks=["add_bug"], runs=1, log_file=str(path),
                      model_factory=lambda task, run: ScriptedModel([requests.ReadTimeout("Slow")]))
    result = report["results"][0]
    assert result["outcome"] == "model_error"
    assert result["run_id"] == read_events(path)[0]["run_id"]
    assert read_events(path)[-1]["termination_reason"] == "error"


def test_benchmark_cli_log_option(tmp_path, monkeypatch, capsys):
    from benchmarks import evaluate as module
    path = tmp_path / "events.jsonl"
    output = tmp_path / "report.json"
    monkeypatch.setattr(module, "create_model", lambda *args, **kwargs: ScriptedModel([FINISH]))
    monkeypatch.setattr("sys.argv", ["evaluate", "--task", "add_bug", "--runs", "1",
                                    "--log-file", str(path), "--output", str(output)])
    module.main()
    report = json.loads(output.read_text())
    assert report["results"][0]["run_id"] == read_events(path)[0]["run_id"]
    assert json.loads(capsys.readouterr().out)["total_runs"] == 1
