# Tool Integration Architecture

Tool integration has two stages: `workspace_tools()` constructs and connects the tools, then `CodingAgent` invokes them through the registry during execution.

## Construction and Execution

```mermaid
flowchart TD
    subgraph Setup["1. Construction"]
        W["Workspace(root)<br/>Repository operations and restrictions"]
        F["workspace_tools(workspace)<br/>Factory function"]
        FT["FunctionTool objects<br/>name, description, schema, handler"]
        R["ToolRegistry<br/>Stores tools by name"]
        A["CodingAgent(model, registry)"]

        W --> F
        F -->|Creates handlers referencing workspace| FT
        FT -->|Registers objects| R
        R -->|Injected into agent| A
    end

    subgraph Run["2. Execution"]
        P["Prompt generated from registry.describe()"]
        M["Model returns an action<br/>and arguments"]
        D["registry.execute(action, args)<br/>Find tool and validate arguments"]
        E["FunctionTool.execute(args)"]
        H["Stored handler(args)"]
        WE["Workspace.execute(action, args)<br/>Read, write, list, or run tests"]
        O["Observation recorded in trace<br/>and returned to model"]
        DONE["Finish: return summary and trace"]

        P --> M
        M -->|Tool action| D
        M -->|Valid finish action| DONE
        D -->|Valid call| E
        D -->|Unknown tool or invalid arguments| O
        E --> H
        H --> WE
        WE --> O
        O --> M
    end

    A --> P
    H -.->|Uses workspace captured during setup| W
```

`workspace_tools()` runs during construction, not during each tool call. It creates `FunctionTool` objects whose handlers retain a reference to the supplied workspace. The registry stores those objects by name and generates their descriptions for the model prompt.

## Example: Reading a File

```mermaid
sequenceDiagram
    participant A as CodingAgent
    participant R as ToolRegistry
    participant T as FunctionTool: read_file
    participant W as Workspace

    A->>R: execute("read_file", {"path": "app.py"})
    R->>R: Look up tool and validate arguments
    R->>T: execute({"path": "app.py"})
    T->>W: handler calls workspace.execute("read_file", args)
    W->>W: Validate path and read file
    W-->>T: {"path": "app.py", "content": "..."}
    T-->>R: Result
    R-->>A: Observation
```

The registry retrieves an actual `FunctionTool` object, so `tool.execute()` invokes `FunctionTool.execute()` through normal Python method lookup. That method calls its stored handler, which delegates to the workspace.

## Responsibilities and Boundaries

`Tool` is a protocol describing the attributes and execution method a registered tool must provide; it is not another execution step. `FunctionTool` implements that contract by wrapping a callable. Other implementations can satisfy the protocol without inheriting from it or depending on a workspace.

`ToolRegistry` handles lookup, argument validation, and structured errors for unknown tools, invalid arguments, and expected execution failures. Its current schema support is limited to closed objects with string properties. `Workspace` owns repository operations and filesystem restrictions; path validation does not provide a security sandbox.

`CodingAgent` handles model interaction, action parsing, observations, tracing, and termination. The `finish` action belongs to the agent lifecycle rather than the registry. Runs also stop at the configured step limit; a finished run does not independently establish task correctness.

The current implementation retains a second dispatcher in `Workspace.execute()`. A later simplification could expose explicit workspace methods and connect handlers directly to them. MCP tools use the same registry through a subprocess client adapter; the current adapter accepts only the registry’s restricted string-object schemas.


## MCP Search Integration

```mermaid
sequenceDiagram
    participant CLI
    participant C as MCPClient
    participant S as Search server subprocess
    participant A as CodingAgent
    participant R as ToolRegistry
    participant T as FunctionTool: mcp_search_repo

    CLI->>C: Enter context and launch server
    C->>S: initialize
    S-->>C: Protocol version and capabilities
    C->>S: notifications/initialized
    CLI->>C: tools({"search_repo"})
    C->>S: tools/list
    S-->>C: Tool name, description, inputSchema
    C-->>CLI: FunctionTool adapters
    CLI->>R: Combine local and MCP tools
    CLI->>A: Run with combined registry
    A->>R: execute("mcp_search_repo", args)
    R->>T: execute(args), after validation
    T->>C: call_tool("search_repo", args)
    C->>S: tools/call
    S-->>C: Content and structured search results
    C-->>T: Observation or structured error
    T-->>R: Result
    R-->>A: Result
    CLI->>C: Exit context, including on failure
    C->>S: Close stdin and terminate if necessary
```

The model still chooses the action. The registry dispatches to a function adapter,
whose handler sends an MCP request instead of calling `Workspace.execute()`.
The search server has its own repository root and read-only implementation;
local editing and pytest tools continue to use `Workspace`. The server process
persists across calls and is closed when the CLI run ends.

This is a small implementation of the
[MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [tool discovery/calls](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
not a general-purpose SDK. Tools are explicitly selected and namespaced with
`mcp_`; unsupported schemas fail during setup. Transport failures invalidate the
connection, while tool execution errors are returned as observations. See the
README for limits and supported behavior.
