"""Bounded coding agent with explicit, workspace-scoped tools."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import hashlib
import uuid
from .events import EventRecorder, NullRecorder
# Keep the original imports available to callers while implementations live separately.
from .models import Model, ModelResponse, OllamaModel
from .actions import AgentAction, strict_json_loads
from .tools import FunctionTool, ToolRegistry, string_arguments

SYSTEM = """You are a coding agent working in a small Python repository.
Respond with exactly one JSON object, no Markdown, using this format:
Never concatenate multiple action objects or append explanations after the object. Choose one action and wait for its observation.
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

NATIVE_SYSTEM = (
    "You are a coding agent working in a small Python repository. "
    "You may batch up to 8 explicitly read-only functions per response; they execute sequentially. "
    "Call any write, test execution, or finish individually. Wait for all results before deciding again. "
    "Do not encode actions in ordinary answer text. Use finish to end the run with a summary.\n"
    + SYSTEM[SYSTEM.index("Use workspace-relative paths"):]
)

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
                     lambda args, action=name: workspace.execute(action, args),
                     read_only=name in {"list_files", "read_file"})
        for name, description, parameters in definitions
    ])


class CodingAgent:
    def __init__(self, model: Model, tools: ToolRegistry, max_steps: int = 12,
                 recorder: EventRecorder | None = None, event_context: dict | None = None,
                 log_rejected_responses: bool = False):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.model, self.tools, self.max_steps = model, tools, max_steps
        self.trace: list[dict] = []
        self.steps = 0
        self.run_id: str | None = None
        self.recorder = recorder if recorder is not None else NullRecorder()
        self.event_context = dict(event_context or {})
        self.log_rejected_responses = log_rejected_responses

    def run(self, task: str) -> dict:
        self.trace = []
        self.steps = 0
        run_id = uuid.uuid4().hex
        self.run_id = run_id
        started = time.monotonic()
        native = getattr(self.model, "native_tools", False)
        definitions = self.tools.specifications() + [{"name": "finish", "description": "End the run with a summary of changes and verification.",
                                                    "input_schema": string_arguments("summary")}]
        prompt = NATIVE_SYSTEM if native else SYSTEM % self.tools.describe()

        def emit(event, **fields):
            self.recorder.emit({**self.event_context, "run_id": run_id, "event": event, **fields})

        emit("run_started", provider=getattr(self.model, "provider", "custom"),
             max_output_tokens=getattr(self.model, "max_output_tokens", None),
             json_mode=getattr(self.model, "json_mode", None),
             token_limit_field=getattr(self.model, "token_limit_field", None), model=getattr(self.model, "model", type(self.model).__name__),
             think=getattr(self.model, "think", None), model_timeout=getattr(self.model, "timeout", None),
             max_steps=self.max_steps, native_tools=native, log_rejected_responses=self.log_rejected_responses, prompt_sha256=hashlib.sha256((prompt + (json.dumps(definitions, sort_keys=True) if native else "")).encode()).hexdigest())
        try:
            result = self._run(task, prompt, emit, definitions if native else None)
        except BaseException as exc:
            emit("run_terminated", termination_reason="interrupted" if isinstance(exc, KeyboardInterrupt) else "error",
                 error_type=type(exc).__name__, error=str(exc), steps=self.steps,
                 elapsed_seconds=round(time.monotonic() - started, 6))
            raise
        emit("run_terminated", termination_reason=result["termination_reason"], steps=result["steps"],
             elapsed_seconds=round(time.monotonic() - started, 6))
        return {**result, "run_id": run_id}

    def _run(self, task: str, prompt: str, emit, definitions=None) -> dict:
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": task}]
        trace = self.trace
        started = time.monotonic()
        summary = "Stopped: step limit reached"
        termination_reason = "step_limit"
        for step in range(1, self.max_steps + 1):
            emit("model_request_started", step=step)
            request_started = time.monotonic()
            try:
                response = self.model.respond(messages, tools=definitions) if definitions is not None else self.model.respond(messages)
            except BaseException as exc:
                emit("model_request_failed", step=step, error_type=type(exc).__name__, error=str(exc),
                     elapsed_seconds=round(time.monotonic() - request_started, 6))
                raise
            self.steps = step
            emit("model_request_completed", step=step, metadata=response.metadata,
                 elapsed_seconds=round(time.monotonic() - request_started, 6))
            raw = response.content
            args = {}
            batch = ()
            try:
                if response.metadata.get("done_reason") == "length":
                    emit("model_output_truncated", step=step)
                    raise ValueError("Model output reached its token limit; no action was executed")
                if definitions is not None:
                    if response.action_error:
                        raise ValueError(response.action_error)
                    if len(response.actions) > 1:
                        batch = response.actions
                        self.tools.validate_read_batch(batch)
                        requested = batch[0]
                    else:
                        if response.action is None:
                            raise ValueError("A native function call is required")
                        requested = response.action
                else:
                    decision = strict_json_loads(raw)
                    if not isinstance(decision, dict):
                        raise ValueError("Decision must be an object")
                    if set(decision) - {"action", "args"}:
                        raise ValueError("Only action and args are allowed in the response object")
                    requested = AgentAction(decision["action"], decision.get("args", {}))
                action, args = requested.name, requested.arguments
                if not isinstance(action, str) or not isinstance(args, dict):
                    raise ValueError("action must be a string and args must be an object")
                if action == "finish":
                    if set(args) != {"summary"} or not isinstance(args["summary"], str):
                        raise ValueError("finish requires a string summary")
                    summary = args["summary"]
                    termination_reason = "finished"
                    trace.append({"step": step, "action": action, "result": summary})
                    break
            except (ValueError, KeyError, TypeError) as exc:
                action, args = "invalid_action", {}
                recovery = (
                    "No action from this response was executed. Return exactly ONE JSON object with "
                    "action and args. Do not return multiple objects, Markdown, or any text before or "
                    "after the object. Select only the next action and wait for its observation; "
                    "do not repeat actions already completed successfully."
                )
                if definitions is not None:
                    recovery = ("No function from this response was executed. Call one provided function, or at most "
                                "8 explicitly read-only functions, with valid JSON object arguments. "
                                "Call writes, tests, and finish individually. Use finish with a summary when done. "
                                "Do not repeat actions already completed successfully.")
                observation = {"error": {"code": "invalid_action", "message": str(exc), "recovery": recovery}}
                details = {}
                if self.log_rejected_responses:
                    details = {"response_preview": raw[:1000], "preview_truncated": len(raw) > 1000}
                    if isinstance(exc, json.JSONDecodeError):
                        details.update(error_offset=exc.pos,
                                       error_context=raw[max(0, exc.pos - 80):exc.pos + 240])
                if definitions is not None:
                    details.update(call_ids=list(response.tool_call_ids),
                                   requested_tools=[item.name for item in response.actions])
                emit("action_rejected", step=step, result=observation, **details)
            else:
                if batch:
                    observations = []
                    emit("batch_started", step=step, call_count=len(batch))
                    for index, item in enumerate(batch):
                        result = self._execute_tool(item.name, item.arguments, step, emit,
                                                    call_id=item.call_id, batch_index=index)
                        observations.append(result)
                        trace.append({"step": step, "action": item.name, "args": item.arguments,
                                      "call_id": item.call_id, "batch_index": index, "result": result})
                    emit("batch_completed", step=step, call_count=len(batch))
                    messages.append(response.assistant_message)
                    for item, result in zip(batch, observations):
                        messages.append({"role": "tool", "tool_call_id": item.call_id,
                                         "content": json.dumps(result)})
                    continue
                observation = self._execute_tool(action, args, step, emit,
                                                 **({"call_id": requested.call_id} if definitions is not None else {}))
            trace.append({"step": step, "action": action, "args": args, "result": observation})
            if definitions is not None:
                messages.append(response.assistant_message or {"role": "assistant", "content": ""})
                if response.tool_call_ids:
                    for call_id in response.tool_call_ids:
                        messages.append({"role": "tool", "tool_call_id": call_id,
                                         "content": json.dumps(observation)})
                else:
                    messages.append({"role": "user", "content": json.dumps(observation)})
            else:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "Tool observation: " + json.dumps(observation)})
        return {"summary": summary, "termination_reason": termination_reason,
                "steps": self.steps, "tool_calls": sum(t["action"] not in {"finish", "invalid_action"} for t in trace),
                "latency_seconds": round(time.monotonic() - started, 3), "trace": trace}


    def _execute_tool(self, action, args, step, emit, **context):
        emit("tool_started", step=step, tool=action, arguments=args, **context)
        started = time.monotonic()
        try:
            observation = self.tools.execute(action, args)
        except BaseException as exc:
            emit("tool_failed", step=step, tool=action, error_type=type(exc).__name__, error=str(exc),
                 elapsed_seconds=round(time.monotonic() - started, 6), **context)
            raise
        emit("tool_completed", step=step, tool=action, result=observation,
             is_error="error" in observation, elapsed_seconds=round(time.monotonic() - started, 6), **context)
        return observation
