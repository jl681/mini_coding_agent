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

Run one component's tests with `python -m pytest -q tests/test_tool_registry.py`.

## Independent evaluation

```bash
python -m benchmarks.evaluate --runs 3 --output benchmark-results.json
```

The evaluator creates a fresh repository for each run and adds hidden regression tests **after** the agent finishes. The supplied one-task benchmark is only a smoke test, not evidence of general coding capability.

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
Richer schemas are rejected at registration. This is an extension point for a
future MCP adapter, not an MCP implementation. `finish` is reserved for the agent
lifecycle; results distinguish `finished` from `step_limit`, neither of which
independently establishes task correctness.

## Security limitations

- `pytest` executes arbitrary code from the repository. A filesystem path check is **not** a sandbox. For untrusted repos, run the entire agent and test runner in an isolated container/VM with restricted network, filesystem, CPU/memory and no secrets.
- The current implementation only edits existing `.py` files, and blocks paths resolving outside the workspace.
- It does not enforce subprocess resource limits beyond wall-clock timeout, prevent malicious symlinks from changing between validation and access, or validate the semantic correctness of patches.
- The benchmark task is intentionally simple; expand to multiple repositories and task types for credible evaluation.
