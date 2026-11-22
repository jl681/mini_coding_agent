"""Read-only literal search over workspace Python files, exposed via MCP stdio."""
import argparse
import json
import os
from pathlib import Path
import sys

from .mcp_client import MAX_MESSAGE, PROTOCOL_VERSION
from .tools import string_arguments

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}


def search_repo(root: Path, query: str) -> dict:
    if not isinstance(query, str) or not query or len(query) > 1000:
        raise ValueError("query must contain 1 to 1000 characters")
    matches, scanned = [], 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix != ".py" or path.is_symlink() or not path.is_file():
                continue
            scanned += 1
            if scanned > 500:
                return {"matches": matches, "truncated": True}
            try:
                with path.open("rb") as file:
                    data = file.read(100_001)
                if len(data) > 100_000:
                    continue
                lines = data.decode("utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for number, line in enumerate(lines, 1):
                if query in line:
                    matches.append({"path": str(path.relative_to(root)), "line": number, "text": line[:500]})
                    if len(matches) == 100:
                        return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}


def serve(root: Path):
    initialized = False
    for line in sys.stdin.buffer:
        request_id = None
        try:
            if len(line) > MAX_MESSAGE:
                raise ValueError("Message too large")
            message = json.loads(line)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("Invalid JSON-RPC message")
            request_id = message.get("id")
            method = message.get("method")
            if method == "notifications/initialized":
                initialized = True
                continue
            if "id" not in message:
                continue
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "repository-search", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif not initialized:
                raise ValueError("Client has not initialized")
            elif method == "tools/list":
                result = {"tools": [{"name": "search_repo", "description":
                    "Search a literal string in workspace Python files. Returns paths, line numbers, and text; "
                    "read-only, at most 100 matches and 500 files, skipping files over 100,000 bytes.",
                    "inputSchema": string_arguments("query"), "annotations": {"readOnlyHint": True}}]}
            elif method == "tools/call":
                params = message.get("params", {})
                if params.get("name") != "search_repo":
                    raise ValueError("Unknown tool")
                try:
                    arguments = params.get("arguments", {})
                    if not isinstance(arguments, dict) or set(arguments) != {"query"}:
                        raise ValueError("Expected only a query argument")
                    data = search_repo(root, arguments["query"])
                    result = {"content": [{"type": "text", "text": json.dumps(data)}],
                              "structuredContent": data, "isError": False}
                except ValueError as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                response = {"jsonrpc": "2.0", "id": request_id,
                            "error": {"code": -32601, "message": "Method not found"}}
                print(json.dumps(response), flush=True)
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ValueError, TypeError, KeyError) as exc:
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32602, "message": str(exc)}}
        print(json.dumps(response), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve(strict=True)
    if not root.is_dir():
        parser.error("workspace must be a directory")
    serve(root)


if __name__ == "__main__":
    main()
