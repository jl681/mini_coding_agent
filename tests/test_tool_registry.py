"""Tool registration, schema validation, and dispatch contracts."""
import pytest

from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments


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
