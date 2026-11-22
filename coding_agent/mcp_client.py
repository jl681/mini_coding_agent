"""Small synchronous MCP stdio client (2025-11-25), not a general SDK.

One request at a time; closed string-object tool schemas only. No HTTP,
resources, prompts, sampling, or automatic reconnection.
"""
import json
import os
import selectors
import subprocess
import time

from .tools import FunctionTool, ToolRegistry

PROTOCOL_VERSION = "2025-11-25"
MAX_MESSAGE = 1_000_000


class MCPError(ValueError):
    pass


class MCPClient:
    def __init__(self, command: list[str], timeout: float = 10):
        if not command or timeout <= 0:
            raise ValueError("command and a positive timeout are required")
        self.command, self.timeout = command, timeout
        self.process = None
        self._buffer = b""
        self._request_id = 0

    def __enter__(self):
        if self.process is not None:
            raise MCPError("Client is already open")
        self._buffer = b""
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.process.stdin.fileno(), False)
        try:
            result = self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "mini-coding-agent", "version": "0.1.0"}})
            if result.get("protocolVersion") != PROTOCOL_VERSION:
                raise MCPError("Server negotiated an unsupported MCP version")
            if "tools" not in result.get("capabilities", {}):
                raise MCPError("Server does not advertise tools")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except BaseException:
            self.close()
            raise

    def _send(self, message):
        if self.process is None or self.process.poll() is not None:
            raise MCPError("MCP server is not running")
        payload = json.dumps(message).encode("utf-8") + b"\n"
        if len(payload) > MAX_MESSAGE:
            raise MCPError("MCP request exceeds message limit")
        try:
            deadline = time.monotonic() + self.timeout
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin, selectors.EVENT_WRITE)
                offset = 0
                while offset < len(payload):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise MCPError("MCP write timed out")
                    try:
                        offset += os.write(self.process.stdin.fileno(), payload[offset:])
                    except BlockingIOError:
                        continue
        except OSError as exc:
            raise MCPError(f"MCP write failed: {exc}") from exc

    def _receive(self, deadline):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise MCPError("MCP request timed out")
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise MCPError("MCP server disconnected")
                self._buffer += chunk
                if len(self._buffer) > MAX_MESSAGE:
                    raise MCPError("MCP response exceeds message limit")
            line, self._buffer = self._buffer.split(b"\n", 1)
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise MCPError("Malformed MCP JSON response") from exc
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise MCPError("Invalid MCP JSON-RPC response")
        return message

    def request(self, method: str, params: dict) -> dict:
        self._request_id += 1
        request_id = self._request_id
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            deadline = time.monotonic() + self.timeout
            while True:
                if time.monotonic() >= deadline:
                    raise MCPError("MCP request timed out")
                message = self._receive(deadline)
                if "method" in message:
                    if "id" in message:
                        reply = {"jsonrpc": "2.0", "id": message["id"]}
                        if message["method"] == "ping":
                            reply["result"] = {}
                        else:
                            reply["error"] = {"code": -32601, "message": "Unsupported client request"}
                        self._send(reply)
                    continue
                if message.get("id") != request_id:
                    raise MCPError("Unexpected MCP response id")
                if "error" in message:
                    raise MCPError(f"MCP protocol error: {message['error']}")
                if not isinstance(message.get("result"), dict):
                    raise MCPError("MCP result must be an object")
                return message["result"]
        except (MCPError, OSError):
            # A timed-out or broken stream must not contaminate later calls.
            self.close()
            raise

    def tools(self, allowed: set[str]) -> list[FunctionTool]:
        discovered, cursors = {}, set()
        params = {}
        while True:
            page = self.request("tools/list", params)
            for spec in page.get("tools", []):
                name = spec["name"]
                if name in discovered:
                    raise MCPError(f"Duplicate MCP tool: {name}")
                discovered[name] = spec
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in cursors or len(cursors) >= 100:
                raise MCPError("Invalid or excessive MCP tool pagination")
            cursors.add(cursor)
            params = {"cursor": cursor}
        if allowed - discovered.keys():
            raise MCPError(f"Requested MCP tools not found: {sorted(allowed - discovered.keys())}")
        tools = [FunctionTool(
            "mcp_" + name, discovered[name].get("description", ""), discovered[name]["inputSchema"],
            lambda args, tool_name=name: self.call_tool(tool_name, args)) for name in sorted(allowed)]
        # Fail at setup rather than advertise a schema we cannot validate.
        ToolRegistry(tools)
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        try:
            result = self.request("tools/call", {"name": name, "arguments": arguments})
        except (MCPError, OSError) as exc:
            return {"error": {"code": "mcp_connection_error", "message": str(exc)}}
        if result.get("isError"):
            return {"error": {"code": "mcp_tool_error", "message": result.get("content", [])}}
        return {key: result[key] for key in ("content", "structuredContent") if key in result}

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        process.stdin.close()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()

    def __exit__(self, *exc):
        self.close()
