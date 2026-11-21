# Repository Guidelines

## Project Structure & Module Organization

`coding_agent/core.py` contains the Ollama client, workspace tools, and bounded agent loop. `coding_agent/cli.py` exposes the `mini-coding-agent` command. Deterministic tests live in `tests/test_unit.py`; live model tests live in `tests/test_integration.py`. `benchmarks/evaluate.py` evaluates repairs using fixtures in `benchmarks/tasks/add_bug/` and hidden regressions in `benchmarks/tasks/add_bug_hidden/`. Evaluation output is stored in `benchmark-results.json`. Packaging and pytest configuration live in `pyproject.toml`.

## Build, Test, and Development Commands

Use Python 3.10 or newer. Create and activate an environment with `python3 -m venv .venv` and `source .venv/bin/activate`, then install with `pip install -e '.[test]'`. Editable installation is the development workflow; no dedicated build script exists.

Run `pytest -q` for the default suite. Start Ollama and use `RUN_LIVE=1 pytest -q -m live` for live integration testing. The CLI defaults to `qwen3:4b`; the model class and evaluator default to `qwen3:8b`. Pull the appropriate model with `ollama pull` or select one explicitly.

Run the agent with `mini-coding-agent --workspace /tmp/coding-agent-demo --model qwen3:4b "Fix calculator.add and run tests"` after copying a fixture into that disposable directory. Evaluate repeated repairs with `python -m benchmarks.evaluate --model qwen3:4b --runs 3 --output benchmark-results.json`.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions and variables, `PascalCase` classes, and uppercase constants. Follow surrounding type annotations and keep tool behavior explicit. No formatter or linter is configured.

## Testing Guidelines

Use pytest, `test_*.py` files, and `test_*` functions. Prefer `tmp_path` fixtures and scripted model responses for deterministic tests. Cover workspace boundaries, tool restrictions, and termination behavior when modifying them. Mark model-dependent tests with `@pytest.mark.live` and retain the `RUN_LIVE` gate. No coverage threshold is configured. Keep hidden evaluation tests unavailable to the agent until execution finishes.

## Commit & Pull Request Guidelines

This checkout has no Git metadata, so historical commit conventions cannot be verified. Use concise, imperative commit subjects. Pull requests should describe behavior changes, link relevant issues, and report test commands and outcomes. Include model names and run counts for benchmark claims.

## Security & Configuration

Use disposable, trusted workspaces. Tests execute repository code; path validation is not a security sandbox. Run untrusted code in external isolation without secrets.
