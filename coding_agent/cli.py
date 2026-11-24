import argparse
import json
import sys
from contextlib import ExitStack
from .mcp_client import MCPClient
from .events import JsonlRecorder, NullRecorder
from .core import CodingAgent, OllamaModel, Workspace, workspace_tools

def main():
    parser = argparse.ArgumentParser(description="Local coding agent; run only on disposable trusted workspaces")
    parser.add_argument("task")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--mcp-search", action="store_true", help="Enable the bundled read-only MCP search server")
    parser.add_argument("--mcp-timeout", type=float, default=10)
    parser.add_argument("--model-timeout", type=float, default=180, help="Model response timeout in seconds")
    parser.add_argument("--think", action=argparse.BooleanOptionalAction, default=None,
                        help="Enable or disable model thinking (--no-think); omitted uses the model default")
    parser.add_argument("--log-file", help="Append flushed run events to a JSON Lines file")
    args = parser.parse_args()
    workspace = Workspace(args.workspace)
    with ExitStack() as stack:
        recorder = stack.enter_context(JsonlRecorder(args.log_file)) if args.log_file else NullRecorder()
        tools = workspace_tools(workspace)
        if args.mcp_search:
            client = stack.enter_context(MCPClient(
                [sys.executable, "-m", "coding_agent.mcp_search_server", "--workspace", str(workspace.root)],
                timeout=args.mcp_timeout))
            tools = tools.with_tools(client.tools({"search_repo"}))
        result = CodingAgent(OllamaModel(args.model, timeout=args.model_timeout, think=args.think), tools, args.max_steps,
                             recorder=recorder, event_context={"mcp_search": args.mcp_search}).run(args.task)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
