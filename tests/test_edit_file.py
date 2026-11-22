"""Exact-match editing behavior through the registered tool boundary."""
import pytest

from coding_agent.core import Workspace, workspace_tools


def edit(tmp_path, old_text, new_text, path="example.py"):
    return workspace_tools(Workspace(tmp_path)).execute(
        "edit_file", {"path": path, "old_text": old_text, "new_text": new_text})


def test_edit_preserves_surrounding_bytes(tmp_path):
    source = '# café\r\ndef add(a, b):\r\n    return a - b\r\n\r\nKEEP = "unchanged"'
    path = tmp_path / "example.py"
    path.write_bytes(source.encode("utf-8"))
    assert edit(tmp_path, "return a - b", "return a + b") == {
        "edited": "example.py", "replacements": 1}
    assert path.read_bytes() == source.replace("return a - b", "return a + b").encode("utf-8")


@pytest.mark.parametrize("original,old,new,message", [
    ("value = 1\n", "", "x", "non-empty"),
    ("value = 1\n", "value=1", "x", "not found"),
    ("x = 1\ny = 1\n", "1", "2", "multiple"),
    ("aaaa", "aaa", "b", "multiple"),
    ("x = 1\n", "1", "é" * 50_001, "too large"),
    ("x" * 100_001, "x", "y", "too large"),
])
def test_rejected_edits_leave_bytes_unchanged(tmp_path, original, old, new, message):
    path = tmp_path / "example.py"
    before = original.encode("utf-8")
    path.write_bytes(before)
    result = edit(tmp_path, old, new)
    assert result["error"]["code"] == "execution_error"
    assert message in result["error"]["message"]
    assert path.read_bytes() == before


def test_delete_and_exact_size_limit(tmp_path):
    path = tmp_path / "example.py"
    path.write_bytes(b"#" + b"a" * 99_999)
    assert "error" not in edit(tmp_path, "#", "!")
    assert path.stat().st_size == 100_000
    assert "error" not in edit(tmp_path, "!", "")
    assert path.read_bytes() == b"a" * 99_999


@pytest.mark.parametrize("kind", ["missing", "non_python", "traversal", "absolute", "symlink"])
def test_edit_preserves_workspace_restrictions(tmp_path, kind):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"original")
    local = root / "notes.txt"
    local.write_bytes(b"original")
    (root / "link.py").symlink_to(outside)
    paths = {"missing": "missing.py", "non_python": "notes.txt",
             "traversal": "../outside.py", "absolute": str(outside), "symlink": "link.py"}
    result = edit(root, "original", "changed", paths[kind])
    assert result["error"]["code"] == "execution_error"
    assert outside.read_bytes() == b"original"
    assert local.read_bytes() == b"original"
    assert not (root / "missing.py").exists()


@pytest.mark.parametrize("changes", [{"old_text": None}, {"new_text": 1}, {"extra": "x"}])
def test_edit_schema_validation(tmp_path, changes):
    path = tmp_path / "example.py"
    path.write_bytes(b"original")
    arguments = {"path": "example.py", "old_text": "original", "new_text": "changed"}
    arguments.update(changes)
    result = workspace_tools(Workspace(tmp_path)).execute("edit_file", arguments)
    assert result["error"]["code"] == "invalid_arguments"
    assert path.read_bytes() == b"original"
