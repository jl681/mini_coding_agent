import os
from pathlib import Path
import shutil
import pytest
from coding_agent.core import CodingAgent, OllamaModel, Workspace

@pytest.mark.live
def test_live_ollama_repairs_bug(tmp_path):
    if os.environ.get("RUN_LIVE") != "1":
        pytest.skip("Set RUN_LIVE=1 and start Ollama to run")
    source = Path(__file__).parents[1] / "benchmarks" / "tasks" / "add_bug"
    shutil.copytree(source, tmp_path / "repo")
    repo = tmp_path / "repo"
    result = CodingAgent(OllamaModel(), Workspace(repo)).run("Fix calculator.add and run tests")
    assert result["steps"] > 0
    assert Workspace(repo).execute("run_tests", {})["exit_code"] == 0
