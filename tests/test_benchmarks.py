"""Deterministic checks of benchmark scoring, isolation, and failure reporting."""
import json
import subprocess
import sys

import pytest
import requests

from benchmarks.evaluate import BASE, evaluate


class ScriptedModel:
    def __init__(self, actions):
        self.actions = iter(actions)

    def respond(self, messages):
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        return json.dumps(action)


FINISH = {"action": "finish", "args": {"summary": "Done"}}
REPAIRS = {
    "add_bug": ("calculator.py", "return a - b", "return a + b"),
    "order_total": ("pricing.py", "return price + quantity", "return price * quantity"),
    "average_empty": ("statistics_helper.py", "return sum(values) / len(values)",
                      "return sum(values) / len(values) if values else 0"),
}


def repair_model(task, run):
    path, old, new = REPAIRS[task.name]
    return ScriptedModel([
        {"action": "list_files", "args": {}},
        {"action": "read_file", "args": {"path": path}},
        {"action": "edit_file", "args": {"path": path, "old_text": old, "new_text": new}},
        FINISH,
    ])


def test_all_tasks_repaired_with_independent_grading_and_fresh_repeats():
    report = evaluate(model="scripted", runs=2, model_factory=repair_model)
    assert report["total_runs"] == 6
    assert report["success_rate"] == 1
    assert all(summary["success_rate"] == 1 for summary in report["per_task"].values())
    for result in report["results"]:
        assert result["outcome"] == "passed"
        assert result["test_exit_code"] == 0
        assert result["tool_calls"] == {"list_files": 1, "read_file": 1, "edit_file": 1}
        assert result["tool_errors"] == 0
        assert result["diff"]
        assert not any("hidden" in path for path in result["trace"][0]["result"]["files"])


def test_unchanged_tasks_fail_independent_checks():
    report = evaluate(runs=1, model_factory=lambda task, run: ScriptedModel([FINISH]))
    assert report["success_rate"] == 0
    assert all(r["outcome"] == "incorrect_repair" for r in report["results"])
    assert all(r["termination_reason"] == "finished" for r in report["results"])


def test_edge_case_visible_tests_initially_pass():
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                            cwd=BASE / "average_empty", capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_modifying_visible_tests_cannot_change_grade():
    model = ScriptedModel([
        {"action": "write_file", "args": {"path": "test_calculator.py", "content": "pass\n"}},
        FINISH,
    ])
    result = evaluate(runs=1, tasks=["add_bug"], model_factory=lambda task, run: model)["results"][0]
    assert not result["passed"]
    assert "test_positive" in result["test_output"]
    assert "test_calculator.py" in result["diff"]


@pytest.mark.parametrize("exception,reason", [
    (requests.ConnectionError("Ollama unavailable"), "model_error"),
    (RuntimeError("Unexpected failure"), "execution_error"),
])
def test_failure_recorded_with_partial_trace_and_remaining_runs_continue(exception, reason):
    def factory(task, run):
        if run == 1:
            return ScriptedModel([{"action": "list_files", "args": {}}, exception])
        return repair_model(task, run)

    report = evaluate(runs=2, tasks=["add_bug"], model_factory=factory)
    failure, success = report["results"]
    assert failure["outcome"] == reason
    assert failure["steps"] == 1
    assert failure["tool_calls"] == {"list_files": 1}
    assert failure["error"]
    assert failure["test_exit_code"] is None
    assert success["passed"]
    assert report["success_rate"] == 0.5


def test_step_limit_and_tool_error_reporting():
    result = evaluate(runs=1, tasks=["add_bug"], max_steps=1,
                      model_factory=lambda task, run: ScriptedModel([
                          {"action": "read_file", "args": {}}
                      ]))["results"][0]
    assert result["termination_reason"] == "step_limit"
    assert result["outcome"] == "step_limit"
    assert result["tool_errors"] == 1
    assert not result["passed"]


def test_evaluation_timeout_is_not_reported_as_incorrect_repair(monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("benchmarks.evaluate.subprocess.run", timeout)
    result = evaluate(runs=1, tasks=["add_bug"],
                      model_factory=lambda task, run: ScriptedModel([FINISH]))["results"][0]
    assert result["outcome"] == "evaluation_timeout"
    assert result["termination_reason"] == "finished"
    assert not result["passed"]


@pytest.mark.parametrize("options", [{"runs": 0}, {"max_steps": 0}, {"test_timeout": 0},
                                     {"tasks": []}, {"tasks": ["missing"]},
                                     {"tasks": ["add_bug", "add_bug"]}])
def test_invalid_configuration(options):
    with pytest.raises(ValueError):
        evaluate(**options)


def test_pytest_collection_error_is_an_evaluation_error(monkeypatch):
    def collection_error(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, "Collection failed", "")

    monkeypatch.setattr("benchmarks.evaluate.subprocess.run", collection_error)
    result = evaluate(runs=1, tasks=["add_bug"],
                      model_factory=lambda task, run: ScriptedModel([FINISH]))["results"][0]
    assert result["outcome"] == "evaluation_error"
    assert result["test_exit_code"] == 2
    assert not result["passed"]
