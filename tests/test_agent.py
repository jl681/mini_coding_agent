"""Agent decisions, tool observations, recovery, and termination with scripted models."""
import json
from coding_agent.core import ModelResponse

import pytest

from coding_agent.core import CodingAgent, Workspace, workspace_tools
from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments


class ScriptedModel:
    def __init__(self, actions):
        self.actions = iter(actions)
    def respond(self, messages):
        return ModelResponse(json.dumps(next(self.actions)))


def test_scripted_agent(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    model = ScriptedModel([
        {"action": "read_file", "args": {"path": "a.py"}},
        {"action": "write_file", "args": {"path": "a.py", "content": "x = 2\n"}},
        {"action": "finish", "args": {"summary": "Fixed"}},
    ])
    result = CodingAgent(model, workspace_tools(Workspace(tmp_path))).run("Fix x")
    assert result["summary"] == "Fixed"
    assert result["steps"] == 3
    assert (tmp_path / "a.py").read_text() == "x = 2\n"


def test_step_limit(tmp_path):
    model = ScriptedModel([{"action": "list_files", "args": {}}] * 5)
    result = CodingAgent(model, workspace_tools(Workspace(tmp_path)), max_steps=2).run("Explore")
    assert result["steps"] == 2
    assert "limit" in result["summary"]


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
                return ModelResponse(json.dumps({"action": "echo", "args": {"message": "hello"}}))
            assert json.loads(messages[-1]["content"].removeprefix("Tool observation: ")) == {"echo": "hello"}
            return ModelResponse(json.dumps({"action": "finish", "args": {"summary": "Done"}}))

    result = CodingAgent(Model(), registry).run("Echo hello")
    assert calls == [{"message": "hello"}]
    assert result["termination_reason"] == "finished"


@pytest.mark.parametrize("bad_response", ["not json", "[]", '{"action": []}',
                                          '{"action":"finish","args":{}}'])
def test_malformed_decision_recovery(bad_response):
    responses = iter([bad_response, '{"action":"finish","args":{"summary":"Recovered"}}'])

    class Model:
        def respond(self, messages):
            return ModelResponse(next(responses))

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
            return ModelResponse(json.dumps(next(responses)))

    result = CodingAgent(Model(), workspace_tools(Workspace(tmp_path))).run("Fix add")
    assert result["termination_reason"] == "finished"
    assert result["trace"][3]["result"]["exit_code"] == 0
    assert "a + b" in (tmp_path / "calculator.py").read_text()


def test_step_limit_and_invalid_budget():
    class Model:
        def respond(self, messages):
            return ModelResponse('{"action":"missing","args":{}}')

    result = CodingAgent(Model(), ToolRegistry([]), max_steps=2).run("Try")
    assert result["steps"] == 2
    assert result["termination_reason"] == "step_limit"
    with pytest.raises(ValueError, match="positive"):
        CodingAgent(Model(), ToolRegistry([]), max_steps=0)


def test_agent_recovers_from_mismatch_and_verifies_repair(tmp_path):
    source = '"""Calculator fixture."""\n\ndef add(a, b):\n    return a - b\n\ndef multiply(a, b):\n    return a * b\n'
    path = tmp_path / "calculator.py"
    path.write_text(source)
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add, multiply\n"
        "def test_add(): assert add(2, 3) == 5\n"
        "def test_multiply(): assert multiply(2, 3) == 6\n")

    class Model:
        def __init__(self):
            self.step = 0

        def respond(self, messages):
            self.step += 1
            assert '"name": "edit_file"' in messages[0]["content"]
            previous = messages[-1]["content"]
            if self.step == 1:
                decision = {"action": "read_file", "args": {"path": "calculator.py"}}
            elif self.step == 2:
                decision = {"action": "edit_file", "args": {
                    "path": "calculator.py", "old_text": "return a-b", "new_text": "return a + b"}}
            elif self.step == 3:
                assert "not found" in previous
                assert path.read_text() == source
                decision = {"action": "read_file", "args": {"path": "calculator.py"}}
            elif self.step == 4:
                assert "return a - b" in previous
                decision = {"action": "edit_file", "args": {
                    "path": "calculator.py", "old_text": "return a - b", "new_text": "return a + b"}}
            elif self.step == 5:
                assert '"replacements": 1' in previous
                decision = {"action": "run_tests", "args": {}}
            else:
                assert '"exit_code": 0' in previous
                decision = {"action": "finish", "args": {"summary": "Fixed and tested"}}
            return ModelResponse(json.dumps(decision))

    result = CodingAgent(Model(), workspace_tools(Workspace(tmp_path))).run("Fix add")
    assert result["termination_reason"] == "finished"
    assert result["steps"] == 6
    assert path.read_text() == source.replace("return a - b", "return a + b")
