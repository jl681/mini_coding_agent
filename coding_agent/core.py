"""Bounded coding agent with explicit, workspace-scoped tools."""
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Protocol
import requests

SYSTEM = """You are a coding agent working in a small Python repository.
Respond with exactly one JSON object, no Markdown:
{"action":"list_files","args":{}}
{"action":"read_file","args":{"path":"relative/path.py"}}
{"action":"write_file","args":{"path":"relative/path.py","content":"entire replacement content"}}
{"action":"run_tests","args":{}}
{"action":"finish","args":{"summary":"what changed"}}
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
                proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=self.root,
                    capture_output=True, text=True, timeout=self.timeout,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                return {"exit_code": proc.returncode, "output": (proc.stdout + proc.stderr)[-8000:]}
            except subprocess.TimeoutExpired:
                return {"exit_code": -1, "output": "Test timeout"}
        raise ValueError(f"Unknown action: {action}")

class CodingAgent:
    def __init__(self, model: Model, workspace: Workspace, max_steps: int = 12):
        self.model, self.workspace, self.max_steps = model, workspace, max_steps

    def run(self, task: str) -> dict:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
        trace = []
        started = time.monotonic()
        summary = "Stopped: step limit reached"
        for step in range(self.max_steps):
            try:
                raw = self.model.respond(messages)
                decision = json.loads(raw)
                action, args = decision["action"], decision.get("args", {})
                if not isinstance(args, dict):
                    raise ValueError("args must be an object")
                if action == "finish":
                    summary = str(args.get("summary", "Finished"))
                    trace.append({"step": step + 1, "action": action, "result": summary})
                    break
                observation = self.workspace.execute(action, args)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                action = "invalid_action"
                observation = {"error": str(exc)}
            trace.append({"step": step + 1, "action": action, "args": args if action != "invalid_action" else {},
                          "result": observation})
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Tool observation: " + json.dumps(observation)})
        return {"summary": summary, "steps": len(trace), "latency_seconds": round(time.monotonic() - started, 3), "trace": trace}
