import argparse
from .config import configure_cli_env, configure_cli_profiles
import json
import sys
from contextlib import ExitStack
from .mcp_client import MCPClient
from .models import add_model_arguments, model_options, create_model
from .events import JsonlRecorder, NullRecorder
from .core import CodingAgent, Workspace, workspace_tools

def main():
    parser = argparse.ArgumentParser(description="Local coding agent; run only on disposable trusted workspaces")
    parser.add_argument("task")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--mcp-search", action=argparse.BooleanOptionalAction, default=False, help="Enable the bundled read-only MCP search server")
    parser.add_argument("--mcp-timeout", type=float, default=10)
    parser.add_argument("--log-file", help="Append flushed run events to a JSON Lines file")
    parser.add_argument("--log-rejected-responses", action="store_true",
                        help="Include bounded rejected response previews in --log-file")
    configure_cli_env(parser)
    add_model_arguments(parser)
    configure_cli_profiles(parser)
    args = parser.parse_args()
    if args.log_rejected_responses and not args.log_file:
        parser.error("--log-rejected-responses requires --log-file")
    try:
        model = create_model(args.model, **model_options(args))
    except ValueError as exc:
        parser.error(str(exc))
    workspace = Workspace(args.workspace)
    with ExitStack() as stack:
        recorder = stack.enter_context(JsonlRecorder(args.log_file)) if args.log_file else NullRecorder()
        tools = workspace_tools(workspace)
        if args.mcp_search:
            client = stack.enter_context(MCPClient(
                [sys.executable, "-m", "coding_agent.mcp_search_server", "--workspace", str(workspace.root)],
                timeout=args.mcp_timeout))
            tools = tools.with_tools(client.tools({"search_repo"}))
        result = CodingAgent(model, tools, args.max_steps,
                             recorder=recorder, event_context={"mcp_search": args.mcp_search, "profile": args.profile,
                                            "profile_hash": args.profile_hash},
                             log_rejected_responses=args.log_rejected_responses).run(args.task)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
