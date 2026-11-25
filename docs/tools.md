# Tools and Model Protocols

[Back to README](../README.md) · [Architecture](architecture.md)

## Native function calls

The `deepseek` profile enables `native_tools: true` and disables text JSON
mode. The adapter sends registered local and MCP tools through the provider's
`tools` API, together with a `finish` function. It extracts a function name,
JSON arguments, and call ID into a common `AgentAction` instead of parsing action
instructions from ordinary answer text.

```bash
mini-coding-agent-evaluate --profile deepseek --task checkout_discovery --runs 1 \
  --log-file /tmp/deepseek-native-events.jsonl \
  --output /tmp/deepseek-native-report.json
```

`--native-tools` enables this mode for any compatible endpoint that supports
Chat Completions function calls; `--no-native-tools` restores the JSON-action
fallback. Ollama retains its JSON-action path and rejects `--native-tools` for
now. Text JSON mode is suppressed when native calling is enabled.

After each call, the agent returns an assistant message containing the provider's
call and a `role: tool` result with the matching `tool_call_id`. When required,
provider `reasoning_content` is preserved only in conversation memory; it is not
copied into logs or report traces. Ordinary model text is never executed as an
action in native mode. Completion requires a valid `finish` function call.

The adapter uses `tool_choice: auto`: DeepSeek thinking mode does not support
forced `required` choices. The prompt allows up to eight trusted read-only
calls per step, executed sequentially. Mixed or invalid batches execute nothing
and receive an error result for every call ID, allowing the conversation to
recover without leaving unanswered calls. Malformed arguments also return a
correlated error. Missing
or duplicate call IDs make the response unusable and raise a model error.
Unknown tools and invalid argument schemas still pass through normal registry
validation. Truncated responses never execute a tool.

This uses native function calling, not the provider's optional strict-schema
Beta feature. Function arguments remain untrusted JSON and are validated before
execution. Native calling improves protocol structure, not repair correctness,
and may still require retries. See the
[DeepSeek Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls/) and
[Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/).
Tests cover mocked provider round trips and a real bundled MCP subprocess; live provider behavior must be verified independently for the selected configuration.

## Sequential read-only batches

Native responses can contain up to eight calls when every tool is explicitly
marked read-only by the host. Local `list_files` and `read_file` qualify. The CLI
and evaluator explicitly trust the bundled `mcp_search_repo` adapter as read-only.
`write_file`, `edit_file`, `run_tests`, and lifecycle `finish` must each be called
alone. `run_tests` executes repository code and is not classified as read-only.

Before starting a batch, the agent parses every call and checks the entire batch
against tool registration, read-only declarations, and argument schemas. An
unknown tool, invalid argument schema, malformed JSON, non-read-only call, or
batch larger than eight rejects the whole batch. No requested call executes in
that case. Individual path checks and filesystem access occur during execution;
an expected read error returns an error for that call and other reads continue.
An unexpected tool crash aborts the run, preserving already completed traces.

Accepted calls execute in order without threads or concurrent access. Only after
all results are available does the agent ask the model again, returning each
result with its original `tool_call_id`. The model can still choose sequential
single calls when later reads depend on earlier observations.

`steps` and `--max-steps` count completed model-response turns, including rejected
responses and `finish`. One turn can therefore contain several tool trace entries
with the same `step`, distinct `call_id`, and zero-based `batch_index`. Tool-call
counts remain individual counts; do not interpret trace length as model steps.
The CLI result includes total `tool_calls`, while benchmark results retain their
per-tool count mapping. Events include `batch_started`, `batch_completed`, and
per-call tool events. Rejection events also record requested tool names and IDs
without requiring raw-response previews.

Custom tools default to non-read-only. Opt in through
`FunctionTool(..., read_only=True)` only when their implementation warrants it.
`MCPClient.tools(allowed, read_only={...})` requires an explicit host declaration;
server `readOnlyHint` annotations alone do not grant batch eligibility. Text-mode
JSON actions remain single-action responses.

## Action-format recovery

In text mode, the agent accepts one JSON action object per model response and never executes
a prefix extracted from an ambiguous reply. Trailing text, concatenated objects,
duplicate keys, non-finite numbers, and unexpected top-level fields are rejected.
The observation tells the model that no action executed and requests exactly one
object with `action` and `args`, without Markdown or trailing explanations.
Retries consume the normal step budget. This improves recovery guidance but
cannot guarantee that a provider will follow the format on a live run.

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
mini-coding-agent --profile local-qwen --workspace /tmp/coding-agent-demo --mcp-search --mcp-timeout 10 \
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

