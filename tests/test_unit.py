import json
import pytest
from coding_agent.core import CodingAgent, Workspace

class ScriptedModel:
    def __init__(self, actions):
        self.actions = iter(actions)
    def respond(self, messages):
        return json.dumps(next(self.actions))

def test_path_traversal(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.path("../secret.py")
    with pytest.raises(ValueError):
        ws.path("/etc/passwd")

def test_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside.py"
    (tmp_path / "link.py").symlink_to(outside)
    with pytest.raises(ValueError):
        Workspace(tmp_path).path("link.py")

def test_read_write(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    ws = Workspace(tmp_path)
    assert "x = 1" in ws.execute("read_file", {"path": "a.py"})["content"]
    ws.execute("write_file", {"path": "a.py", "content": "x = 2\n"})
    assert (tmp_path / "a.py").read_text() == "x = 2\n"

def test_reject_new_files(tmp_path):
    with pytest.raises(ValueError):
        Workspace(tmp_path).execute("write_file", {"path": "new.py", "content": "pass"})

def test_scripted_agent(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    model = ScriptedModel([
        {"action": "read_file", "args": {"path": "a.py"}},
        {"action": "write_file", "args": {"path": "a.py", "content": "x = 2\n"}},
        {"action": "finish", "args": {"summary": "Fixed"}},
    ])
    result = CodingAgent(model, Workspace(tmp_path)).run("Fix x")
    assert result["summary"] == "Fixed"
    assert result["steps"] == 3
    assert (tmp_path / "a.py").read_text() == "x = 2\n"

def test_step_limit(tmp_path):
    model = ScriptedModel([{"action": "list_files", "args": {}}] * 5)
    result = CodingAgent(model, Workspace(tmp_path), max_steps=2).run("Explore")
    assert result["steps"] == 2
    assert "limit" in result["summary"]

def test_pytest_execution(tmp_path):
    (tmp_path / "test_demo.py").write_text("def test_ok(): assert 2 + 2 == 4\n")
    result = Workspace(tmp_path).execute("run_tests", {})
    assert result["exit_code"] == 0
