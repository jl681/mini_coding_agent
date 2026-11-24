"""Provider-neutral actions and strict JSON parsing for action arguments."""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class AgentAction:
    name: str
    arguments: dict
    call_id: str | None = None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def reject_json_constant(value):
    raise ValueError("Non-finite JSON numbers are not allowed")


def strict_json_loads(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_json_constant)
