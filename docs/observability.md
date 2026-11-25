# Run Logs and Diagnostics

[Back to README](../README.md)

## Durable run logs

Use `--log-file` on either CLI to append JSON Lines events as execution happens:

```bash
mini-coding-agent --profile local-qwen --workspace /tmp/coding-agent-demo \
  --log-file /tmp/agent-events.jsonl "Find and fix the bug. Run tests."

mini-coding-agent-evaluate --profile local-qwen --task checkout_discovery \
  --runs 1 --mcp-search --log-file /tmp/benchmark-events.jsonl \
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

Full prompts, tasks, raw model responses, and internal thinking text are omitted
by default. To diagnose malformed action text, opt in with
`--log-rejected-responses --log-file /tmp/agent-debug.jsonl` on either CLI.
Only rejected response content is previewed (first 1,000 characters, plus up to
320 characters around a JSON parse error). These previews can contain sensitive
model-generated text; they are saved only in log events, not returned traces.
Internal thinking fields from the provider response are not collected.
A hash of the assembled system prompt identifies prompt changes (including function schemas in native mode), and model name,
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

For custom model implementations, `respond(messages)` returns
`ModelResponse(content, metadata={})` from `coding_agent.models` (also re-exported by `coding_agent.core`), rather than a plain
string. Metadata is optional; scripted models can return
`ModelResponse(json.dumps(action))`. Native adapters can additionally return an
`AgentAction` plus provider conversation state; their `respond()` method accepts
the available definitions through an optional `tools` argument.

