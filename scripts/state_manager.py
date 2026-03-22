"""
state_manager.py — Structured post-invocation state persistence.

Writes tasks/state.json after every Claude Code invocation.
Read by update_primer.py to inject context into CLAUDE.md.
Read by Grok to understand what happened last session.

Usage (called automatically by bridge.py):
    from state_manager import write_state, read_state

    write_state(project, task="build auth", status="success",
                files_changed=["src/auth.rs"], claude_output="...",
                next_suggested="add tests for auth module")
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECT = Path("C:/Users/ronil/Desktop/OpenClaw")


def get_state_path(project_root: Path = None) -> Path:
    root = project_root or DEFAULT_PROJECT
    return root / "tasks" / "state.json"


def read_state(project_root: Path = None) -> dict:
    """Read the current state.json."""
    path = get_state_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_state(
    project_root: Path = None,
    task: str = "",
    status: str = "unknown",
    tier: str = "",
    decision: str = "",
    mode: str = "",
    files_changed: list = None,
    claude_output: str = "",
    exit_code: int = None,
    error: str = None,
    next_suggested: str = "",
    self_heal_attempts: int = 0,
    quality_results: dict = None,
) -> dict:
    """Write structured state to tasks/state.json."""
    path = get_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Read previous state to preserve history
    prev = read_state(project_root)
    prev_history = prev.get("history", [])

    # Build current state
    state = {
        "last_task": task,
        "status": status,
        "tier": tier,
        "decision": decision,
        "mode": mode,
        "files_changed": files_changed or [],
        "exit_code": exit_code,
        "error": error,
        "next_suggested": next_suggested,
        "self_heal_attempts": self_heal_attempts,
        "quality_results": quality_results or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "claude_output_preview": claude_output[:500] if claude_output else "",
    }

    # Keep last 10 entries in history
    history_entry = {
        "task": task,
        "status": status,
        "timestamp": state["timestamp"],
    }
    prev_history.append(history_entry)
    if len(prev_history) > 10:
        prev_history = prev_history[-10:]

    state["history"] = prev_history

    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def update_state_field(project_root: Path = None, **kwargs) -> dict:
    """Update specific fields in state.json without overwriting everything."""
    state = read_state(project_root)
    state.update(kwargs)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()

    path = get_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Read or write state.json")
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--read", action="store_true", help="Print current state")
    parser.add_argument("--task", default="")
    parser.add_argument("--status", default="unknown")
    parser.add_argument("--next", default="", dest="next_suggested")
    args = parser.parse_args()

    project = Path(args.project)

    if args.read:
        state = read_state(project)
        print(json.dumps(state, indent=2) if state else "{}")
    elif args.task:
        state = write_state(project, task=args.task, status=args.status,
                            next_suggested=args.next_suggested)
        print(json.dumps(state, indent=2))
    else:
        print("Use --read to view state or --task to write state")
