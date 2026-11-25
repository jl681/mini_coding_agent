# Repository Guidelines

## Project Structure

`coding_agent/core.py` contains the agent loop, local `Workspace`, and tool construction. `models.py` defines model adapters and the provider factory; `actions.py` holds normalized actions; `tools.py` handles registration and validation. `config.py` loads environment files and profiles, `events.py` records JSONL events, and the MCP modules implement subprocess search. `cli.py` and `benchmarks/evaluate.py` are separate entry points. Tests are grouped by component under `tests/`; visible and hidden benchmark fixtures live under `benchmarks/tasks/`. Read `README.md` for usage and `docs/architecture.md` for boundaries.

## Development Commands

Use Python 3.10+. Create an environment with `python3 -m venv .venv`, activate it, and install with `pip install -e '.[test]'`. Run `python -m pytest -q` for offline verification or select a component, for example `python -m pytest -q tests/test_read_batches.py`. `RUN_LIVE=1 python -m pytest -q -m live` explicitly enables the Ollama integration test.

`mini-coding-agent --profile local-qwen --workspace /tmp/demo "Fix the bug and run tests"` operates on an existing disposable workspace. `mini-coding-agent-evaluate --profile deepseek --task add_bug --runs 1` creates its own trial workspace. Model-backed commands require the selected service; hosted calls may incur charges.

## Configuration and Style

Keep repository code, comments, and documentation in English. Use four-space indentation, `snake_case` functions, `PascalCase` classes, and explicit type annotations at interfaces. No formatter or linter is configured. Keep credentials in ignored `.env` files, nonsecret presets in `profiles.json`, and invocation-specific paths in CLI arguments. Preserve provider-independent agent logic and clear validation errors.

## Testing and Execution Contracts

Use pytest `test_*.py` files and `test_*` functions, temporary workspaces, scripted models, and mocked HTTP. Keep live tests opt-in. Native read batches require explicit host read-only declarations, whole-batch schema validation, and correlated call IDs; writes, tests, and `finish` remain individual actions. Steps count completed model responses, not tool entries. Never expose hidden regressions to the agent or equate visible-test success with independent correctness.

## Commits and Review

Follow the imperative subjects in Git history, such as `Support sequential batches of trusted read-only tools`. Describe behavior changes and validation in pull requests. Include provider, profile, budgets, and repetitions for benchmark claims. Update documentation when CLI or configuration changes.

## Current Limits

Workspace path checks are not isolation: tests execute local code with inherited permissions and environment. Use trusted copies. Container backends, new-file creation, and patch export remain planned work in `docs/roadmap.md`.
