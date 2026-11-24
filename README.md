# Mini Coding Agent (Local Ollama)

A small, inspectable coding agent inspired by mini-swe-agent. This is an educational implementation, not a production sandbox.

## Requirements

- Python 3.10+
- Ollama with `qwen3:8b` pulled (`ollama pull qwen3:8b`)
- pytest for tests

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e '.[test]'
ollama pull qwen3:4b
```

## Run

**Use only disposable, trusted workspaces**. Test execution runs repository code and is not securely sandboxed.

```bash
cp -R benchmarks/tasks/add_bug /tmp/coding-agent-demo
mini-coding-agent --workspace /tmp/coding-agent-demo "Fix calculator.add so it adds rather than subtracts. Run tests."
```

## Tests

```bash
pytest -q
RUN_LIVE=1 pytest -q -m live
```

Tests are organized by responsibility: `tests/test_tool_registry.py` covers
registration and argument validation, `tests/test_workspace.py` covers local
operations and adapters, and `tests/test_edit_file.py` covers exact-match editing.
`tests/test_agent.py` exercises scripted agent decisions and recovery;
`tests/test_integration.py` contains the opt-in live Ollama test.
`tests/test_mcp.py` tests real MCP subprocess communication without Ollama or network access.

Run one component's tests with `python -m pytest -q tests/test_tool_registry.py`.

## Independent evaluation

After installing the project, use the `mini-coding-agent-evaluate` console command
or the equivalent `python -m benchmarks.evaluate`. Reinstall with
`pip install -e '.[test]'` after adding or changing console entry points.

```bash
mini-coding-agent-evaluate --model qwen3:8b --task checkout_discovery \
  --runs 1 --no-think --output /tmp/coding-agent-discovery-no-think.json
```

```bash
# Three runs per task: fifteen fresh workspaces in total.
python -m benchmarks.evaluate --model qwen3:4b --runs 3 --output benchmark-results.json

# Select one task and set explicit execution limits.
python -m benchmarks.evaluate --model qwen3:4b --task order_total --runs 2 --max-steps 16
```

The suite contains five small repair tasks:

| Task | Behavior being evaluated |
| --- | --- |
| `add_bug` | Repair arithmetic addition while preserving negative and fractional inputs. |
| `order_total` | Inspect `orders.py` and `pricing.py` to fix line pricing used by order aggregation. |
| `average_empty` | Handle empty sequences when the existing visible tests already pass. |
| `checkout_shipping` | Repair shipping eligibility in the original small checkout fixture. |
| `checkout_discovery` | Follow nested purchase-flow modules to find a shipping data-flow bug while preserving preview and subscription behavior. |

Each trial uses a fresh workspace and model instance. After the agent stops, the
evaluator copies its source changes into a separate grading workspace containing
the original visible tests and hidden regression tests. Changes to visible tests
are recorded in the diff but do not affect grading. Hidden tests never enter the
agent workspace. This separation prevents accidental test weakening; it is not a
security boundary against malicious repository code.

The JSON report includes overall and per-task success rates, per-run traces,
Python-file diffs, tool call/error counts, invalid action counts, test output,
termination reasons, and elapsed time including grading. `outcome` distinguishes
`passed`, `incorrect_repair`, `step_limit`, `model_error`, `execution_error`,
`evaluation_error`, and `evaluation_timeout`. The independent `passed` field records
whether grading tests passed, even when the agent exhausted its step budget.
Model and agent failures count as unsuccessful trials and do not stop later runs.
`--test-timeout` bounds independent grading, not the total agent run.

### Compare local tools with MCP search

Run the same task, model, repetition count, and step budget in both modes:

```bash
python -m benchmarks.evaluate --model qwen3:8b --task checkout_discovery \
  --runs 3 --max-steps 16 --output /tmp/coding-agent-discovery-baseline.json

python -m benchmarks.evaluate --model qwen3:8b --task checkout_discovery \
  --runs 3 --max-steps 16 --mcp-search --mcp-timeout 10 \
  --output /tmp/coding-agent-discovery-mcp.json
```

The instruction describes free shipping after discounts without naming the
faulty file or requiring search. The agent can discover the issue using either
local tools or MCP search. The public entry point delegates through
`storefront/service.py` to `policies/order_quote.py`; related subscription and
cart-preview flows also calculate shipping. Visible tests already pass;
independent regressions check discount-driven threshold crossings, the exact
boundary, helper behavior, and preservation of the other purchase-related flows.
The original `checkout_shipping` fixture remains available unchanged.

Tool descriptions now explicitly require relative paths and reading code before
editing. Rerun both modes with the updated prompt; do not attribute differences
from older reports solely to MCP. The new task increases discovery work but does
not guarantee search use or a measurable advantage.

With `--mcp-search`, each trial starts its own server against that trial's
workspace and closes it before independent grading, including on agent failure.
Setup failures are recorded as `execution_error` and later trials continue.
Reports record `mcp_search` at both report and trial level, plus `mcp_timeout` in
the report. Latency includes server setup and cleanup as well as grading.

Compare success rates and per-run latency, then inspect `tool_calls`,
`tool_errors`, and `trace` to see whether `mcp_search_repo` was used and what it
returned. Search use itself is not a scoring requirement, and enabling MCP does
not force the model to use it. A few runs on one task are exploratory evidence,
not proof of a general improvement.

Repeat `--task` to select multiple tasks. `--runs` means repetitions **per task**.
These fixtures provide a development baseline, not evidence of general coding
capability. Deterministic harness tests in `tests/test_benchmarks.py` use scripted
models to verify scoring, isolation, repetition, and failure reporting without
Ollama; their scores do not measure a live model's performance.

## Durable run logs

Use `--log-file` on either CLI to append JSON Lines events as execution happens:

```bash
mini-coding-agent --workspace /tmp/coding-agent-demo --model qwen3:8b \
  --no-think --log-file /tmp/agent-events.jsonl "Find and fix the bug. Run tests."

mini-coding-agent-evaluate --model qwen3:8b --task checkout_discovery \
  --runs 1 --no-think --mcp-search --log-file /tmp/benchmark-events.jsonl \
  --output /tmp/benchmark-report.json

tail -f /tmp/benchmark-events.jsonl
```

Each event is flushed immediately and includes a UTC timestamp, schema version,
and unique run ID. Events cover run start/termination, model-request
start/completion/failure, action rejection, and tool start/completion/failure.
Tool error observations are distinguished from unexpected exceptions. Logs from
benchmark agents also include task name, trial number, and MCP mode; report
entries carry the corresponding run ID even on model failures. Server setup
failures occur before an agent run and have no run ID or agent events. Independent
grading remains in the benchmark report rather than the agent event stream.

Model completion events retain Ollama's token counts, durations (in nanoseconds),
and `done_reason` when supplied. Event elapsed times are in seconds. A
`done_reason` of `length` produces a separate truncation event and rejected action;
that response never executes a tool. The loop may recover within its remaining
step budget. Returned action traces remain available alongside the new run ID.

Full prompts, tasks, raw model responses, and internal thinking text are omitted.
A hash of the assembled system prompt identifies prompt changes, and model name,
thinking mode, response timeout, and step budget are recorded at run start.
Tool arguments and observations are logged with bounded strings and collection
depth/size, so logs may still contain source code or other sensitive tool data.
Bounds affect saved events only, not execution or the returned trace.

The recorder opens in append mode, requires an existing parent directory, and
supports one writer per file. File I/O errors propagate rather than silently
losing events. Flushing preserves completed writes across ordinary process
failures, but is not an `fsync` guarantee against power loss. Without `--log-file`,
no persistent events are written. Uncatchable termination can leave a start event
without its matching completion.

For custom model implementations, `respond(messages)` now returns
`ModelResponse(content, metadata={})` from `coding_agent.models` (also re-exported by `coding_agent.core`), rather than a plain
string. Metadata is optional; scripted models can return
`ModelResponse(json.dumps(action))`.

## Named configuration profiles

Use `profiles.json` for nonsecret model and tool settings, and `.env` for keys.
The supplied profiles are `local-qwen` (local Ollama with thinking disabled) and
`deepseek` (direct DeepSeek Chat Completions). Each profile sets provider, model,
endpoint, generation budget, step budget, and MCP search mode. No profile is
selected automatically.

For direct DeepSeek calls, copy the key template if you do not already have `.env`:

```bash
cp .env.example .env
```

Edit the private file to set `DEEPSEEK_API_KEY`, then run:

```bash
mini-coding-agent-evaluate --profile deepseek --task add_bug --runs 1 \
  --output /tmp/deepseek-report.json --log-file /tmp/deepseek-events.jsonl

mini-coding-agent --profile deepseek --workspace /tmp/coding-agent-demo \
  "Find and fix the bug. Run tests."
```

Ollama is not used by the `deepseek` profile. Its endpoint, model ID, and
`max_tokens` field follow the
[DeepSeek Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/).
API access still requires your account credentials; no live paid request is made
when loading a profile.

Switch profiles or override individual settings without changing code:

```bash
mini-coding-agent-evaluate --profile local-qwen --task checkout_discovery \
  --runs 1 --mcp-search --max-output-tokens 2048

mini-coding-agent-evaluate --profile deepseek --task add_bug --runs 1 --no-mcp-search
```

Example profile structure:

```json
{
  "profiles": {
    "my-local-model": {
      "provider": "ollama",
      "model": "gemma3:4b",
      "max_output_tokens": 1024,
      "max_steps": 16,
      "mcp_search": false
    }
  }
}
```

Pass `--profiles-file /path/to/profiles.json` for another file. The default is
`profiles.json` in the current directory; it is only read when `--profile` is
supplied. Missing profiles, unknown fields (including literal API-key fields),
and invalid types fail at setup. Supported settings are `provider`, `model`,
`base_url`, `api_key_env`, `model_timeout`, `think`, `max_output_tokens`,
`json_mode`, `token_limit_field`, `max_steps`, `mcp_search`, and `mcp_timeout`.
Provider and model are required. Store the environment variable's name in
`api_key_env`, never its value. Provider-specific option combinations are checked
by the model factory. Workspace, task, report destinations, and log destinations
remain per-invocation arguments rather than profile settings.

With a profile selected, precedence is explicit CLI flags, then profile values,
then built-in defaults. **Profiles replace MODEL_* defaults as a unit**, so
switching providers cannot accidentally reuse another provider's endpoint or
credential-variable selection from `.env`. An incompatible explicit override
still fails validation. `--no-mcp-search` can disable a profile's search setting.

Reports and agent events record the selected profile name and a SHA-256 hash of
its resolved settings. The hash identifies the profile before CLI overrides;
actual model settings and execution budgets are recorded separately. Editing a
profile changes its hash. The reusable `load_profile(name, path)` function
returns validated settings without loading keys, modifying the environment, or
starting an agent, so another application entry point can use the same loader.

## Environment files and legacy defaults

Both CLIs load `.env` from the current directory, not the target workspace.
`--env-file /path/to/config.env` selects another file. A missing default `.env` is
ignored; an explicitly requested missing file is an error. Existing shell
variables take precedence over file values. Keys are read from the selected
variable when the model adapter is constructed.

Without `--profile`, existing behavior remains: CLI flags override `MODEL_*`
values from the environment, which override built-in defaults. Supported legacy
defaults are `MODEL_PROVIDER`, `MODEL_NAME`, `MODEL_BASE_URL`, `MODEL_API_KEY_ENV`,
`MODEL_TOKEN_LIMIT_FIELD`, `MODEL_MAX_OUTPUT_TOKENS`, and `MODEL_TIMEOUT`. Existing
`.env` files need not be changed, but named profiles are preferred for reusable
nonsecret settings. The supplied `.env.example` now contains only a key slot.

The literal loader supports single-line assignments, quotes, comments, optional
`export`, and empty values. Quote values containing spaces. It does not evaluate
shell commands, interpolate variables, or support multiline values. Malformed
files fail without echoing their contents. `.env` is Git-ignored. API keys are
omitted from benchmark configuration and event metadata. Programmatic model
factory calls do not automatically load `.env`; callers explicitly choose when
to load files.

## Model providers

Both CLIs use `create_model()` from `coding_agent/models.py`. The agent loop
continues to depend only on `Model.respond()` and `ModelResponse`; it does not
construct providers or translate their HTTP payloads. Existing imports of
`OllamaModel`, `Model`, and `ModelResponse` from `coding_agent.core` remain valid.

Ollama is the default provider and defaults to `qwen3:8b`. Switch installed models
by name, without changing agent code; only pass thinking flags to models that
support them:

```bash
mini-coding-agent-evaluate --provider ollama --model gemma3:4b \
  --task add_bug --runs 1 --max-output-tokens 1024

mini-coding-agent-evaluate --provider ollama --model qwen3:8b --no-think \
  --task checkout_discovery --runs 1 --max-output-tokens 1024
```

`--base-url` overrides the Ollama server root (default
`http://127.0.0.1:11434`); the adapter appends `/api/chat`.

For a server implementing the OpenAI Chat Completions HTTP format, select
`--provider openai-compatible` and supply its model ID and API base URL. The
adapter appends `/chat/completions` to that base, so include an API prefix such as
`/v1`, not the complete chat endpoint:

```bash
mini-coding-agent-evaluate --provider openai-compatible \
  --base-url http://localhost:1234/v1 --model your-model-id \
  --task add_bug --runs 1 --max-output-tokens 1024
```

This command assumes you have already started that server. An explicitly
selected model is required; an Ollama model ID is not automatically translated
into a different server's model ID. Hosted Chat Completions endpoints use the
same adapter with their API base URL. Set credentials through the environment,
not command-line key values:

```bash
mini-coding-agent-evaluate --provider openai-compatible \
  --base-url https://api.openai.com/v1 --model your-model-id \
  --api-key-env OPENAI_API_KEY --task add_bug --runs 1
```

The named variable must already contain a key. Without `--api-key-env`, remote
compatible endpoints use `OPENAI_API_KEY` if set; loopback endpoints receive no
key implicitly. Explicitly select an environment variable for authenticated local
servers. Keys are not included in benchmark configuration or event metadata.

The compatible adapter requests JSON-object output by default; use
`--no-json-mode` if the endpoint does not support it. The agent still requires
JSON action text. Native API tool calls, multimodal messages, streaming,
Responses API, and native Anthropic/Gemini formats are not implemented. Endpoint
and model features vary: this is Chat Completions compatibility, not a promise
that every provider/model accepts identical options. Unsupported `--think` or
`--no-think` flags fail explicitly for this adapter instead of being ignored.

`--max-output-tokens` must be positive when supplied; omission preserves the
provider's default. Ollama receives `options.num_predict`; compatible endpoints
receive `max_completion_tokens`. For endpoints that require the legacy spelling,
select `--token-limit-field max_tokens` (compatible provider only). Token budgets
can include reasoning tokens and are not equivalent measures of useful text
across models. The compatible adapter omits temperature to avoid assuming all
models support that setting.

Compatible responses normalize `finish_reason` into the `done_reason` metadata
used by the agent; usage counts retain their provider field names. A length limit
never executes a partial action. Refusals, filtered responses, malformed responses,
and native tool-call responses are reported as model errors. See the
[Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
for the underlying response format and token controls.

Reports record provider, model, token budget, JSON mode, and compatible token
field selection. Event logs record provider and adapter configuration without
credentials. Compare models under explicitly recorded settings; equal token
budgets do not guarantee equal reasoning effort. Adapter tests mock HTTP; no
third-party endpoint or paid API has been called to validate this feature.

## Model thinking mode

For Ollama, both entry points accept `--think` to enable model thinking and `--no-think` to
disable it. Omitting both leaves the request field unset, preserving Ollama's
model default. The setting is sent as top-level `think`, not inside `options`;
support depends on the selected model. Benchmark reports record `think` as
`true`, `false`, or `null` (model default).

```bash
mini-coding-agent --workspace /tmp/coding-agent-demo --model qwen3:8b --no-think \
  "Find and fix the addition bug. Run tests."

python -m benchmarks.evaluate --model qwen3:8b --task checkout_discovery \
  --runs 1 --max-steps 16 --no-think --model-timeout 180 \
  --output /tmp/coding-agent-discovery-no-think.json
```

Use the same thinking setting in both modes when comparing MCP search. Disabling
thinking does not disable model action selection; it changes model generation
behavior. `--max-output-tokens` now optionally bounds generation through the selected adapter.

## Troubleshooting local model timeouts

Localhost Ollama requests bypass environment proxies. Connection establishment
has a five-second timeout; `--model-timeout` controls the response read timeout
(default 180 seconds) for both the agent CLI and evaluator. It does not change
MCP or grading timeouts, and is not a total trial deadline.

First try one trial with a longer response allowance:

```bash
python -m benchmarks.evaluate --model qwen3:8b --task checkout_discovery \
  --runs 1 --max-steps 16 --model-timeout 600 \
  --output /tmp/coding-agent-discovery-diagnostic.json
```

A longer timeout allows slow generation but does not establish its cause. If
this still times out, inspect Ollama's logs and resource usage, check for concurrent
generation requests, and compare a simple direct chat request. A successful
`/api/tags` request establishes availability, not successful model generation.
Use the same timeout in both modes when resuming the MCP comparison.

## Design

- Ollama `/api/chat` JSON-format response with one action per step.
- Five tools: `list_files`, `read_file`, `write_file`, `edit_file`, `run_tests`.
- `finish` action and maximum-step termination.
- Full action/observation trace in output.
- Independent hidden tests and repeat-run success rate.

## Localized edits

Prefer `edit_file` for small changes rather than rewriting a whole file:

```json
{"action":"edit_file","args":{"path":"calculator.py","old_text":"return a - b","new_text":"return a + b"}}
```

Read the file first. `old_text` must be non-empty and match exactly once,
including whitespace; overlapping matches are rejected as ambiguous. Reread the
file after a mismatch or include more context when a match is ambiguous.
`new_text` may be empty to delete a passage. Only existing workspace `.py` files
are editable, and both the original and resulting files are limited to 100,000
UTF-8 bytes. Validation failures leave file bytes unchanged; successful edits
preserve bytes outside the replaced passage. `write_file` remains available for
whole-file replacement.

## MCP repository search

Enable the bundled read-only MCP server alongside the existing local tools:

```bash
mini-coding-agent --workspace /tmp/coding-agent-demo --mcp-search --mcp-timeout 10 \
  "Find the addition bug, fix it, and run tests."
```

The model sees `mcp_search_repo` with a string `query` argument. It performs
literal, case-sensitive searches in Python files and returns relative paths,
1-based line numbers, and matching text. The server skips symlinks, virtual
environments, Git metadata, and oversized files. Results are limited to 100
matches, 500 scanned Python files, and 500 characters per matching line; check
`truncated` before assuming the results are exhaustive.

`MCPClient` launches a subprocess, initializes MCP, discovers tools with
`tools/list`, and invokes them with `tools/call`. The connection stays open for
the agent run and closes even if the model fails. Each transport operation has
a timeout; a transport failure closes the connection and becomes an error
observation, while a server-reported tool failure leaves the connection usable.
There is no automatic reconnect. Startup/discovery failures abort setup.

The implementation uses the standard library and a narrow
[MCP 2025-11-25 stdio protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
subset, not the official SDK. It supports one synchronous request at a time,
initialization, ping handling, paginated discovery, and tool calls. It does not
implement HTTP, resources, prompts, sampling, or richer argument schemas.
Selected tools must expose the registry's closed string-object schemas; other
schemas fail at setup. This has been tested against the bundled subprocess
server and controlled protocol fixtures, not third-party servers.

Programmatic use explicitly selects which discovered tools to expose:

```python
import sys
from coding_agent.mcp_client import MCPClient

with MCPClient([sys.executable, "-m", "coding_agent.mcp_search_server",
                "--workspace", str(workspace.root)]) as client:
    tools = workspace_tools(workspace).with_tools(client.tools({"search_repo"}))
    result = CodingAgent(model, tools).run("Find and fix the bug")
```

Only the bundled server is read-only by construction. Tool descriptions and MCP
annotations do not enforce permissions on arbitrary servers. A configured server
runs as a normal subprocess with the current user's permissions; workspace path
checks do not sandbox it. The CLI only launches the bundled search server.

## Extending tools

`CodingAgent` accepts a `ToolRegistry` instead of a workspace. Existing callers
should wrap their workspace with `workspace_tools(workspace)`:

```python
from coding_agent.core import CodingAgent, OllamaModel, Workspace, workspace_tools

agent = CodingAgent(OllamaModel(), workspace_tools(Workspace("/tmp/coding-agent-demo")))
```

Define a tool through the `Tool` protocol, or wrap a function:

```python
from coding_agent.tools import FunctionTool, ToolRegistry, string_arguments

tools = ToolRegistry([
    FunctionTool("echo", "Echo a message.", string_arguments("message"),
                 lambda args: {"message": args["message"]})
])
agent = CodingAgent(OllamaModel(), tools)
```

Tool definitions generate the model's prompt. The registry validates arguments
before execution and returns structured errors for unknown tools, invalid
arguments, and expected execution failures (`ValueError` or `OSError`). Successful
handlers return JSON-serializable dictionaries. Unexpected implementation errors
and model transport failures propagate to the caller.

Schemas currently support only closed objects with string properties, including
optional properties; `string_arguments` makes every named property required.
Richer schemas are rejected at registration. The MCP adapter currently
accepts the same restricted schemas. `finish` is reserved for the agent
lifecycle; results distinguish `finished` from `step_limit`, neither of which
independently establishes task correctness.

## Security limitations

- `pytest` executes arbitrary code from the repository. A filesystem path check is **not** a sandbox. For untrusted repos, run the entire agent and test runner in an isolated container/VM with restricted network, filesystem, CPU/memory and no secrets.
- The current implementation only edits existing `.py` files, and blocks paths resolving outside the workspace.
- It does not enforce subprocess resource limits beyond wall-clock timeout, prevent malicious symlinks from changing between validation and access, or validate the semantic correctness of patches.
- The five benchmark tasks are intentionally small; broader repositories and task types are needed for credible capability evaluation.
