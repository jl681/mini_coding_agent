# Configuration Guide

[Back to README](../README.md) · [CLI quick reference](../README.md#command-reference)

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
`json_mode`, `token_limit_field`, `native_tools`, `max_steps`, `mcp_search`, and `mcp_timeout`.
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
nonsecret settings. The supplied `.env.example` contains only a key slot.

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
JSON action text. Native API tool calls are available with `--native-tools`. Multimodal messages,
streaming, Responses API, and native Anthropic/Gemini formats are not implemented. Endpoint
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
and native tool-call responses in text mode are reported as model errors. See the
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
behavior. `--max-output-tokens` optionally bounds generation through the selected adapter.

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

