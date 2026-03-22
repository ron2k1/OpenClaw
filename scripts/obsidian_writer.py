"""
obsidian_writer.py — Write session logs and update agent memory in the Obsidian vault.

Called automatically by bridge.py after every Claude Code invocation.
Can also be called standalone or by Grok for manual entries.

Usage (automatic via bridge):
    from obsidian_writer import write_session_entry, update_claude_output

Usage (standalone):
    python obsidian_writer.py --task "task" --status "success" --output "what happened"
    python obsidian_writer.py --update-memory --task "task" --status "success"
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECT = Path("C:/Users/ronil/Desktop/OpenClaw")


def get_vault_paths(project_root: Path = None) -> dict:
    root = project_root or DEFAULT_PROJECT
    vault = root / "vault"
    return {
        "session_log": vault / "projects" / "openclaw" / "session_log.md",
        "claude_output": vault / "agent_memory" / "claude_last_output.md",
        "grok_memory": vault / "agent_memory" / "grok_working_memory.md",
        "open_tasks": vault / "projects" / "openclaw" / "open_tasks.md",
    }


def write_session_entry(
    project_root: Path = None,
    task: str = "",
    status: str = "unknown",
    tier: str = "",
    mode: str = "",
    files_changed: list = None,
    claude_output: str = "",
    error: str = None,
    self_heal: dict = None,
    cost_usd: float = 0,
    branch: str = "",
):
    """Append a session entry to the Obsidian session log."""
    paths = get_vault_paths(project_root)
    log_path = paths["session_log"]
    log_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build the entry
    lines = []
    lines.append(f"\n---\n")
    lines.append(f"## {date_str} — {task[:80]}\n")
    lines.append(f"- **Time**: {timestamp}")
    lines.append(f"- **Status**: {status}")
    lines.append(f"- **Tier**: {tier} | **Mode**: {mode}")

    if branch:
        lines.append(f"- **Branch**: `{branch}`")

    if files_changed:
        files_str = ", ".join(f"`{f}`" for f in files_changed[:10])
        lines.append(f"- **Files changed**: {files_str}")

    if cost_usd:
        lines.append(f"- **Cost**: ${cost_usd:.4f}")

    if self_heal:
        attempts = self_heal.get("attempts", 0)
        healed = self_heal.get("healed", False)
        lines.append(f"- **Self-heal**: {'healed' if healed else 'failed'} after {attempts} attempt(s)")

    if error:
        lines.append(f"- **Error**: {error[:200]}")

    # Claude output summary (first 300 chars)
    if claude_output:
        preview = claude_output.strip()[:300]
        # Try to parse JSON output for cleaner summary
        try:
            parsed = json.loads(claude_output)
            preview = parsed.get("result", preview)[:300]
        except (json.JSONDecodeError, TypeError):
            pass
        lines.append(f"\n**Output preview**:\n> {preview}")

    lines.append("")

    # Read existing content, insert new entry after the header
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        # Find the first "---" separator after the header and insert before it
        marker = "\n---\n"
        first_entry = content.find(marker, content.find("---\n") + 4)
        if first_entry != -1:
            # Insert after header section, before first entry
            new_content = content[:first_entry] + "\n".join(lines) + content[first_entry:]
        else:
            # No entries yet, append
            new_content = content.rstrip() + "\n" + "\n".join(lines) + "\n"
    else:
        new_content = "# Session Log\n\nNarrative log of agent sessions. Newest first.\n" + "\n".join(lines) + "\n"

    log_path.write_text(new_content, encoding="utf-8")


def update_claude_output(
    project_root: Path = None,
    task: str = "",
    status: str = "unknown",
    tier: str = "",
    mode: str = "",
    files_changed: list = None,
    error: str = None,
    cost_usd: float = 0,
):
    """Update the claude_last_output.md with latest invocation results."""
    paths = get_vault_paths(project_root)
    output_path = paths["claude_output"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    files_str = ", ".join(files_changed[:10]) if files_changed else "none"

    content = f"""# Claude Last Output

Parsed results of the most recent Claude Code invocation. Updated by bridge.py.

---

## Last Invocation
- **Task**: {task}
- **Status**: {status}
- **Tier**: {tier}
- **Mode**: {mode}
- **Files changed**: {files_str}
- **Cost**: ${cost_usd:.4f}
- **Timestamp**: {timestamp}
"""

    if error:
        content += f"- **Error**: {error[:300]}\n"

    output_path.write_text(content, encoding="utf-8")


def update_grok_memory(
    project_root: Path = None,
    last_task: str = "",
    last_status: str = "",
    next_suggested: str = "",
):
    """Update grok_working_memory.md with latest session context."""
    paths = get_vault_paths(project_root)
    memory_path = paths["grok_memory"]

    if not memory_path.exists():
        return

    content = memory_path.read_text(encoding="utf-8")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Update the Last Session section
    new_last_session = f"""## Last Session
- Date: {date_str}
- Task: {last_task}
- Status: {last_status}"""

    if next_suggested:
        new_last_session += f"\n- Suggested next: {next_suggested}"

    # Replace the Last Session block
    import re
    pattern = r"## Last Session\n(?:- .*\n)*"
    if re.search(pattern, content):
        content = re.sub(pattern, new_last_session + "\n", content)
    else:
        content = content.rstrip() + "\n\n" + new_last_session + "\n"

    memory_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Write to Obsidian vault")
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--task", required=True)
    parser.add_argument("--status", default="unknown")
    parser.add_argument("--tier", default="")
    parser.add_argument("--mode", default="")
    parser.add_argument("--output", default="", help="Claude output text")
    parser.add_argument("--error", default=None)
    parser.add_argument("--cost", type=float, default=0)
    parser.add_argument("--branch", default="")
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--update-memory", action="store_true",
                        help="Also update grok working memory")
    args = parser.parse_args()

    project = Path(args.project)

    write_session_entry(
        project_root=project, task=args.task, status=args.status,
        tier=args.tier, mode=args.mode, files_changed=args.files,
        claude_output=args.output, error=args.error,
        cost_usd=args.cost, branch=args.branch,
    )

    update_claude_output(
        project_root=project, task=args.task, status=args.status,
        tier=args.tier, mode=args.mode, files_changed=args.files,
        error=args.error, cost_usd=args.cost,
    )

    if args.update_memory:
        update_grok_memory(
            project_root=project, last_task=args.task, last_status=args.status,
        )

    print(json.dumps({"success": True, "message": "Obsidian vault updated"}, indent=2))
