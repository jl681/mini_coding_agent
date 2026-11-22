"""Repeatable repair evaluation with independent, post-run regression tests."""
import argparse
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import requests

from coding_agent.core import CodingAgent, Model, OllamaModel, Workspace, workspace_tools

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


def run_task(task: Task, run: int, model: Model, max_steps: int, test_timeout: float) -> dict:
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(BASE / task.name, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        before = python_files(repo)
        agent = CodingAgent(model, workspace_tools(Workspace(repo)), max_steps=max_steps)
        result = {"summary": "", "termination_reason": "execution_error"}
        error = None
        try:
            result = agent.run(task.instruction)
        except requests.RequestException as exc:
            result["termination_reason"] = "model_error"
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            # The benchmark boundary records a failed trial and continues other trials.
            error = f"{type(exc).__name__}: {exc}"
        trace = agent.trace
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
            "task": task.name, "run": run, "passed": passed, "outcome": outcome,
            "termination_reason": result["termination_reason"], "summary": result["summary"],
            "steps": len(trace), "latency_seconds": round(time.monotonic() - started, 3),
            "tool_calls": dict(Counter(entry["action"] for entry in tool_trace)),
            "tool_errors": sum("error" in entry["result"] for entry in tool_trace),
            "invalid_actions": sum(entry["action"] == "invalid_action" for entry in trace),
            "test_exit_code": exit_code, "test_output": output, "error": error,
            "diff": changes(before, after), "trace": trace,
        }


def evaluate(model="qwen3:8b", runs=3, tasks=None, max_steps=12, test_timeout=30,
             model_factory: Callable[[Task, int], Model] | None = None):
    """Run each selected task `runs` times, using a fresh model and workspace."""
    if runs < 1 or max_steps < 1 or test_timeout <= 0:
        raise ValueError("runs, max_steps, and test_timeout must be positive")
    selected = list(tasks) if tasks is not None else [task.name for task in TASKS]
    available = {task.name: task for task in TASKS}
    if not selected or len(selected) != len(set(selected)) or any(name not in available for name in selected):
        raise ValueError("Select one or more distinct known task names")
    factory = model_factory or (lambda task, run: OllamaModel(model))
    results = [run_task(available[name], run, factory(available[name], run), max_steps, test_timeout)
               for name in selected for run in range(1, runs + 1)]
    per_task = {}
    for name in selected:
        trials = [result for result in results if result["task"] == name]
        per_task[name] = {"runs": runs, "success_rate": sum(r["passed"] for r in trials) / runs}
    return {"model": model, "runs": runs, "max_steps": max_steps, "test_timeout": test_timeout,
            "total_runs": len(results), "success_rate": sum(r["passed"] for r in results) / len(results),
            "per_task": per_task, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--runs", type=int, default=3, help="Runs per task")
    parser.add_argument("--task", action="append", choices=[task.name for task in TASKS],
                        help="Select a task; repeat to select multiple (default: all)")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--test-timeout", type=float, default=30)
    parser.add_argument("--output", default="benchmark-results.json")
    args = parser.parse_args()
    try:
        data = evaluate(args.model, args.runs, args.task, args.max_steps, args.test_timeout)
    except ValueError as exc:
        parser.error(str(exc))
    Path(args.output).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
