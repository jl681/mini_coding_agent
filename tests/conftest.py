"""Keep local credentials and user CLI defaults out of automated tests."""
from pathlib import Path
import os

import pytest

from coding_agent import config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_user_configuration(monkeypatch):
    for name in list(os.environ):
        if name.startswith("MODEL_") or name in {"DEEPSEEK_API_KEY", "OPENAI_API_KEY"}:
            monkeypatch.delenv(name)
    loader = config.load_env_file

    def isolated_loader(path, required=False):
        if str(path) == ".env" and not required and Path.cwd() == ROOT:
            return
        return loader(path, required=required)

    monkeypatch.setattr(config, "load_env_file", isolated_loader)
