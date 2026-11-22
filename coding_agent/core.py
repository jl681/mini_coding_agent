"""Bounded coding agent with explicit, workspace-scoped tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Protocol
import requests
from .tools import FunctionTool, ToolRegistry, string_arguments

SYSTEM = """You are a coding agent working in a small Python repository.
Respond with exactly one JSON object, no Markdown, using this format:
{"action":"tool_name","args":{}}
Available tools (arguments must match input_schema):
%s
To finish, respond with {"action":"finish","args":{"summary":"what changed"}}.
Read code before editing. Make the smallest change that fixes the issue. Run tests after edits.
Never assume that tests passing proves the task is solved.
"""

class Model(Protocol):
    def respond(self, messages: list[dict]) -> str: ...

class OllamaModel:
    def __init__(self, model: str = "qwen3:8b", url: str = "http://127.0.0.1:11434/api/chat"):
        self.model, self.url = model, url

    def respond(self, messages: list[dict]) -> str:
        response = requests.post(self.url, json={"model": self.model, "messages": messages,
            "stream": False, "format": "json", "options": {"temperature": 0}}, timeout=180)
        response.raise_for_status()
        return response.json()["message"]["content"]

class Workspace:
    def __init__(self, root: str | Path, timeout: int = 20):
        self.root = Path(root).resolve(strict=True)
        self.timeout = timeout

    def path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("Expected a relative workspace path")
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError("Path escapes workspace")
        return target

    def execute(self, action: str, args: dict) -> dict:
        if action == "list_files":
            return {"files": sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*.py")
                if p.is_file() and not any(x in p.parts for x in (".venv", "__pycache__", ".git")))[:100]}
        if action == "read_file":
            p = self.path(args["path"])
            if not p.is_file() or p.stat().st_size > 100_000:
                raise ValueError("File missing or too large")
            return {"path": args["path"], "content": p.read_text(encoding="utf-8")}
        if action == "write_file":
            p = self.path(args["path"])
            if p.suffix != ".py" or not p.is_file():
                raise ValueError("Only existing Python files may be edited")
            content = args["content"]
            if not isinstance(content, str) or len(content.encode()) > 100_000:
                raise ValueError("Invalid content")
            p.write_text(content, encoding="utf-8")
            return {"written": args["path"]}
        if action == "run_tests":
            # SECURITY: pytest executes repository code. Use only disposable trusted fixtures
            # or an externally isolated container/VM with no secrets or network access.
            try:
                proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=self.root,
                    capture_output=True, text=True, timeout=self.timeout,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                return {"exit_code": proc.returncode, "output": (proc.stdout + proc.stderr)[-8000:]}
            except subprocess.TimeoutExpired:
                return {"exit_code": -1, "output": "Test timeout"}
        raise ValueError(f"Unknown action: {action}")

def workspace_tools(workspace: Workspace) -> ToolRegistry:
    """Adapt workspace operations without exposing filesystem details to the loop."""
    definitions = [
        ("list_files", "List Python files in the workspace.", ()),
        ("read_file", "Read a workspace file (at most 100,000 bytes).", ("path",)),
        ("write_file", "Replace an existing Python file (at most 100,000 bytes).", ("path", "content")),
        ("run_tests", "Run pytest in the workspace with a timeout.", ()),
    ]
    return ToolRegistry([
        FunctionTool(name, description, string_arguments(*parameters),
                     lambda args, action=name: workspace.execute(action, args))
        for name, description, parameters in definitions
    ])


class CodingAgent:
    def __init__(self, model: Model, tools: ToolRegistry, max_steps: int = 12):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.model, self.tools, self.max_steps = model, tools, max_steps

    def run(self, task: str) -> dict:
        messages = [{"role": "system", "content": SYSTEM % self.tools.describe()},
                    {"role": "user", "content": task}]
        trace = []
        started = time.monotonic()
        summary = "Stopped: step limit reached"
        termination_reason = "step_limit"
        for step in range(self.max_steps):
            raw = self.model.respond(messages)
            args = {}
            try:
                decision = json.loads(raw)
                if not isinstance(decision, dict):
                    raise ValueError("Decision must be an object")
                action, args = decision["action"], decision.get("args", {})
                if not isinstance(action, str) or not isinstance(args, dict):
                    raise ValueError("action must be a string and args must be an object")
                if action == "finish":
                    if set(args) != {"summary"} or not isinstance(args["summary"], str):
                        raise ValueError("finish requires a string summary")
                    summary = args["summary"]
                    termination_reason = "finished"
                    trace.append({"step": step + 1, "action": action, "result": summary})
                    break
                observation = self.tools.execute(action, args)
            except (ValueError, KeyError, TypeError) as exc:
                action, args = "invalid_action", {}
                observation = {"error": {"code": "invalid_action", "message": str(exc)}}
            trace.append({"step": step + 1, "action": action, "args": args,
                          "result": observation})
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Tool observation: " + json.dumps(observation)})
        return {"summary": summary, "termination_reason": termination_reason,
                "steps": len(trace), "latency_seconds": round(time.monotonic() - started, 3), "trace": trace}
