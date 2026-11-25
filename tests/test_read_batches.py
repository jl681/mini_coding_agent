"""Sequential read batches, whole-batch preflight, and request accounting."""
import copy
import json
import sys

import pytest
import requests

from coding_agent.core import CodingAgent, Workspace, workspace_tools
from coding_agent.events import JsonlRecorder
from coding_agent.models import OpenAICompatibleModel
from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments


def call(name, args, identity):
    return {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


FINISH = call("finish", {"summary": "Done"}, "finish")


def setup_model(monkeypatch, batches):
    batches = iter(batches)
    payloads = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def post(self, url, **kwargs):
            payloads.append(copy.deepcopy(kwargs["json"]))
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "tool_calls": next(batches), "content": None}}]}

    monkeypatch.setattr(requests, "Session", Session)
    return OpenAICompatibleModel("test", "http://localhost/v1", native_tools=True), payloads


def test_three_reads_one_model_turn_sequential_results_and_events(tmp_path, monkeypatch):
    order = []
    tool = FunctionTool("read", "Read", string_arguments("path"),
                        lambda args: order.append(args["path"]) or {"content": args["path"]}, read_only=True)
    batch = [call("read", {"path": name}, str(i)) for i, name in enumerate(("a.py", "b.py", "c.py"))]
    model, payloads = setup_model(monkeypatch, [batch, [FINISH]])
    log = tmp_path / "events.jsonl"
    with JsonlRecorder(log) as recorder:
        run = CodingAgent(model, ToolRegistry([tool]), recorder=recorder).run("Read")
    assert order == ["a.py", "b.py", "c.py"]
    assert len(payloads) == run["steps"] == 2
    assert run["tool_calls"] == 3
    assert len(run["trace"]) == 4
    assert [t["step"] for t in run["trace"]] == [1, 1, 1, 2]
    assert [t["call_id"] for t in run["trace"][:3]] == ["0", "1", "2"]
    replies = payloads[1]["messages"][-3:]
    assert [r["tool_call_id"] for r in replies] == ["0", "1", "2"]
    assert [json.loads(r["content"])["content"] for r in replies] == order
    events = [json.loads(line) for line in log.read_text().splitlines()]
    batch_events = [e["event"] for e in events if e["event"].startswith("batch_")]
    assert batch_events == ["batch_started", "batch_completed"]
    assert [e["call_id"] for e in events if e["event"] == "tool_completed"] == ["0", "1", "2"]


@pytest.mark.parametrize("second", [
    call("write_file", {"path": "a.py", "content": "changed"}, "second"),
    call("edit_file", {"path": "a.py", "old_text": "original", "new_text": "changed"}, "second"),
    call("run_tests", {}, "second"), call("finish", {"summary": "Done"}, "second"),
    call("read_file", {}, "second"), call("missing", {}, "second"),
])
def test_invalid_or_mixed_batch_executes_nothing(tmp_path, monkeypatch, second):
    (tmp_path / "a.py").write_text("original")
    workspace = Workspace(tmp_path)
    executed = []
    original = workspace.execute

    def execute(name, args):
        executed.append(name)
        return original(name, args)

    monkeypatch.setattr(workspace, "execute", execute)
    model, payloads = setup_model(monkeypatch, [[call("read_file", {"path": "a.py"}, "first"), second], [FINISH]])
    run = CodingAgent(model, workspace_tools(workspace)).run("Read")
    assert executed == []
    assert (tmp_path / "a.py").read_text() == "original"
    assert run["trace"][0]["action"] == "invalid_action"
    replies = payloads[1]["messages"][-2:]
    assert [r["tool_call_id"] for r in replies] == ["first", "second"]
    assert all("error" in json.loads(r["content"]) for r in replies)


def test_oversized_read_batch_rejected_before_execution(monkeypatch):
    calls = []
    tool = FunctionTool("read", "Read", string_arguments(), lambda args: calls.append(args) or {}, read_only=True)
    model, _ = setup_model(monkeypatch, [[call("read", {}, str(i)) for i in range(9)], [FINISH]])
    run = CodingAgent(model, ToolRegistry([tool])).run("Read")
    assert calls == []
    assert "limited to 8" in run["trace"][0]["result"]["error"]["message"]


def test_read_error_is_returned_to_correct_call_and_remaining_reads_continue(tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text("content")
    model, payloads = setup_model(monkeypatch, [[call("read_file", {"path": "missing.py"}, "missing"),
                                                call("read_file", {"path": "ok.py"}, "ok")], [FINISH]])
    run = CodingAgent(model, workspace_tools(Workspace(tmp_path))).run("Read")
    assert run["termination_reason"] == "finished"
    replies = payloads[1]["messages"][-2:]
    assert json.loads(replies[0]["content"])["error"]["code"] == "execution_error"
    assert json.loads(replies[1]["content"])["content"] == "content"


def test_malformed_later_arguments_prevent_earlier_call(monkeypatch):
    calls = []
    tool = FunctionTool("read", "Read", string_arguments(), lambda args: calls.append(args) or {}, read_only=True)
    malformed = call("read", {}, "bad")
    malformed["function"]["arguments"] = '{} trailing'
    model, _ = setup_model(monkeypatch, [[call("read", {}, "first"), malformed], [FINISH]])
    CodingAgent(model, ToolRegistry([tool])).run("Read")
    assert calls == []


def test_mcp_read_only_requires_explicit_host_declaration(tmp_path):
    from coding_agent.mcp_client import MCPClient
    with MCPClient([sys.executable, "-m", "coding_agent.mcp_search_server", "--workspace", str(tmp_path)]) as client:
        # The server advertises readOnlyHint, but it is not a trusted permission.
        normal = client.tools({"search_repo"})
        trusted = client.tools({"search_repo"}, read_only={"search_repo"})
        assert normal[0].read_only is False
        assert trusted[0].read_only is True


def test_benchmark_counts_model_turns_separately_from_tool_calls(monkeypatch):
    from benchmarks.evaluate import evaluate
    model, _ = setup_model(monkeypatch, [[call("list_files", {}, "one"), call("list_files", {}, "two")], [FINISH]])
    report = evaluate(tasks=["add_bug"], runs=1, model_factory=lambda task, run: model)
    result = report["results"][0]
    assert result["steps"] == 2
    assert result["tool_calls"] == {"list_files": 2}
    assert len(result["trace"]) == 3
