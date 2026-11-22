import json

import pytest

from coding_agent.core import CodingAgent, Workspace, workspace_tools
from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments


def test_custom_tool_and_prompt_without_workspace():
    calls = []
    tool = FunctionTool("echo", "Echo a message.", string_arguments("message"),
                        lambda args: calls.append(args) or {"echo": args["message"]})
    registry = ToolRegistry([tool])

    class Model:
        def respond(self, messages):
            prompt = messages[0]["content"]
            assert '"name": "echo"' in prompt
            assert "Echo a message." in prompt
            assert "read_file" not in prompt
            if len(messages) == 2:
                return json.dumps({"action": "echo", "args": {"message": "hello"}})
            assert json.loads(messages[-1]["content"].removeprefix("Tool observation: ")) == {"echo": "hello"}
            return json.dumps({"action": "finish", "args": {"summary": "Done"}})

    result = CodingAgent(Model(), registry).run("Echo hello")
    assert calls == [{"message": "hello"}]
    assert result["termination_reason"] == "finished"


@pytest.mark.parametrize("arguments", [{}, {"path": 42}, {"path": "a.py", "extra": "x"}, []])
def test_invalid_arguments_never_execute(arguments):
    calls = []
    registry = ToolRegistry([
        FunctionTool("read", "Read", string_arguments("path"), lambda args: calls.append(args))
    ])
    assert registry.execute("read", arguments)["error"]["code"] == "invalid_arguments"
    assert calls == []


def test_unknown_and_duplicate_tools():
    tool = FunctionTool("echo", "Echo", string_arguments(), lambda args: {})
    assert ToolRegistry([]).execute("missing", {})["error"]["code"] == "unknown_tool"
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry([tool, tool])
    with pytest.raises(ValueError):
        ToolRegistry([FunctionTool("finish", "Reserved", string_arguments(), lambda args: {})])


def test_reject_unsupported_schema():
    schema = string_arguments("count")
    schema["properties"]["count"] = {"type": "integer"}
    with pytest.raises(ValueError, match="schemas"):
        ToolRegistry([FunctionTool("count", "Count", schema, lambda args: {})])


@pytest.mark.parametrize("path", ["../outside.py", "new.py"])
def test_registry_preserves_workspace_write_restrictions(tmp_path, path):
    registry = workspace_tools(Workspace(tmp_path))
    result = registry.execute("write_file", {"path": path, "content": "pass"})
    assert result["error"]["code"] == "execution_error"
    assert not (tmp_path / "new.py").exists()


def test_filesystem_errors_become_observations(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)

    def fail(action, args):
        raise PermissionError("Access denied")

    monkeypatch.setattr(workspace, "execute", fail)
    result = workspace_tools(workspace).execute("list_files", {})
    assert result == {"error": {"code": "execution_error", "message": "Access denied"}}


@pytest.mark.parametrize("bad_response", ["not json", "[]", '{"action": []}',
                                          '{"action":"finish","args":{}}'])
def test_malformed_decision_recovery(bad_response):
    responses = iter([bad_response, '{"action":"finish","args":{"summary":"Recovered"}}'])

    class Model:
        def respond(self, messages):
            return next(responses)

    result = CodingAgent(Model(), ToolRegistry([])).run("Recover")
    assert result["trace"][0]["result"]["error"]["code"] == "invalid_action"
    assert result["summary"] == "Recovered"


def test_invalid_call_recovery_edit_and_test(tmp_path):
    (tmp_path / "calculator.py").write_text("def add(a, b): return a - b\n")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\ndef test_add(): assert add(2, 3) == 5\n")
    responses = iter([
        {"action": "read_file", "args": {}},
        {"action": "read_file", "args": {"path": "calculator.py"}},
        {"action": "write_file", "args": {"path": "calculator.py",
                                            "content": "def add(a, b): return a + b\n"}},
        {"action": "run_tests", "args": {}},
        {"action": "finish", "args": {"summary": "Fixed"}},
    ])

    class Model:
        def respond(self, messages):
            if len(messages) == 4:
                assert "invalid_arguments" in messages[-1]["content"]
            return json.dumps(next(responses))

    result = CodingAgent(Model(), workspace_tools(Workspace(tmp_path))).run("Fix add")
    assert result["termination_reason"] == "finished"
    assert result["trace"][3]["result"]["exit_code"] == 0
    assert "a + b" in (tmp_path / "calculator.py").read_text()


def test_step_limit_and_invalid_budget():
    class Model:
        def respond(self, messages):
            return '{"action":"missing","args":{}}'

    result = CodingAgent(Model(), ToolRegistry([]), max_steps=2).run("Try")
    assert result["steps"] == 2
    assert result["termination_reason"] == "step_limit"
    with pytest.raises(ValueError, match="positive"):
        CodingAgent(Model(), ToolRegistry([]), max_steps=0)


def test_test_runner_timeout_uses_current_interpreter(tmp_path, monkeypatch):
    import subprocess
    import sys

    def timeout(command, **kwargs):
        assert command == [sys.executable, "-m", "pytest", "-q"]
        assert kwargs["cwd"] == tmp_path.resolve()
        assert kwargs["timeout"] == 7
        raise subprocess.TimeoutExpired(command, 7)

    monkeypatch.setattr("coding_agent.core.subprocess.run", timeout)
    result = workspace_tools(Workspace(tmp_path, timeout=7)).execute("run_tests", {})
    assert result == {"exit_code": -1, "output": "Test timeout"}
