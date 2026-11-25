# Evaluation Guide

[Back to README](../README.md)

## Tasks and execution

`mini-coding-agent-evaluate` accepts the same profiles and model options as the
agent CLI. It owns workspace creation; do not pass `--workspace`. `--runs` is the
repetition count per task (default 3), and `--task` can be repeated. Without a task
selection, all five tasks run.

| Task | Behavior under test |
| --- | --- |
| `add_bug` | Addition with negative, zero, and fractional inputs |
| `order_total` | Line pricing across an aggregation module and its helper |
| `average_empty` | Empty-input behavior missed by passing visible tests |
| `checkout_shipping` | Shipping eligibility after discounts |
| `checkout_discovery` | Nested purchase-flow investigation, preserving previews and renewals |

Each trial creates a fresh workspace and model instance. When enabled, the MCP
search server is scoped to that workspace and closed before grading. Startup and
agent failures count as unsuccessful trials; remaining trials continue. Provider
configuration errors can abort setup before trials begin.

## Independent grading

After the agent stops, the evaluator prepares another fresh copy with original
visible tests and hidden regressions. It transfers the agent's source changes;
visible test edits remain in the reported diff but do not weaken grading. The
agent never receives the grading workspace through its tools. This prevents
accidental test weakening, not hostile repository-code attacks: execution is
still local and not sandboxed.

The evaluator currently transfers existing non-test Python files from the fixture;
it is designed for the repository's small repair tasks, not arbitrary project
layouts, new files, or general-purpose patch evaluation.

## Reports and interpretation

The console prints a summary; `--output` saves the full JSON report (default
`benchmark-results.json`). That file is overwritten. Each trial includes a run
ID, action trace, Python-file diff, test output, elapsed time, tool counts, errors,
and termination reason. A profile name/hash identifies selected configuration
before CLI overrides; actual budgets and model settings are also recorded.

| Field or outcome | Interpretation |
| --- | --- |
| `passed` | Independent grading returned exit code zero |
| `success_rate` | Passed trials divided by all attempted trials, including model failures |
| `finished` termination | The agent explicitly called `finish`; correctness is separate |
| `step_limit` termination | Completed model-response turns exhausted the budget |
| `incorrect_repair` | Agent finished, but independent tests failed |
| `model_error` | Model transport or unusable provider response failed |
| `execution_error` | Agent/tool runtime or MCP startup failed |
| `evaluation_error` | Independent pytest returned a non-test-failure error code |
| `evaluation_timeout` | Independent tests exceeded `--test-timeout` |

Require both `passed: true` and `termination_reason: finished` when reporting
normal successful completion. A step-limited run can still have a correct patch.
Agent-visible test output and independent grading output are distinct.

`steps` counts completed model-response turns, not trace entries. A native
read-only batch generates multiple tool entries with the same step and distinct
call IDs. `tool_calls` maps tool names to individual call counts; `invalid_actions`
counts rejected response turns. An expected error returned by a tool contributes
to `tool_errors`; a failing pytest result is an observation with nonzero
`exit_code`, not necessarily a tool-protocol error.

Latency includes workspace/setup, model calls, tool execution, MCP cleanup, and
grading. Ollama metadata durations in event logs are nanoseconds; event elapsed
times are seconds. Model, MCP, and independent-test timeouts are separate; none
is a whole-trial wall-clock limit. The agent's local test tool uses its own
20-second timeout, which `--test-timeout` does not change.

## Compare configurations

Keep task selection, model, generation settings, repetitions, and step budget
fixed when testing MCP availability:

```bash
mini-coding-agent-evaluate --profile deepseek --task checkout_discovery \
  --runs 3 --max-steps 24 --no-mcp-search --output /tmp/baseline-report.json

mini-coding-agent-evaluate --profile deepseek --task checkout_discovery \
  --runs 3 --max-steps 24 --mcp-search --output /tmp/mcp-report.json
```

Inspect actual `mcp_search_repo` calls; enabling a tool does not force its use.
Compare independent correctness, normal completion, rejected responses, and
model turns as well as elapsed time. Small repeated fixtures are not independent
evidence of general coding ability. Provider latency, caching, and task-specific
prompts can affect apparent performance.

Use `--log-file` for progress and model metrics; consult the
[observability guide](observability.md) for event correlation and optional
rejected-response previews. Automated harness tests use scripted models and do
not establish live model performance.
