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

## Independent evaluation

```bash
python -m benchmarks.evaluate --runs 3 --output benchmark-results.json
```

The evaluator creates a fresh repository for each run and adds hidden regression tests **after** the agent finishes. The supplied one-task benchmark is only a smoke test, not evidence of general coding capability.

## Design

- Ollama `/api/chat` JSON-format response with one action per step.
- Four tools: `list_files`, `read_file`, `write_file`, `run_tests`.
- `finish` action and maximum-step termination.
- Full action/observation trace in output.
- Independent hidden tests and repeat-run success rate.

## Security limitations

- `pytest` executes arbitrary code from the repository. A filesystem path check is **not** a sandbox. For untrusted repos, run the entire agent and test runner in an isolated container/VM with restricted network, filesystem, CPU/memory and no secrets.
- The current implementation only edits existing `.py` files, and blocks paths resolving outside the workspace.
- It does not enforce subprocess resource limits beyond wall-clock timeout, prevent malicious symlinks from changing between validation and access, or validate the semantic correctness of patches.
- The benchmark task is intentionally simple; expand to multiple repositories and task types for credible evaluation.
