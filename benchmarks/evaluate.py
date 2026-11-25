"""Repeatable repair evaluation with independent, post-run regression tests."""
import argparse
from coding_agent.config import configure_cli_env, configure_cli_profiles
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
import difflib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import requests

from coding_agent.models import add_model_arguments, create_model, resolve_model_name, validate_settings
from coding_agent.mcp_client import MCPClient
from coding_agent.events import JsonlRecorder, NullRecorder
from coding_agent.core import CodingAgent, Model, Workspace, workspace_tools

BASE = Path(__file__).parent / "tasks"


@dataclass(frozen=True)
class Task:
    name: str
    instruction: str


TASKS = (
    Task("add_bug", "Fix calculator.add so it returns the sum of its inputs. Run tests."),
    Task("order_total", "Fix order_total: each (unit_price, quantity) line costs price times quantity, "
         "and an order costs the sum of its lines (zero for an empty order). "
         "Inspect orders.py and pricing.py, preserve both public functions, and run tests."),
    Task("average_empty", "Fix statistics_helper.average to return zero for an empty sequence "
         "while preserving arithmetic means for nonempty sequences. Run tests; "
         "the existing tests may not cover the bug."),
    Task("checkout_shipping", "Fix checkout so free shipping applies when the merchandise subtotal "
         "after discounts is at least 100. Below 100, shipping costs 5. Preserve existing "
         "discount behavior and public functions, and run tests."),
    Task("checkout_discovery", "Customers report incorrect delivery charges after applying coupons. "
         "Free shipping must use the merchandise subtotal after discounts: at least 100 ships free, "
         "otherwise shipping costs 5. Find and fix the purchase flow without changing existing "
         "discounts, subscription renewals, cart previews, or public functions. Run tests."),
)


def python_files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.py"))}


def changes(before: dict[str, bytes], after: dict[str, bytes]) -> str:
    chunks = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) != after.get(name):
            chunks.extend(difflib.unified_diff(
                before.get(name, b"").decode("utf-8", errors="replace").splitlines(keepends=True),
                after.get(name, b"").decode("utf-8", errors="replace").splitlines(keepends=True),
                fromfile=f"a/{name}", tofile=f"b/{name}"))
    return "".join(chunks)


def run_task(task: Task, run: int, model: Model, max_steps: int, test_timeout: float,
             mcp_search: bool = False, mcp_timeout: float = 10, recorder=None, profile=None, profile_hash=None,
             log_rejected_responses=False) -> dict:
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(BASE / task.name, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        before = python_files(repo)
        agent = None
        result = {"summary": "", "termination_reason": "execution_error"}
        error = None
        try:
            with ExitStack() as stack:
                tools = workspace_tools(Workspace(repo))
                if mcp_search:
                    client = stack.enter_context(MCPClient(
                        [sys.executable, "-m", "coding_agent.mcp_search_server", "--workspace", str(repo)],
                        timeout=mcp_timeout))
                    tools = tools.with_tools(client.tools({"search_repo"}, read_only={"search_repo"}))
                agent = CodingAgent(model, tools, max_steps=max_steps, recorder=recorder,
                                    event_context={"task": task.name, "trial": run, "mcp_search": mcp_search,
                                                   "profile": profile, "profile_hash": profile_hash},
                                    log_rejected_responses=log_rejected_responses)
                result = agent.run(task.instruction)
        except requests.RequestException as exc:
            result["termination_reason"] = "model_error"
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            # The benchmark boundary records a failed trial and continues other trials.
            error = f"{type(exc).__name__}: {exc}"
        trace = agent.trace if agent is not None else []
        after = python_files(repo)
        passed, output, exit_code = False, "", None
        outcome = result["termination_reason"]
        if error is None:
            # Grade in a separate fresh copy: agent edits to visible tests cannot
            # weaken the evaluator, and no hidden tests enter the agent workspace.
            grading = Path(tmp) / "grading"
            shutil.copytree(BASE / task.name, grading,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            for name in before:
                if not Path(name).name.startswith("test_"):
                    (grading / name).write_bytes(after[name])
            shutil.copytree(BASE / f"{task.name}_hidden", grading / "hidden_tests",
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            try:
                test = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                    cwd=grading, capture_output=True, text=True, timeout=test_timeout,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                exit_code = test.returncode
                passed = exit_code == 0
                output = (test.stdout + test.stderr)[-8000:]
                if exit_code not in (0, 1):
                    outcome = "evaluation_error"
                elif outcome == "finished":
                    outcome = "passed" if passed else "incorrect_repair"
            except subprocess.TimeoutExpired:
                outcome, output = "evaluation_timeout", "Independent tests exceeded timeout"
        tool_trace = [entry for entry in trace if entry["action"] not in {"finish", "invalid_action"}]
        return {
            "task": task.name, "run": run, "mcp_search": mcp_search, "passed": passed, "outcome": outcome,
            "termination_reason": result["termination_reason"], "summary": result["summary"],
            "run_id": agent.run_id if agent is not None else None,
            "steps": agent.steps if agent is not None else 0, "latency_seconds": round(time.monotonic() - started, 3),
            "tool_calls": dict(Counter(entry["action"] for entry in tool_trace)),
            "tool_errors": sum("error" in entry["result"] for entry in tool_trace),
            "invalid_actions": sum(entry["action"] == "invalid_action" for entry in trace),
            "test_exit_code": exit_code, "test_output": output, "error": error,
            "diff": changes(before, after), "trace": trace,
        }


def evaluate(model=None, runs=3, tasks=None, max_steps=12, test_timeout=30,
             model_factory: Callable[[Task, int], Model] | None = None,
             mcp_search: bool = False, mcp_timeout: float = 10, model_timeout: float = 180, think: bool | None = None,
             log_file: str | None = None, provider="ollama", base_url=None, api_key_env=None,
             max_output_tokens=None, json_mode=True, token_limit_field=None,
             profile=None, profile_hash=None, log_rejected_responses=False, native_tools=False):
    """Run each selected task `runs` times, using a fresh model and workspace."""
    if runs < 1 or max_steps < 1 or test_timeout <= 0 or mcp_timeout <= 0:
        raise ValueError("runs, max_steps, test_timeout, and mcp_timeout must be positive")
    if not math.isfinite(model_timeout) or model_timeout <= 0:
        raise ValueError("model_timeout must be finite and positive")
    if think is not None and not isinstance(think, bool):
        raise ValueError("think must be a boolean or None")
    validate_settings(model_timeout, max_output_tokens)
    if log_rejected_responses and not log_file:
        raise ValueError("log_rejected_responses requires log_file")
    selected = list(tasks) if tasks is not None else [task.name for task in TASKS]
    available = {task.name: task for task in TASKS}
    if not selected or len(selected) != len(set(selected)) or any(name not in available for name in selected):
        raise ValueError("Select one or more distinct known task names")
    model = resolve_model_name(provider, model)
    factory = model_factory or (lambda task, run: create_model(
        model, provider=provider, base_url=base_url, api_key_env=api_key_env, timeout=model_timeout,
        think=think, max_output_tokens=max_output_tokens, json_mode=json_mode, token_limit_field=token_limit_field, native_tools=native_tools))
    with ExitStack() as stack:
        recorder = stack.enter_context(JsonlRecorder(log_file)) if log_file else NullRecorder()
        results = [run_task(available[name], run, factory(available[name], run), max_steps, test_timeout,
                            mcp_search, mcp_timeout, recorder, profile, profile_hash, log_rejected_responses)
                   for name in selected for run in range(1, runs + 1)]
    per_task = {}
    for name in selected:
        trials = [result for result in results if result["task"] == name]
        per_task[name] = {"runs": runs, "success_rate": sum(r["passed"] for r in trials) / runs}
    return {"native_tools": native_tools, "log_rejected_responses": log_rejected_responses, "profile": profile, "profile_hash": profile_hash, "provider": provider, "model": model, "max_output_tokens": max_output_tokens,
            "json_mode": json_mode and not native_tools, "token_limit_field": token_limit_field, "runs": runs, "max_steps": max_steps, "test_timeout": test_timeout,
            "mcp_search": mcp_search, "mcp_timeout": mcp_timeout, "model_timeout": model_timeout, "think": think,
            "total_runs": len(results), "success_rate": sum(r["passed"] for r in results) / len(results),
            "per_task": per_task, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="Runs per task")
    parser.add_argument("--task", action="append", choices=[task.name for task in TASKS],
                        help="Select a task; repeat to select multiple (default: all)")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--test-timeout", type=float, default=30)
    parser.add_argument("--mcp-search", action=argparse.BooleanOptionalAction, default=False, help="Expose MCP search in each trial")
    parser.add_argument("--mcp-timeout", type=float, default=10)
    parser.add_argument("--output", default="benchmark-results.json")
    parser.add_argument("--log-file", help="Append events for all trials to a JSON Lines file")
    parser.add_argument("--log-rejected-responses", action="store_true",
                        help="Include bounded rejected response previews in --log-file")
    configure_cli_env(parser)
    add_model_arguments(parser)
    configure_cli_profiles(parser)
    args = parser.parse_args()
    try:
        data = evaluate(args.model, args.runs, args.task, args.max_steps, args.test_timeout,
                        mcp_search=args.mcp_search, mcp_timeout=args.mcp_timeout,
                        model_timeout=args.model_timeout, think=args.think, log_file=args.log_file,
                        provider=args.provider, base_url=args.base_url, api_key_env=args.api_key_env,
                        max_output_tokens=args.max_output_tokens, json_mode=args.json_mode,
                        token_limit_field=args.token_limit_field, profile=args.profile, profile_hash=args.profile_hash,
                        log_rejected_responses=args.log_rejected_responses, native_tools=args.native_tools)
    except ValueError as exc:
        parser.error(str(exc))
    Path(args.output).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
