"""Deterministic checks of benchmark scoring, isolation, and failure reporting."""
import json
from coding_agent.core import ModelResponse
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
        return ModelResponse(json.dumps(action))


FINISH = {"action": "finish", "args": {"summary": "Done"}}
REPAIRS = {
    "checkout_discovery": ("policies/order_quote.py", "calculate_shipping(subtotal)", "calculate_shipping(merchandise_total)"),
    "checkout_shipping": ("checkout.py", "calculate_shipping(subtotal)", "calculate_shipping(merchandise_total)"),
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
    assert report["total_runs"] == 10
    assert report["success_rate"] == 1
    assert all(summary["success_rate"] == 1 for summary in report["per_task"].values())
    for result in report["results"]:
        assert result["outcome"] == "passed"
        assert result["test_exit_code"] == 0
        assert result["tool_calls"] == {"list_files": 1, "read_file": 1, "edit_file": 1}
        assert result["tool_errors"] == 0
        assert result["diff"]
        assert not any("hidden" in path for path in result["trace"][0]["result"]["files"])


@pytest.mark.parametrize("enabled", [False, True])
def test_unchanged_tasks_fail_independent_checks(enabled):
    report = evaluate(runs=1, mcp_search=enabled, model_factory=lambda task, run: ScriptedModel([FINISH]))
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
                                     {"mcp_timeout": 0}, {"tasks": []}, {"tasks": ["missing"]},
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


@pytest.mark.parametrize("enabled", [False, True])
def test_checkout_grading_matches_with_and_without_mcp(enabled):
    def factory(task, run):
        path, old, new = REPAIRS[task.name]
        actions = ([{"action": "mcp_search_repo", "args": {"query": "shipping"}}] if enabled else [])
        actions += [
            {"action": "edit_file", "args": {"path": path, "old_text": old, "new_text": new}}, FINISH]
        return ScriptedModel(actions)

    report = evaluate(runs=1, tasks=["checkout_shipping"], mcp_search=enabled, model_factory=factory)
    result = report["results"][0]
    assert report["mcp_search"] is enabled
    assert result["mcp_search"] is enabled
    assert result["passed"]
    assert result["test_exit_code"] == 0
    assert result["tool_errors"] == 0
    if enabled:
        matches = result["trace"][0]["result"]["structuredContent"]["matches"]
        assert any(match["path"] == "checkout.py" for match in matches)
        assert all("hidden" not in match["path"] for match in matches)
    else:
        assert "mcp_search_repo" not in result["tool_calls"]


def test_checkout_visible_tests_pass_before_repair():
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                            cwd=BASE / "checkout_shipping", capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_each_mcp_trial_gets_fresh_server_and_cleans_up_on_failure(monkeypatch):
    from benchmarks import evaluate as module
    from coding_agent.mcp_client import MCPClient

    processes, roots = [], []

    class TrackingClient(MCPClient):
        def __enter__(self):
            result = super().__enter__()
            processes.append(self.process)
            roots.append(self.command[-1])
            return result

    monkeypatch.setattr(module, "MCPClient", TrackingClient)

    def factory(task, run):
        if run == 1:
            return ScriptedModel([requests.ConnectionError("Model unavailable")])
        return repair_model(task, run)

    report = evaluate(runs=2, tasks=["checkout_shipping"], mcp_search=True, model_factory=factory)
    assert report["results"][0]["outcome"] == "model_error"
    assert report["results"][1]["passed"]
    assert len(processes) == 2
    assert roots[0] != roots[1]
    assert all(process.poll() is not None for process in processes)


def test_mcp_startup_failure_does_not_abort_remaining_trials(monkeypatch):
    from benchmarks import evaluate as module

    class FailedClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise OSError("Server unavailable")

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(module, "MCPClient", FailedClient)
    report = evaluate(runs=2, tasks=["checkout_shipping"], mcp_search=True, model_factory=repair_model)
    assert len(report["results"]) == 2
    for result in report["results"]:
        assert result["outcome"] == "execution_error"
        assert result["steps"] == 0
        assert "Server unavailable" in result["error"]


def test_benchmark_cli_forwards_mcp_options(tmp_path, monkeypatch, capsys):
    from benchmarks import evaluate as module

    captured = {}

    def fake_evaluate(*args, **kwargs):
        captured.update(kwargs)
        return {"mcp_search": kwargs["mcp_search"], "results": []}

    output = tmp_path / "report.json"
    monkeypatch.setattr(module, "evaluate", fake_evaluate)
    monkeypatch.setattr(sys, "argv", ["evaluate", "--task", "checkout_shipping", "--mcp-search",
                                      "--mcp-timeout", "2.5", "--output", str(output)])
    module.main()
    assert captured == {"mcp_search": True, "mcp_timeout": 2.5, "model_timeout": 180, "think": None, "log_file": None,
                        "provider": "ollama", "base_url": None, "api_key_env": None,
                        "max_output_tokens": None, "json_mode": True, "token_limit_field": None,
                        "profile": None, "profile_hash": None, "log_rejected_responses": False, "native_tools": False}
    assert json.loads(output.read_text())["mcp_search"] is True
    assert json.loads(capsys.readouterr().out)["mcp_search"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_discovery_task_supports_nested_edits_and_search(enabled):
    class DiscoveryModel:
        def __init__(self):
            path, old, new = REPAIRS["checkout_discovery"]
            self.actions = iter(([
                {"action": "mcp_search_repo", "args": {"query": "calculate_shipping"}},
            ] if enabled else [
                {"action": "read_file", "args": {"path": "checkout.py"}},
                {"action": "read_file", "args": {"path": "storefront/service.py"}},
            ]) + [
                {"action": "read_file", "args": {"path": path}},
                {"action": "edit_file", "args": {"path": path, "old_text": old, "new_text": new}},
                FINISH,
            ])

        def respond(self, messages):
            if enabled and len(messages) == 4:
                observation = json.loads(messages[-1]["content"].removeprefix("Tool observation: "))
                paths = {match["path"] for match in observation["structuredContent"]["matches"]}
                assert "policies/order_quote.py" in paths
                assert "policies/subscription_quote.py" in paths
                assert "storefront/preview.py" in paths
                assert not any("hidden" in path for path in paths)
            return ModelResponse(json.dumps(next(self.actions)))

    report = evaluate(runs=1, tasks=["checkout_discovery"], mcp_search=enabled,
                      model_factory=lambda task, run: DiscoveryModel())
    assert report["success_rate"] == 1
    assert "policies/order_quote.py" in report["results"][0]["diff"]


def test_discovery_visible_tests_pass_before_repair():
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                            cwd=BASE / "checkout_discovery", capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_model_timeout_forwarded_and_recorded(monkeypatch):
    from benchmarks import evaluate as module

    settings = []

    def model_factory(name, timeout, think, **kwargs):
        settings.append((name, timeout, think))
        return ScriptedModel([FINISH])

    monkeypatch.setattr(module, "create_model", model_factory)
    report = evaluate(model="qwen3:8b", runs=1, tasks=["add_bug"], model_timeout=600)
    assert settings == [("qwen3:8b", 600, None)]
    assert report["model_timeout"] == 600
