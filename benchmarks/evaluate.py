"""Independent benchmark. Hidden tests are copied only after agent completes."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from coding_agent.core import CodingAgent, OllamaModel, Workspace

BASE = Path(__file__).parent / "tasks"

def evaluate(model="qwen3:8b", runs=3):
    results = []
    for run in range(runs):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(BASE / "add_bug", repo)
            agent = CodingAgent(OllamaModel(model), Workspace(repo))
            result = agent.run("Fix the bug in calculator.add so it returns the sum of its inputs. Run tests.")
            # Never expose hidden tests to the agent.
            shutil.copy2(BASE / "add_bug_hidden" / "test_hidden.py", repo / "test_hidden.py")
            test = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=repo,
                capture_output=True, text=True, timeout=30)
            results.append({"run": run + 1, "passed": test.returncode == 0,
                "steps": result["steps"], "latency_seconds": result["latency_seconds"],
                "test_output": (test.stdout + test.stderr)[-2000:], "trace": result["trace"]})
    return {"model": model, "runs": runs, "success_rate": sum(r["passed"] for r in results) / runs,
            "results": results}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", default="benchmark-results.json")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    data = evaluate(args.model, args.runs)
    Path(args.output).write_text(json.dumps(data, indent=2))
    print(json.dumps({k: v for k, v in data.items() if k != "results"}, indent=2))
