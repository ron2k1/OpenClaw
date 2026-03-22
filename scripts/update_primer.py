"""
update_primer.py — Refresh CLAUDE.md with latest project state before each Claude Code invocation.

Reads from:
  - tasks/state.json          (last task results)
  - git log / git diff        (recent changes)
  - audit_log.jsonl           (recent gatekeeper decisions)
  - errors/build_failures.md  (known pitfalls from self-heal)
  - agent_control/AGENT_MODE  (current mode)

Rewrites the "Current Sprint Context" section of CLAUDE.md.

Usage:
    python update_primer.py [--project PATH] [--task "current task description"]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEFAULT_PROJECT = Path("C:/Users/ronil/Desktop/OpenClaw")


def get_paths(project_root: Path) -> dict:
    return {
        "claude_md": project_root / "CLAUDE.md",
        "state_json": project_root / "tasks" / "state.json",
        "audit_log": project_root / "audit_log.jsonl",
        "build_failures": project_root / "errors" / "build_failures.md",
        "agent_mode": project_root / "agent_control" / "AGENT_MODE",
        "agent_enabled": project_root / "agent_control" / "AGENT_ENABLED",
    }


# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------

def read_state_json(path: Path) -> dict:
    """Read the last task state."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_git_log(project_root: Path, count: int = 5) -> str:
    """Get recent git log entries."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{count}"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "No git history available"
    except Exception:
        return "Git not available"


def get_git_status(project_root: Path) -> str:
    """Get current git status summary."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.strip()
        if not output:
            return "Clean working tree"
        lines = output.split("\n")
        return f"{len(lines)} changed file(s)"
    except Exception:
        return "Git not available"


def get_git_branch(project_root: Path) -> str:
    """Get current git branch."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or "detached HEAD"
    except Exception:
        return "unknown"


def get_recent_audit_entries(path: Path, count: int = 5) -> list:
    """Read last N audit log entries."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        recent = lines[-count:] if len(lines) > count else lines
        entries = []
        for line in recent:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries
    except OSError:
        return []


def get_build_failures(path: Path) -> str:
    """Read known pitfalls from build failures log."""
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        # Take last 500 chars to keep primer concise
        if len(content) > 500:
            content = "...\n" + content[-500:]
        return content
    except OSError:
        return ""


def get_agent_mode(path: Path) -> str:
    """Read current agent mode."""
    if not path.exists():
        return "safe (default)"
    try:
        mode = path.read_text(encoding="utf-8").strip().lower()
        return mode if mode in ("safe", "supervised", "autonomous") else "safe (invalid, defaulted)"
    except OSError:
        return "safe (unreadable)"


def is_agent_enabled(path: Path) -> bool:
    """Check kill switch."""
    return path.exists()


# ---------------------------------------------------------------------------
# Primer builder
# ---------------------------------------------------------------------------

def build_sprint_context(project_root: Path, current_task: str = None) -> str:
    """Build the dynamic sprint context section."""
    paths = get_paths(project_root)

    # Collect data
    state = read_state_json(paths["state_json"])
    git_log = get_git_log(project_root)
    git_status = get_git_status(project_root)
    git_branch = get_git_branch(project_root)
    audit_entries = get_recent_audit_entries(paths["audit_log"])
    build_failures = get_build_failures(paths["build_failures"])
    agent_mode = get_agent_mode(paths["agent_mode"])
    enabled = is_agent_enabled(paths["agent_enabled"])

    # Build context
    lines = []
    lines.append("## Current Sprint Context")
    lines.append(f"<!-- Auto-generated by update_primer.py at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} -->")
    lines.append("")

    # Agent status
    lines.append(f"- **Agent status**: {'ENABLED' if enabled else 'DISABLED (kill switch active)'}")
    lines.append(f"- **Agent mode**: {agent_mode}")
    lines.append(f"- **Branch**: {git_branch}")
    lines.append(f"- **Working tree**: {git_status}")

    # Current task
    if current_task:
        lines.append(f"- **Current task**: {current_task}")

    # Last task from state.json
    if state:
        last_task = state.get("last_task", "")
        last_status = state.get("status", "")
        if last_task:
            lines.append(f"- **Last task**: {last_task}")
            lines.append(f"- **Last status**: {last_status}")
        files_changed = state.get("files_changed", [])
        if files_changed:
            lines.append(f"- **Last files changed**: {', '.join(files_changed[:10])}")
        next_suggested = state.get("next_suggested", "")
        if next_suggested:
            lines.append(f"- **Suggested next**: {next_suggested}")

    # Recent git history
    lines.append("")
    lines.append("### Recent Git History")
    lines.append("```")
    lines.append(git_log)
    lines.append("```")

    # Recent audit trail
    if audit_entries:
        lines.append("")
        lines.append("### Recent Gatekeeper Decisions")
        for entry in audit_entries[-3:]:
            ts = entry.get("timestamp", "?")[:19]
            task = entry.get("task", "?")[:60]
            tier = entry.get("tier", "?")
            decision = entry.get("decision", "?")
            lines.append(f"- `{ts}` | **{tier}** | {decision} | {task}")

    # Known pitfalls
    if build_failures:
        lines.append("")
        lines.append("### Known Pitfalls (from build failures)")
        lines.append("These errors have occurred before. Avoid repeating them:")
        lines.append("")
        lines.append(build_failures)

    return "\n".join(lines)


def update_claude_md(project_root: Path, current_task: str = None) -> dict:
    """Rewrite the Current Sprint Context section in CLAUDE.md."""
    paths = get_paths(project_root)
    claude_md = paths["claude_md"]

    if not claude_md.exists():
        return {
            "success": False,
            "error": f"CLAUDE.md not found at {claude_md}",
        }

    content = claude_md.read_text(encoding="utf-8")
    new_context = build_sprint_context(project_root, current_task)

    # Replace everything from "## Current Sprint Context" to end of file
    # or to the next ## heading
    pattern = r"## Current Sprint Context.*"
    if re.search(pattern, content, re.DOTALL):
        updated = re.sub(pattern, new_context, content, flags=re.DOTALL)
    else:
        # No existing section — append it
        updated = content.rstrip() + "\n\n" + new_context + "\n"

    claude_md.write_text(updated, encoding="utf-8")

    return {
        "success": True,
        "claude_md": str(claude_md),
        "context_lines": len(new_context.split("\n")),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Update CLAUDE.md primer with latest project state")
    parser.add_argument("--project", default=str(DEFAULT_PROJECT), help="Project root directory")
    parser.add_argument("--task", default=None, help="Current task description to inject")
    parser.add_argument("--preview", action="store_true", help="Print the context without writing")
    args = parser.parse_args()

    project_root = Path(args.project)

    if args.preview:
        context = build_sprint_context(project_root, args.task)
        print(context)
        return

    result = update_claude_md(project_root, args.task)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
