# Mini Coding Agent

An educational coding agent that inspects Python repositories, edits existing
files, and runs tests. It supports local Ollama models and OpenAI-compatible
Chat Completions endpoints, including direct DeepSeek API calls. A separate
benchmark runner evaluates repairs with independent regression tests.

Use disposable, trusted workspaces: the current implementation runs repository
tests on your computer and is **not a sandbox**.

## Install

Run these commands from the repository root with Python 3.10 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
```

On Windows, activate with `.venv\Scripts\activate`. Keep the environment activated
for the commands below. Model services are only needed for agent/evaluation runs;
the default test suite runs without model credentials or Ollama.

## Choose a model profile

Two ready-to-use profiles are defined in [profiles.json](profiles.json). A profile
selects model settings and tools; it is not a separate agent implementation.

| Profile | Model and connection | Action protocol | Prerequisite |
| --- | --- | --- | --- |
| `local-qwen` | `qwen3:8b` through local Ollama | JSON action text; thinking disabled | Ollama running and the model installed |
| `deepseek` | `deepseek-flash` through the direct DeepSeek API | Native function calls | `DEEPSEEK_API_KEY` in `.env` or the shell |

Both profiles set a 16-turn budget and disable MCP search. The local profile
sets a 1,024-token generation budget; DeepSeek sets 4,096. Use `--max-steps 24`
when a task needs more investigation and verification. These budgets are
configuration choices, not guarantees that a task will finish.

### Local Ollama

Install Ollama separately, start its application or run `ollama serve` in another
terminal, and download the configured model:

```bash
ollama pull qwen3:8b
```

You do not need to run an interactive `ollama run` session before an API call.
Select this setup with `--profile local-qwen`.

### Direct DeepSeek API

Create `.env` only if you do not already have one:

```bash
test -e .env || cp .env.example .env
```

Edit that file and set your key:

```dotenv
DEEPSEEK_API_KEY=your-actual-key
```

Select it with `--profile deepseek`. This profile calls DeepSeek directly;
Ollama is not involved. Hosted calls may incur API charges. `.env` is Git-ignored;
never put the key value into `profiles.json` or a CLI argument.

Run profile examples from the repository root. `.env` and `profiles.json` are
resolved from the current directory, not the target workspace. Use `--env-file`
and `--profiles-file` to select other locations. No profile is selected implicitly.

## Two commands, two purposes

| Command | Purpose | Workspace behavior | Output |
| --- | --- | --- | --- |
| `mini-coding-agent` | Solve your supplied task | Modifies the existing directory passed to `--workspace` | JSON summary and action trace on stdout |
| `mini-coding-agent-evaluate` | Measure repairs on bundled benchmark tasks | Creates a fresh temporary workspace for every trial | JSON report file and summary on stdout |

Both commands share model profiles and common options. Module equivalents are
`python -m coding_agent.cli` and `python -m benchmarks.evaluate`. If a console
command is missing, reactivate the environment and repeat the editable install.

### Run the agent on a disposable example

Create a fresh workspace; use the same terminal for the following commands:

```bash
DEMO_DIR=$(mktemp -d)
cp -R benchmarks/tasks/add_bug/. "$DEMO_DIR/"

mini-coding-agent --profile local-qwen --workspace "$DEMO_DIR" \
  "Fix calculator.add so it adds its inputs. Run tests."
```

For direct API inference, replace `--profile local-qwen` with `--profile deepseek`.
The agent edits this workspace directly; it does not automatically create a
branch, commit changes, export a patch, or merge anything.

### Evaluate one task

```bash
mini-coding-agent-evaluate --profile deepseek \
  --task checkout_discovery --runs 1 --max-steps 24 \
  --log-file /tmp/agent-events.jsonl \
  --output /tmp/agent-report.json
```

This runs one trial, records live diagnostic events, then grades the resulting
source against original visible tests and hidden regressions. Replace the profile
to evaluate a different model; no `--workspace` argument is needed.

### Evaluate all tasks repeatedly

```bash
mini-coding-agent-evaluate --profile deepseek --runs 3 --max-steps 24 \
  --log-file /tmp/full-events.jsonl --output /tmp/full-report.json
```

Omitting `--task` selects all five tasks. `--runs 3` means three repetitions per
task: **15 trials**, each making multiple model requests. Repeat `--task` to
select a subset. These small fixtures provide a development baseline, not a
measurement of general coding ability.

### Enable MCP repository search

```bash
mini-coding-agent-evaluate --profile deepseek \
  --task checkout_discovery --runs 1 --max-steps 24 --mcp-search \
  --output /tmp/mcp-report.json
```

This starts the bundled read-only search server for the trial and exposes
`mcp_search_repo` to the model. Search is a literal substring match, not semantic
search. Making the tool available does not force the model to use it. Compare
runs with and without the flag using the same profile and budgets.

## Understand the result

`success_rate` is the fraction of trials passing independent grading. Also inspect
`results[].termination_reason`: a patch can pass tests even if the agent exhausted
its budget without calling `finish`. A fully completed successful trial has
`passed: true` and `termination_reason: "finished"`.

`steps` counts model-response turns, including rejected responses and `finish`.
Native models may request up to eight trusted read-only calls in one turn;
those calls execute sequentially and have separate trace entries. Trace length
therefore need not equal steps. Benchmark `tool_calls` counts individual calls by
tool name, while `invalid_actions` and `tool_errors` identify different failures.

```bash
python -m json.tool /tmp/agent-report.json
tail -f /tmp/agent-events.jsonl
```

`--output` defaults to `benchmark-results.json`; it is overwritten on each
invocation. Logs are optional and append JSON Lines events, flushing each event.
Use separate filenames for comparisons. `/tmp` is temporary system storage, not
a project directory. See [evaluation details](docs/evaluation.md) and
[logging details](docs/observability.md).

## Command reference

Obtain the full parser help without running a model:

```bash
mini-coding-agent --help
mini-coding-agent-evaluate --help
```

| Option | Available on | Meaning |
| --- | --- | --- |
| `--workspace PATH` and positional task text | Agent only | Existing repository directory and instruction; both required |
| `--task NAME` | Evaluator only | Select a bundled task; repeatable; omission selects all |
| `--runs N` | Evaluator only | Repetitions per selected task; default 3 |
| `--output PATH` | Evaluator only | Write the complete grading report |
| `--profile NAME` | Both | Load named model/tool settings; built-ins are `local-qwen` and `deepseek` |
| `--profiles-file PATH`, `--env-file PATH` | Both | Select nonsecret profiles and environment/credential files |
| `--max-steps N` | Both | Bound model-response turns; built-in profiles set 16 |
| `--max-output-tokens N` | Both | Optional provider generation budget; may include reasoning tokens |
| `--mcp-search`, `--no-mcp-search` | Both | Enable or disable bundled MCP search |
| `--log-file PATH` | Both | Append durable execution events |
| `--log-rejected-responses` | Both | Include bounded rejected-response previews; requires `--log-file` |
| `--model-timeout SECONDS` | Both | Model response read timeout; default 180; not a whole-run deadline |
| `--mcp-timeout SECONDS` | Both | MCP transport timeout; default 10 |
| `--test-timeout SECONDS` | Evaluator only | Independent grading timeout; default 30; does not configure the agent's test tool |

Advanced model overrides include `--provider`, `--model`, `--base-url`,
`--api-key-env`, `--think` / `--no-think`, `--native-tools` / `--no-native-tools`,
`--json-mode` / `--no-json-mode`, and `--token-limit-field`. Their provider-specific
restrictions are explained in the [configuration guide](docs/configuration.md).
For example, Ollama's `--no-think` cannot be used with the DeepSeek adapter.

With a profile, explicit flags override profile settings, which override built-in
defaults. Profiles replace legacy `MODEL_*` defaults as a unit to avoid mixing
providers. Credential lookup still uses the environment, with shell values taking
precedence over `.env`. Without a profile, legacy environment defaults continue
to work; the built-in fallback is Ollama with `qwen3:8b` and its default thinking
behavior. Use profiles for reproducible runs.

## Development

```bash
python -m pytest -q
python -m pytest -q tests/test_read_batches.py
RUN_LIVE=1 python -m pytest -q -m live
```

The first two commands run offline tests, including mocked model APIs and real
MCP subprocess checks. The last command explicitly enables the live Ollama test;
it requires the installed model and running server. There is no configured
formatter, linter, or coverage threshold.

| Location | Responsibility |
| --- | --- |
| `coding_agent/core.py` | Agent loop, local workspace operations, tool construction |
| `coding_agent/models.py` | Model interface, Ollama/compatible adapters, provider factory |
| `coding_agent/config.py`, `profiles.json` | Environment loading and named profiles |
| `coding_agent/tools.py`, `coding_agent/actions.py` | Tool contracts, validation, normalized actions |
| `coding_agent/mcp_client.py`, `coding_agent/mcp_search_server.py` | MCP transport and bundled search server |
| `coding_agent/events.py` | JSON Lines event recording |
| `coding_agent/cli.py` | Agent console entry point |
| `benchmarks/evaluate.py`, `benchmarks/tasks/` | Evaluation runner and visible/hidden fixtures |
| `tests/` | Registry, workspace, model, agent, profile, logging, batch, and evaluator tests |

## Further documentation

[Configuration](docs/configuration.md) explains profiles, credentials, model
adapters, and timeouts. [Tools](docs/tools.md) covers native calls, batching, edits,
and MCP. [Architecture](docs/architecture.md) describes component interactions.
[Run logs](docs/observability.md) explains event fields and privacy limits.
[Evaluation](docs/evaluation.md) describes grading and failure categories.

The [roadmap](docs/roadmap.md) is planned work: execution backend abstraction,
container isolation, real-repository tasks, and patch export are not implemented.
Historical investigations are recorded in [issue 001](docs/issue-001-ollama-generation-timeout.md)
and [issue 002](docs/issue-002-deepseek-action-format.md).

Repository tests execute arbitrary code with the current user's permissions and
environment. Path checks and temporary workspaces are not isolation. Existing
Python files can be edited; new-file creation is not supported. See the roadmap
before considering untrusted repositories or production use.
