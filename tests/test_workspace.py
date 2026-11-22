"""Local workspace operations, restrictions, and registered tool adapters."""
import pytest

from coding_agent.core import Workspace, workspace_tools


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


def test_pytest_execution(tmp_path):
    (tmp_path / "test_demo.py").write_text("def test_ok(): assert 2 + 2 == 4\n")
    result = Workspace(tmp_path).execute("run_tests", {})
    assert result["exit_code"] == 0


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
