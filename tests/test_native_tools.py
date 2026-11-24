"""Native function-call normalization, correlation, and rejection boundaries."""
import copy
import json
import sys

import pytest
import requests

from coding_agent.core import CodingAgent
from coding_agent.events import JsonlRecorder
from coding_agent.models import AgentAction, ModelError, OpenAICompatibleModel, create_model
from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments


def call(name="echo", arguments='{"text":"hello"}', call_id="call_1"):
    return {"type": "function", "id": call_id,
            "function": {"name": name, "arguments": arguments}}


def result(calls=None, content=None, reason="tool_calls", reasoning=None):
    message = {"content": content}
    if calls is not None:
        message["tool_calls"] = calls
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": reason}]}


FINISH = result([call("finish", '{"summary":"Done"}', "finish_id")])


def mock_native(monkeypatch, responses):
    responses = iter(responses)
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
            return next(responses)

    monkeypatch.setattr(requests, "Session", Session)
    return payloads


def model():
    return OpenAICompatibleModel("model", "http://localhost/v1", native_tools=True)


def registry(calls):
    return ToolRegistry([FunctionTool("echo", "Echo", string_arguments("text"),
                                     lambda args: calls.append(args) or args)])


def test_native_round_trip_preserves_ids_reasoning_and_excludes_json_mode(tmp_path, monkeypatch):
    payloads = mock_native(monkeypatch, [result([call()], reasoning="private reasoning"), FINISH])
    calls = []
    log = tmp_path / "events.jsonl"
    with JsonlRecorder(log) as recorder:
        run = CodingAgent(model(), registry(calls), recorder=recorder).run("Echo")
    assert run["termination_reason"] == "finished"
    assert calls == [{"text": "hello"}]
    first = payloads[0]
    assert "parallel_tool_calls" not in first
    assert first["tool_choice"] == "auto"
    assert "response_format" not in first
    assert {t["function"]["name"] for t in first["tools"]} == {"echo", "finish"}
    assert first["tools"][0]["function"]["parameters"] == string_arguments("text")
    second = payloads[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "call_1"
    assert second[-2]["reasoning_content"] == "private reasoning"
    assert second[-1] == {"role": "tool", "tool_call_id": "call_1", "content": '{"text": "hello"}'}
    assert "private reasoning" not in log.read_text()
    assert "private reasoning" not in json.dumps(run)


@pytest.mark.parametrize("arguments", ['{"text":"hello"} trailing', '{"text":"a","text":"b"}',
                                       '[]', '{"text": NaN}'])
def test_malformed_arguments_are_recoverable_and_never_executed(monkeypatch, arguments):
    payloads = mock_native(monkeypatch, [result([call(arguments=arguments)]), FINISH])
    calls = []
    run = CodingAgent(model(), registry(calls)).run("Echo")
    assert calls == []
    assert run["trace"][0]["action"] == "invalid_action"
    error = json.loads(payloads[1]["messages"][-1]["content"])["error"]
    assert "exactly one provided function" in error["recovery"]
    assert payloads[1]["messages"][-1]["tool_call_id"] == "call_1"
    assert run["termination_reason"] == "finished"


def test_multiple_calls_all_rejected_and_all_ids_answered(monkeypatch):
    payloads = mock_native(monkeypatch, [result([call(call_id="one"), call(call_id="two")]), FINISH])
    calls = []
    run = CodingAgent(model(), registry(calls)).run("Echo")
    assert calls == []
    assert run["trace"][0]["action"] == "invalid_action"
    replies = payloads[1]["messages"][-2:]
    assert [r["tool_call_id"] for r in replies] == ["one", "two"]
    assert all(json.loads(r["content"])["error"]["code"] == "invalid_action" for r in replies)


@pytest.mark.parametrize("content", ['{"action":"echo","args":{"text":"hello"}}', 'All done.'])
def test_text_only_does_not_bypass_native_mode(monkeypatch, content):
    payloads = mock_native(monkeypatch, [result(content=content, reason="stop"), FINISH])
    calls = []
    run = CodingAgent(model(), registry(calls)).run("Echo")
    assert calls == []
    assert run["trace"][0]["action"] == "invalid_action"
    assert payloads[1]["messages"][-1]["role"] == "user"


@pytest.mark.parametrize("bad_call,code", [
    (call(name="unknown"), "unknown_tool"),
    (call(arguments='{"wrong":"value"}'), "invalid_arguments"),
])
def test_registry_validation_still_applies(monkeypatch, bad_call, code):
    payloads = mock_native(monkeypatch, [result([bad_call]), FINISH])
    calls = []
    run = CodingAgent(model(), registry(calls)).run("Echo")
    assert calls == []
    assert run["trace"][0]["result"]["error"]["code"] == code
    assert json.loads(payloads[1]["messages"][-1]["content"])["error"]["code"] == code


def test_length_never_executes_partial_calls_and_history_is_valid(monkeypatch):
    payloads = mock_native(monkeypatch, [result([call(arguments='{"text":')], reason="length"), FINISH])
    calls = []
    run = CodingAgent(model(), registry(calls)).run("Echo")
    assert calls == []
    assert run["trace"][0]["action"] == "invalid_action"
    assistant = payloads[1]["messages"][-2]
    assert assistant["content"] == ""
    assert "tool_calls" not in assistant
    assert payloads[1]["messages"][-1]["role"] == "user"


@pytest.mark.parametrize("bad_call", [
    {"type": "function", "function": {"name": "echo", "arguments": "{}"}},
    {"type": "function", "id": "id", "function": {"name": "echo", "arguments": {}}},
])
def test_unusable_call_envelope_fails_without_execution(monkeypatch, bad_call):
    mock_native(monkeypatch, [result([bad_call])])
    calls = []
    with pytest.raises(ModelError, match="envelope"):
        CodingAgent(model(), registry(calls)).run("Echo")
    assert calls == []


def test_native_action_is_provider_neutral(monkeypatch):
    mock_native(monkeypatch, [result([call()])])
    response = model().respond([], tools=[{"name": "echo", "description": "Echo", "input_schema": string_arguments("text")}])
    assert response.action == AgentAction("echo", {"text": "hello"}, "call_1")


def test_specs_are_copies_and_ollama_rejects_unsupported_mode():
    tools = registry([])
    specs = tools.specifications()
    specs[0]["input_schema"]["properties"].clear()
    assert tools.specifications()[0]["input_schema"]["properties"] == {"text": {"type": "string"}}
    with pytest.raises(ValueError, match="only by openai-compatible"):
        create_model(native_tools=True)


@pytest.mark.parametrize("entry", ["agent", "benchmark"])
def test_deepseek_profile_uses_native_tools_from_cli(tmp_path, monkeypatch, capsys, entry):
    from coding_agent import cli
    from benchmarks import evaluate
    payloads = mock_native(monkeypatch, [FINISH])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    args = [entry, "--profile", "deepseek"]
    if entry == "agent":
        args += ["--workspace", str(tmp_path), "Finish"]
        main = cli.main
    else:
        args += ["--task", "add_bug", "--runs", "1", "--output", str(tmp_path / "report.json")]
        main = evaluate.main
    monkeypatch.setattr(sys, "argv", args)
    main()
    assert "tools" in payloads[0]
    assert "response_format" not in payloads[0]
    output = json.loads(capsys.readouterr().out)
    if entry == "benchmark":
        assert output["native_tools"] is True
    else:
        assert output["termination_reason"] == "finished"


def test_native_call_can_invoke_mcp_through_common_registry(tmp_path, monkeypatch):
    from coding_agent.mcp_client import MCPClient
    (tmp_path / "example.py").write_text("value = 42\n")
    payloads = mock_native(monkeypatch, [result([call("mcp_search_repo", '{"query":"42"}', "search_id")]), FINISH])
    with MCPClient([sys.executable, "-m", "coding_agent.mcp_search_server",
                    "--workspace", str(tmp_path)]) as client:
        tools = ToolRegistry(client.tools({"search_repo"}))
        run = CodingAgent(model(), tools).run("Search")
    observation = json.loads(payloads[1]["messages"][-1]["content"])
    assert observation["structuredContent"]["matches"][0]["path"] == "example.py"
    assert payloads[1]["messages"][-1]["tool_call_id"] == "search_id"
    assert run["termination_reason"] == "finished"


def test_duplicate_call_ids_fail_without_executing_any_call(monkeypatch):
    mock_native(monkeypatch, [result([call(call_id="same"), call(call_id="same")])])
    calls = []
    with pytest.raises(ModelError, match="duplicate call ID"):
        CodingAgent(model(), registry(calls)).run("Echo")
    assert calls == []


def test_invalid_finish_arguments_receive_correlated_error(monkeypatch):
    payloads = mock_native(monkeypatch, [result([call("finish", '{}', "bad_finish")]), FINISH])
    run = CodingAgent(model(), ToolRegistry([])).run("Finish")
    assert run["termination_reason"] == "finished"
    assert run["steps"] == 2
    reply = payloads[1]["messages"][-1]
    assert reply["tool_call_id"] == "bad_finish"
    assert "summary" in json.loads(reply["content"])["error"]["message"]
