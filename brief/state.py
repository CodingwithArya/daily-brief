"""Persistent state (seen items, curriculum pointers, resolved feed URLs) in one JSON file."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

STATE_FILE = Path("state.json")


def load(path: Path = STATE_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save(state: dict, path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, indent=1, sort_keys=True, default=str))


def prune_seen(state: dict, now: datetime, keep_days: int = 21) -> dict:
    """seen = {item_id: 'YYYY-MM-DD'} — anything shown in the last `keep_days` is suppressed."""
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    seen = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    state["seen"] = seen
    return seen


def mark_seen(state: dict, ids, date: str) -> None:
    seen = state.setdefault("seen", {})
    for i in ids:
        seen.setdefault(i, date)
