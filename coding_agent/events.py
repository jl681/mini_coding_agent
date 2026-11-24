"""Append-only JSON Lines diagnostics; flushed events survive ordinary failures."""
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Protocol


class EventRecorder(Protocol):
    def emit(self, event: dict) -> None: ...


class NullRecorder:
    def emit(self, event: dict) -> None:
        pass


def bounded(value, depth=0):
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + " [truncated]"
    if depth >= 6:
        return "[depth limit]"
    if isinstance(value, dict):
        result = {key: bounded(item, depth + 1) for key, item in list(value.items())[:50]}
        if len(value) > 50:
            result["_truncated"] = True
        return result
    if isinstance(value, list):
        return [bounded(item, depth + 1) for item in value[:50]] + (["[truncated]"] if len(value) > 50 else [])
    return value


class JsonlRecorder:
    """Single-writer log. I/O errors propagate; no silent loss of diagnostics."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._file = None

    def __enter__(self):
        self._file = self.path.open("a", encoding="utf-8")
        return self

    def emit(self, event: dict) -> None:
        if self._file is None:
            raise RuntimeError("Recorder must be opened before use")
        record = {"schema_version": 1, "timestamp": datetime.now(timezone.utc).isoformat(),
                  **bounded(event)}
        self._file.write(json.dumps(record, ensure_ascii=True) + "\n")
        self._file.flush()

    def __exit__(self, *exc):
        self._file.close()
        self._file = None
