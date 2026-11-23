"""Bounded coding agent with explicit, workspace-scoped tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Protocol
from urllib.parse import urlsplit
import math
import requests
from .tools import FunctionTool, ToolRegistry, string_arguments

SYSTEM = """You are a coding agent working in a small Python repository.
Respond with exactly one JSON object, no Markdown, using this format:
{"action":"tool_name","args":{}}
Available tools (arguments must match input_schema):
%s
To finish, respond with {"action":"finish","args":{"summary":"what changed"}}.
Use workspace-relative paths exactly as returned by tools (for example, "module.py" or "src/module.py").
Never prefix paths with "/", "/workspace", or the workspace directory.
Investigate before editing: read existing code and trace the relevant entry point, callers, and values passed between functions.
Do not assume the helper named in the symptom is faulty; check whether its callers supply the correct inputs.
When search is available, use it to locate relevant definitions and call sites. For literal search, use one exact substring;
if no matches are returned, try a shorter identifier rather than assuming the code is absent.
Derive a concrete input and expected result from the user's requirements. Reproduce the reported behavior before editing when possible.
If the visible tests miss that behavior, add a focused regression to an existing test file when permitted and confirm it fails before the fix.
Read the target file before editing; do not guess existing file content. Make the smallest change that repairs the active execution path.
Do not add an unused wrapper as a substitute for changing the behavior actually called. Preserve public interfaces and unrelated behavior.
Run tests after editing and verify the concrete reported case, including relevant boundaries.
Passing pre-existing tests alone is insufficient, especially if they passed before the fix.
Before finishing, distinguish observed test results from assumptions; state any verification you could not perform.
"""

class Model(Protocol):
    def respond(self, messages: list[dict]) -> str: ...

class OllamaModel:
    def __init__(self, model: str = "qwen3:8b", url: str = "http://127.0.0.1:11434/api/chat",
                 timeout: float = 180, think: bool | None = None):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("model timeout must be finite and positive")
        self.model, self.url, self.timeout = model, url, timeout
        if think is not None and not isinstance(think, bool):
            raise ValueError("think must be a boolean or None")
        self.think = think

    def respond(self, messages: list[dict]) -> str:
        payload = {"model": self.model, "messages": messages,
                   "stream": False, "format": "json", "options": {"temperature": 0}}
        if self.think is not None:
            payload["think"] = self.think
        # Local model traffic must not be routed through environment proxies.
        with requests.Session() as session:
            if urlsplit(self.url).hostname in {"localhost", "127.0.0.1", "::1"}:
                session.trust_env = False
            try:
                response = session.post(self.url, json=payload,
                    timeout=(5, self.timeout))
            except requests.ReadTimeout as exc:
                raise requests.ReadTimeout(
                    f"Ollama did not return a response within {self.timeout:g}s; "
                    "increase --model-timeout or check Ollama load and generation speed"
                ) from exc
            response.raise_for_status()
            return response.json()["message"]["content"]

class Workspace:
    def __init__(self, root: str | Path, timeout: int = 20):
        self.root = Path(root).resolve(strict=True)
        self.timeout = timeout

    def path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("Expected a relative workspace path, e.g. calculator.py; use paths from list_files or search, without a leading slash")
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
        if action == "edit_file":
            p = self.path(args["path"])
            if p.suffix != ".py" or not p.is_file():
                raise ValueError("Only existing Python files may be edited")
            old_text, new_text = args["old_text"], args["new_text"]
            if not isinstance(old_text, str) or not old_text:
                raise ValueError("old_text must be a non-empty string")
            if not isinstance(new_text, str):
                raise ValueError("new_text must be a string")
            if p.stat().st_size > 100_000:
                raise ValueError("File too large (maximum 100,000 bytes)")
            # Decode bytes directly so unrelated line endings remain unchanged.
            content = p.read_bytes().decode("utf-8")
            if len(content.encode("utf-8")) > 100_000:
                raise ValueError("File too large (maximum 100,000 bytes)")
            start = content.find(old_text)
            if start == -1:
                raise ValueError("old_text not found; reread the file and use an exact match")
            # Include overlapping occurrences when checking ambiguity.
            if content.find(old_text, start + 1) != -1:
                raise ValueError("old_text matches multiple locations; include more surrounding context")
            updated = content[:start] + new_text + content[start + len(old_text):]
            encoded = updated.encode("utf-8")
            if len(encoded) > 100_000:
                raise ValueError("Edited file too large (maximum 100,000 bytes)")
            p.write_bytes(encoded)
            return {"edited": args["path"], "replacements": 1}
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
        ("read_file", "Read a workspace-relative path, e.g. calculator.py (no leading slash; at most 100,000 bytes).", ("path",)),
        ("write_file", "Replace an existing Python file using a workspace-relative path (no leading slash; at most 100,000 bytes).", ("path", "content")),
        ("edit_file", "Preferred for localized edits: replace one exact, non-empty, unique passage in an existing Python file. "
         "path must be workspace-relative with no leading slash. Use read_file first; on a mismatch reread it, and on ambiguity include more context. "
         "Input and resulting files must be at most 100,000 bytes.", ("path", "old_text", "new_text")),
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
        self.trace: list[dict] = []

    def run(self, task: str) -> dict:
        messages = [{"role": "system", "content": SYSTEM % self.tools.describe()},
                    {"role": "user", "content": task}]
        self.trace = []
        trace = self.trace
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
