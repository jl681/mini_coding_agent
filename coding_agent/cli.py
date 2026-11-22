import argparse
import json
from .core import CodingAgent, OllamaModel, Workspace, workspace_tools

def main():
    parser = argparse.ArgumentParser(description="Local coding agent; run only on disposable trusted workspaces")
    parser.add_argument("task")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()
    result = CodingAgent(OllamaModel(args.model), workspace_tools(Workspace(args.workspace)), args.max_steps).run(args.task)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
