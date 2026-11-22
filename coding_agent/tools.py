"""Transport-independent tools with a deliberately small argument contract.

Schemas support objects with required/optional string properties only. Extend the
validator (or adopt a full JSON Schema validator) before accepting richer schemas.
"""
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol
import json


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict

    def execute(self, arguments: dict) -> dict: ...


@dataclass
class FunctionTool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]

    def execute(self, arguments: dict) -> dict:
        return self.handler(arguments)


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]):
        self._tools = {}
        for tool in tools:
            if not tool.name or tool.name == "finish" or tool.name in self._tools:
                raise ValueError(f"Invalid or duplicate tool name: {tool.name}")
            schema = tool.input_schema
            if (set(schema) != {"type", "properties", "required", "additionalProperties"}
                    or schema["type"] != "object"
                    or schema["additionalProperties"] is not False
                    or not isinstance(schema["properties"], dict)
                    or any(spec != {"type": "string"} for spec in schema["properties"].values())
                    or not isinstance(schema["required"], list)
                    or any(name not in schema["properties"] for name in schema["required"])):
                raise ValueError("Only closed object schemas with string properties are supported")
            self._tools[tool.name] = tool

    def describe(self) -> str:
        return json.dumps([
            {"name": tool.name, "description": tool.description,
             "input_schema": tool.input_schema}
            for tool in self._tools.values()
        ])

    def execute(self, name: str, arguments: dict) -> dict:
        if not isinstance(name, str) or name not in self._tools:
            return {"error": {"code": "unknown_tool", "message": f"Unknown tool: {name}"}}
        tool = self._tools[name]
        schema = tool.input_schema
        if not isinstance(arguments, dict):
            message = "Tool arguments must be an object"
        elif any(key not in arguments for key in schema["required"]):
            message = "Missing required arguments: " + ", ".join(
                key for key in schema["required"] if key not in arguments)
        elif any(key not in schema["properties"] for key in arguments):
            message = "Unexpected arguments: " + ", ".join(
                key for key in arguments if key not in schema["properties"])
        elif any(not isinstance(value, str) for value in arguments.values()):
            message = "Tool argument values must be strings"
        else:
            try:
                return tool.execute(arguments)
            except (ValueError, OSError) as exc:
                return {"error": {"code": "execution_error", "message": str(exc)}}
        return {"error": {"code": "invalid_arguments", "message": message}}


def string_arguments(*names: str) -> dict:
    return {"type": "object", "properties": {name: {"type": "string"} for name in names},
            "required": list(names), "additionalProperties": False}
