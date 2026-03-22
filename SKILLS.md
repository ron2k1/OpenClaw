---
name: claude-code-bridge
description: Delegate coding tasks to Claude Code CLI directly (no ACP/acpx). Enforces gatekeeper.py permissions before every invocation. Use when user says "have Claude Code do X", "delegate to Claude", "run this through Claude Code", or any task requiring Claude Code execution with security gating.
user-invocable: false
metadata: {"openclaw":{"requires":{"bins":["python","claude"]}}}
---

# Claude Code Bridge

Secure bridge to delegate coding tasks to Claude Code CLI with gatekeeper permission enforcement. Bypasses ACP/acpx — spawns `claude.exe` directly as a subprocess.

## Quick Start

Run the bridge script with `exec`:

```bash
python "C:/Users/ronil/.openclaw/workspace/skills/claude-code-bridge/claude-code-bridge/scripts/bridge.py" --task "your task description here" --project "C:/Users/ronil/Desktop/OpenClaw"
```

The script handles everything: gatekeeper check, Claude Code invocation, output capture, and audit logging.

## Workflow

### Step 1: Run the bridge

Always use `exec` to run `bridge.py`. Required arguments:

| Arg | Description |
|-----|-------------|
| `--task` | The task description (what Claude Code should do) |
| `--project` | Project directory (default: `C:/Users/ronil/Desktop/OpenClaw`) |
| `--mode` | Override agent mode: `safe`, `supervised`, `autonomous` (reads from AGENT_MODE file if omitted) |
| `--branch` | Create a git branch before execution (e.g., `agent/feature-name-20260321`) |
| `--print-only` | Use `claude --print` (no file writes, read-only) instead of `claude --yes` |

### Step 2: Read the output

The bridge returns JSON to stdout:

```json
{
  "success": true,
  "gatekeeper": {
    "tier": "ALLOWED",
    "decision": "proceed",
    "mode": "supervised"
  },
  "claude_output": "... full Claude Code response ...",
  "exit_code": 0,
  "files_changed": ["list", "of", "changed", "files"],
  "error": null
}
```

### Step 3: Handle results

- If `success: true`: Report Claude Code's output to the user. Summarize what changed.
- If `success: false` and `gatekeeper.decision` is `"needs_approval"`: Tell the user to approve via `python gatekeeper.py --approve` then retry.
- If `success: false` and `gatekeeper.decision` is `"blocked"`: Inform the user the task is blocked and why.
- If `success: false` and `error` is set: Report the error.

## Examples

### Safe read-only task
```bash
python scripts/bridge.py --task "read file src/main.rs and explain the architecture" --print-only
```

### Code modification task
```bash
python scripts/bridge.py --task "add error handling to the parse_config function in src/config.rs" --project "C:/Users/ronil/Desktop/OpenClaw" --branch "agent/add-error-handling-20260321"
```

### Check gatekeeper only (no Claude invocation)
```bash
python scripts/bridge.py --task "delete all test files" --dry-run
```

## Primer Auto-Update

The bridge automatically runs `update_primer.py` before every Claude Code invocation. This refreshes CLAUDE.md with:
- Current agent status and mode
- Git branch and working tree state
- Recent gatekeeper decisions from audit log
- Last task results from state.json
- Known pitfalls from build_failures.md

You can also run the primer update standalone:
```bash
python "C:/Users/ronil/Desktop/OpenClaw/scripts/update_primer.py" --task "description" --preview
```

## Important Rules

1. **Always run through bridge.py** — never call `claude` directly. The bridge enforces gatekeeper permissions and runs the primer update.
2. **Use `--print-only` for read/analysis tasks** — this prevents Claude from modifying files.
3. **Use `--branch` for modification tasks** — keeps changes isolated on a separate git branch.
4. **If gatekeeper blocks**: Do not circumvent. Report to user with the reason.
5. **Audit trail**: Every invocation is logged to `audit_log.jsonl` automatically.

## Gatekeeper Tiers

| Tier | Examples | Behavior |
|------|----------|----------|
| ALLOWED | read file, cargo check, create file | Auto-proceeds |
| SENSITIVE | delete, git push, modify core | Needs human approval in safe/supervised mode |
| BLOCKED | force push, rm -rf /, curl\|bash | Always rejected |

## File Locations

- Bridge script: `C:/Users/ronil/.openclaw/workspace/skills/claude-code-bridge/claude-code-bridge/scripts/bridge.py`
- Gatekeeper: `C:/Users/ronil/Desktop/OpenClaw/gatekeeper/gatekeeper.py`
- Audit log: `C:/Users/ronil/Desktop/OpenClaw/audit_log.jsonl`
- Pending approval: `C:/Users/ronil/Desktop/OpenClaw/tasks/pending_approval.json`
- Kill switch: `C:/Users/ronil/Desktop/OpenClaw/agent_control/AGENT_ENABLED`
- Mode file: `C:/Users/ronil/Desktop/OpenClaw/agent_control/AGENT_MODE`
