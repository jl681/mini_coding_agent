"""Real subprocess MCP tests; no Ollama, network, or external SDK required."""
import json
import sys

import pytest

from coding_agent.core import CodingAgent, Workspace, workspace_tools
from coding_agent.mcp_client import MCPClient, MCPError, PROTOCOL_VERSION
from coding_agent.mcp_search_server import search_repo
from coding_agent.tools import ToolRegistry


def search_client(root, timeout=3):
    return MCPClient([sys.executable, "-m", "coding_agent.mcp_search_server",
                      "--workspace", str(root)], timeout=timeout)


def test_discovery_call_validation_and_persistent_connection(tmp_path):
    (tmp_path / "example.py").write_text("x = 1\nneedle = 2\n")
    with search_client(tmp_path) as client:
        process = client.process
        tools = ToolRegistry(client.tools({"search_repo"}))
        spec = json.loads(tools.describe())[0]
        assert spec["name"] == "mcp_search_repo"
        assert spec["input_schema"]["required"] == ["query"]
        assert tools.execute("mcp_search_repo", {})["error"]["code"] == "invalid_arguments"
        result = tools.execute("mcp_search_repo", {"query": "needle"})
        assert result["structuredContent"] == {"matches": [
            {"path": "example.py", "line": 2, "text": "needle = 2"}], "truncated": False}
        assert tools.execute("mcp_search_repo", {"query": "absent"})["structuredContent"]["matches"] == []
        assert client.process is process
        assert tools.execute("mcp_search_repo", {"query": ""})["error"]["code"] == "mcp_tool_error"
    assert process.poll() is not None
    assert client.process is None
    assert tools.execute("mcp_search_repo", {"query": "x"})["error"]["code"] == "mcp_connection_error"


def test_agent_can_search_then_edit_through_same_registry(tmp_path):
    (tmp_path / "calculator.py").write_text("def add(a, b): return a - b\n")
    actions = iter([
        {"action": "mcp_search_repo", "args": {"query": "a - b"}},
        {"action": "edit_file", "args": {"path": "calculator.py", "old_text": "a - b", "new_text": "a + b"}},
        {"action": "finish", "args": {"summary": "Fixed"}},
    ])

    class Model:
        def respond(self, messages):
            assert "mcp_search_repo" in messages[0]["content"]
            if len(messages) == 4:
                assert '"path": "calculator.py"' in messages[-1]["content"]
            return json.dumps(next(actions))

    with search_client(tmp_path) as client:
        tools = workspace_tools(Workspace(tmp_path)).with_tools(client.tools({"search_repo"}))
        result = CodingAgent(Model(), tools).run("Fix add")
    assert result["termination_reason"] == "finished"
    assert (tmp_path / "calculator.py").read_text() == "def add(a, b): return a + b\n"


def test_search_skips_symlinks_large_files_and_excluded_directories(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.py").write_text("needle")
    (root / "link.py").symlink_to(tmp_path / "outside.py")
    (root / "linked_dir").symlink_to(tmp_path, target_is_directory=True)
    (root / ".venv").mkdir()
    (root / ".venv" / "skip.py").write_text("needle")
    (root / "large.py").write_text("needle" * 20_000)
    (root / "invalid.py").write_bytes(b"\xffneedle")
    (root / "notes.txt").write_text("needle")
    (root / "ok.py").write_text("needle")
    result = search_repo(root, "needle")
    assert result == {"matches": [{"path": "ok.py", "line": 1, "text": "needle"}], "truncated": False}
    assert (tmp_path / "outside.py").read_text() == "needle"


def test_search_output_is_bounded(tmp_path):
    (tmp_path / "many.py").write_text(("needle" + "x" * 600 + "\n") * 110)
    result = search_repo(tmp_path, "needle")
    assert result["truncated"]
    assert len(result["matches"]) == 100
    assert len(result["matches"][0]["text"]) == 500


def fake_server(tmp_path, behavior):
    """Initialize normally, then execute custom behavior for each next request."""
    path = tmp_path / "server.py"
    path.write_text('''import json, sys, time
for line in sys.stdin:
    req = json.loads(line)
    if req["method"] == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {
            "protocolVersion": "''' + PROTOCOL_VERSION + '''", "capabilities": {"tools": {}}}}), flush=True)
    elif "id" in req:
''' + "\n".join("        " + line for line in behavior.splitlines()) + "\n")
    return [sys.executable, str(path)]


@pytest.mark.parametrize("behavior,expected", [
    ("time.sleep(30)", "timed out"),
    ("sys.exit(0)", "disconnected"),
    ("print('not json', flush=True)", "Malformed"),
    ("print(json.dumps({'jsonrpc':'2.0','id':999,'result':{}}), flush=True)", "response id"),
])
def test_transport_failures_close_process(tmp_path, behavior, expected):
    with MCPClient(fake_server(tmp_path, behavior), timeout=0.3) as client:
        process = client.process
        result = client.call_tool("search_repo", {"query": "x"})
        assert result["error"]["code"] == "mcp_connection_error"
        assert expected in result["error"]["message"]
        assert process.poll() is not None
        assert client.process is None


def test_failed_initialization_cleans_up(tmp_path):
    client = MCPClient([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)
    with pytest.raises(MCPError, match="timed out"):
        with client:
            pass
    assert client.process is None


def test_missing_tool_rejected_at_setup(tmp_path):
    with search_client(tmp_path) as client:
        with pytest.raises(MCPError, match="not found"):
            client.tools({"missing"})


def test_paginated_discovery_and_schema_rejection(tmp_path):
    behavior = '''cursor = req.get('params', {}).get('cursor')
spec = {'name': 'first' if not cursor else 'second', 'inputSchema': {
    'type':'object','properties':{},'required':[],'additionalProperties':False}}
result = {'tools':[spec]}
if not cursor: result['nextCursor'] = 'page2'
print(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':result}), flush=True)'''
    with MCPClient(fake_server(tmp_path, behavior)) as client:
        assert [t.name for t in client.tools({"second"})] == ["mcp_second"]
    behavior = behavior.replace("'properties':{}", "'properties':{'count':{'type':'integer'}}")
    with MCPClient(fake_server(tmp_path, behavior)) as client:
        with pytest.raises(ValueError, match="schemas"):
            client.tools({"first"})


@pytest.mark.parametrize("fail", [False, True])
def test_cli_mcp_wiring_and_cleanup(tmp_path, monkeypatch, capsys, fail):
    from coding_agent import cli

    clients = []

    def client_factory(*args, **kwargs):
        client = MCPClient(*args, **kwargs)
        clients.append(client)
        return client

    class Model:
        def __init__(self, name):
            pass

        def respond(self, messages):
            assert "mcp_search_repo" in messages[0]["content"]
            if fail:
                raise RuntimeError("Model failed")
            return '{"action":"finish","args":{"summary":"Done"}}'

    monkeypatch.setattr(cli, "MCPClient", client_factory)
    monkeypatch.setattr(cli, "OllamaModel", Model)
    monkeypatch.setattr(sys, "argv", ["mini-coding-agent", "--workspace", str(tmp_path), "--mcp-search", "Inspect"])
    if fail:
        with pytest.raises(RuntimeError, match="Model failed"):
            cli.main()
    else:
        cli.main()
        assert json.loads(capsys.readouterr().out)["termination_reason"] == "finished"
    assert len(clients) == 1
    assert clients[0].process is None


def test_unsupported_protocol_version_rejected(tmp_path):
    command = fake_server(tmp_path, "pass")
    path = tmp_path / "server.py"
    path.write_text(path.read_text().replace(PROTOCOL_VERSION, "unsupported"))
    client = MCPClient(command)
    with pytest.raises(MCPError, match="unsupported MCP version"):
        with client:
            pass
    assert client.process is None
