"""
Event Bus — structured real-time event emission.

Every call to emit() does two things:
  1. Prints EVENT:<json> to stdout so Node.js SSE can pick it up immediately.
  2. Appends the event to storage/events.jsonl for persistence.

write_state() overwrites a named JSON file in storage/ so the Node.js API
can serve the latest snapshot without streaming.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_STORAGE = Path(__file__).parent.parent / "storage"
_EVENTS_FILE = _STORAGE / "events.jsonl"
_MAX_EVENTS = 500


def _ensure_storage() -> None:
    _STORAGE.mkdir(parents=True, exist_ok=True)


def emit(event_type: str, data: dict) -> None:
    """Emit a structured event to stdout (for SSE) and append to events.jsonl."""
    record = {
        "ts": int(time.time() * 1000),
        "type": event_type,
        "data": data,
    }
    line = json.dumps(record, default=str)

    # Stdout — Node.js handleLine() detects EVENT: prefix
    sys.stdout.write(f"EVENT:{line}\n")
    sys.stdout.flush()

    # Persist to jsonl
    try:
        _ensure_storage()
        with open(_EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        # Trim to last _MAX_EVENTS lines periodically
        _trim_events()
    except Exception:
        pass


def write_state(state_name: str, data: dict | list) -> None:
    """Overwrite a named state snapshot file in storage/."""
    try:
        _ensure_storage()
        path = _STORAGE / f"{state_name}.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str, indent=2)
        tmp.replace(path)
    except Exception:
        pass


def read_state(state_name: str, default=None):
    """Read a named state snapshot file from storage/."""
    try:
        path = _STORAGE / f"{state_name}.json"
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def read_events(limit: int = 200) -> list[dict]:
    """Read the last `limit` events from events.jsonl."""
    try:
        if not _EVENTS_FILE.exists():
            return []
        lines = _EVENTS_FILE.read_text(encoding="utf-8").strip().split("\n")
        result = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
    except Exception:
        return []


def _trim_events() -> None:
    try:
        if not _EVENTS_FILE.exists():
            return
        lines = _EVENTS_FILE.read_text(encoding="utf-8").strip().split("\n")
        if len(lines) > _MAX_EVENTS:
            _EVENTS_FILE.write_text("\n".join(lines[-_MAX_EVENTS:]) + "\n", encoding="utf-8")
    except Exception:
        pass
