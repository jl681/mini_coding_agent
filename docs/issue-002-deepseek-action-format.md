# Issue 002: DeepSeek Returns Invalid Action JSON

## Symptoms

On `checkout_discovery`, DeepSeek sometimes returns a response that fails strict JSON parsing with `Extra data`. The agent rejects that response and spends another step asking the model to continue.

In the 24-step run, eight responses were rejected. The agent fixed the source code, but exhausted its budget before running tests or calling `finish`. Independent grading passed all nine tests; the run still terminated with `step_limit`.

## What We Know

API requests completed normally. This was not an authentication failure, timeout, or output-token limit. The source repair was correct, but action-format errors consumed one-third of the step budget.

## Suspected Cause

The response contains additional content after the first JSON value—possibly another action or explanatory text. We do not know its exact form because raw rejected responses are not currently logged. Requesting JSON mode did not prevent these parsing failures in the observed run.

## Implemented Mitigation

The agent now gives explicit recovery feedback: return exactly one JSON object, with no trailing text or additional actions. An opt-in `--log-rejected-responses` flag saves bounded previews to `--log-file` for diagnosis. Parsing remains strict; ambiguous responses execute no tool. Tests cover recovery and preview limits. Live improvement is not yet verified.

The DeepSeek profile now uses native function calls rather than JSON action text. The adapter normalizes calls into `AgentAction`, preserves call IDs, and returns correlated tool observations. Malformed or multiple calls execute nothing. The text parser remains a fallback for other configurations. Native-calling tests pass; a live DeepSeek rerun is still required.

Recorded 2026-11-24. Evidence: `/tmp/deepseek-discovery-24-report.json` and its corresponding event log.
