"""
ClaudeCodeBridge — Direct CLI bridge to Claude Code with gatekeeper enforcement.

Bypasses ACP/acpx entirely. Spawns claude.exe as a subprocess.
Integrates: primer update, gatekeeper, Claude Code, state.json, self-heal.

Usage:
    python bridge.py --task "task description" [--project PATH] [--mode MODE]
                     [--branch NAME] [--print-only] [--dry-run] [--self-heal]
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
OPENCLAW_ROOT = Path("C:/Users/ronil/Desktop/OpenClaw")
GATEKEEPER = OPENCLAW_ROOT / "gatekeeper" / "gatekeeper.py"
AUDIT_LOG = OPENCLAW_ROOT / "audit_log.jsonl"
SCRIPTS_DIR = OPENCLAW_ROOT / "scripts"

# Claude Code executable — try common locations
CLAUDE_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "claude.exe",
    Path("C:/Users/ronil/AppData/Local/Microsoft/WinGet/Links/claude.exe"),
]


def find_claude() -> str:
    """Find the claude executable."""
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path_dir) / "claude.exe"
        if candidate.exists():
            return str(candidate)
    for candidate in CLAUDE_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return "claude"


def run_gatekeeper(task: str, mode: str = None) -> dict:
    """Run gatekeeper.py and return the classification result."""
    if not GATEKEEPER.exists():
        return {
            "allowed": False, "tier": "N/A", "decision": "error",
            "mode": mode or "unknown",
            "reason": f"Gatekeeper not found at {GATEKEEPER}",
        }

    sys.path.insert(0, str(GATEKEEPER.parent))
    try:
        import gatekeeper as gk
        if mode:
            gk.AGENT_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
            gk.AGENT_MODE_FILE.write_text(mode)
        return gk.gate(task)
    except Exception as e:
        return {
            "allowed": False, "tier": "N/A", "decision": "error",
            "mode": mode or "unknown",
            "reason": f"Gatekeeper error: {str(e)}",
        }
    finally:
        sys.path.pop(0)


def create_branch(branch_name: str, project_path: str) -> bool:
    """Create and checkout a git branch."""
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=project_path, capture_output=True, text=True, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def get_changed_files(project_path: str) -> list:
    """Get list of changed files from git."""
    try:
        # Check both staged and unstaged, plus untracked
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path, capture_output=True, text=True,
        )
        files = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                # git status --porcelain format: "XY filename"
                filename = line[3:].strip()
                if filename:
                    files.append(filename)
        return files
    except Exception:
        return []


def run_claude(task: str, project_path: str, print_only: bool = False) -> dict:
    """Invoke Claude Code CLI and capture output."""
    claude_exe = find_claude()

    if print_only:
        cmd = [claude_exe, "--print", task]
    else:
        cmd = [claude_exe, "-p", "--permission-mode", "bypassPermissions",
               "--output-format", "json", task]

    try:
        result = subprocess.run(
            cmd, cwd=project_path, capture_output=True, text=True, timeout=300,
        )
        return {
            "output": result.stdout, "stderr": result.stderr,
            "exit_code": result.returncode, "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"output": "", "stderr": "", "exit_code": -1,
                "error": "Claude Code timed out after 5 minutes"}
    except FileNotFoundError:
        return {"output": "", "stderr": "", "exit_code": -1,
                "error": f"Claude Code not found. Tried: {claude_exe}"}
    except Exception as e:
        return {"output": "", "stderr": "", "exit_code": -1, "error": str(e)}


def parse_claude_json_output(raw_output: str) -> dict:
    """Parse Claude Code JSON output to extract result and cost."""
    try:
        data = json.loads(raw_output)
        return {
            "result_text": data.get("result", raw_output),
            "cost_usd": data.get("total_cost_usd", 0),
            "session_id": data.get("session_id", ""),
            "num_turns": data.get("num_turns", 0),
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "result_text": raw_output,
            "cost_usd": 0,
            "session_id": "",
            "num_turns": 0,
        }


def log_audit(task: str, tier: str, decision: str, mode: str, details: str = ""):
    """Append to audit log."""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "claude-code-bridge",
        "task": task, "tier": tier, "decision": decision,
        "mode": mode, "details": details,
    }
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def update_primer(project: str, task: str):
    """Run update_primer.py to refresh CLAUDE.md."""
    try:
        primer_script = SCRIPTS_DIR / "update_primer.py"
        if primer_script.exists():
            subprocess.run(
                [sys.executable, str(primer_script), "--project", project, "--task", task],
                capture_output=True, text=True, timeout=15,
            )
    except Exception:
        pass


def write_state(project: str, task: str, status: str, gate_result: dict,
                claude_result: dict = None, files_changed: list = None,
                self_heal_attempts: int = 0, quality_results: dict = None,
                next_suggested: str = ""):
    """Write state.json via state_manager."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from state_manager import write_state as _write_state
        _write_state(
            project_root=Path(project),
            task=task,
            status=status,
            tier=gate_result.get("tier", ""),
            decision=gate_result.get("decision", ""),
            mode=gate_result.get("mode", ""),
            files_changed=files_changed or [],
            claude_output=claude_result.get("output", "") if claude_result else "",
            exit_code=claude_result.get("exit_code") if claude_result else None,
            error=claude_result.get("error") if claude_result else None,
            next_suggested=next_suggested,
            self_heal_attempts=self_heal_attempts,
            quality_results=quality_results or {},
        )
    except Exception:
        pass
    finally:
        if str(SCRIPTS_DIR) in sys.path:
            sys.path.remove(str(SCRIPTS_DIR))


def run_self_heal(project: str, task: str, error_text: str, max_attempts: int = 3) -> dict:
    """Run the self-heal loop."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from self_heal import self_heal_loop
        return self_heal_loop(
            project_path=project,
            original_task=task,
            error_text=error_text,
            max_attempts=max_attempts,
        )
    except Exception as e:
        return {"healed": False, "attempts": 0, "final_error": str(e), "history": []}
    finally:
        if str(SCRIPTS_DIR) in sys.path:
            sys.path.remove(str(SCRIPTS_DIR))


def main():
    parser = argparse.ArgumentParser(description="ClaudeCodeBridge — secure Claude Code invocation")
    parser.add_argument("--task", required=True, help="Task description for Claude Code")
    parser.add_argument("--project", default=str(OPENCLAW_ROOT), help="Project directory")
    parser.add_argument("--mode", choices=["safe", "supervised", "autonomous"],
                        help="Override agent mode")
    parser.add_argument("--branch", help="Git branch name to create before execution")
    parser.add_argument("--print-only", action="store_true",
                        help="Read-only mode (claude --print)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only run gatekeeper, don't invoke Claude")
    parser.add_argument("--self-heal", action="store_true",
                        help="Enable self-heal loop on failure (max 3 retries)")
    parser.add_argument("--max-heal-attempts", type=int, default=3,
                        help="Max self-heal attempts")
    args = parser.parse_args()

    # Step 0: Update CLAUDE.md primer
    update_primer(args.project, args.task)

    # Step 1: Gatekeeper check
    gate_result = run_gatekeeper(args.task, args.mode)

    output = {
        "success": False,
        "gatekeeper": {
            "tier": gate_result.get("tier", "N/A"),
            "decision": gate_result.get("decision", "error"),
            "mode": gate_result.get("mode", "unknown"),
            "reason": gate_result.get("reason", ""),
        },
        "claude_output": None,
        "exit_code": None,
        "files_changed": [],
        "self_heal": None,
        "error": None,
    }

    # Dry run
    if args.dry_run:
        output["success"] = gate_result.get("allowed", False)
        print(json.dumps(output, indent=2))
        return

    # Step 2: Check if allowed
    if not gate_result.get("allowed", False):
        decision = gate_result.get("decision", "")
        if decision == "needs_approval":
            output["error"] = "Task requires human approval. Run: python gatekeeper.py --approve"
        elif decision == "blocked":
            output["error"] = f"Task blocked: {gate_result.get('reason', 'security policy')}"
        elif decision == "killed":
            output["error"] = "Agent is disabled. Run: python gatekeeper.py --enable"
        else:
            output["error"] = gate_result.get("reason", "Task not allowed")

        log_audit(args.task, gate_result.get("tier", "N/A"), decision,
                  gate_result.get("mode", "unknown"), output["error"])
        write_state(args.project, args.task, "blocked", gate_result)
        print(json.dumps(output, indent=2))
        return

    # Step 3: Create branch if requested
    if args.branch:
        if not create_branch(args.branch, args.project):
            output["error"] = f"Failed to create branch: {args.branch}"
            log_audit(args.task, gate_result["tier"], "branch_failed",
                      gate_result["mode"], output["error"])
            write_state(args.project, args.task, "branch_failed", gate_result)
            print(json.dumps(output, indent=2))
            return

    # Step 4: Run Claude Code
    claude_result = run_claude(args.task, args.project, args.print_only)

    output["claude_output"] = claude_result["output"]
    output["exit_code"] = claude_result["exit_code"]

    if claude_result["error"]:
        output["error"] = claude_result["error"]
        log_audit(args.task, gate_result["tier"], "claude_error",
                  gate_result["mode"], claude_result["error"])
        write_state(args.project, args.task, "claude_error", gate_result, claude_result)

    elif claude_result["exit_code"] != 0:
        error_text = claude_result["stderr"] or f"Exit code {claude_result['exit_code']}"
        output["error"] = f"Claude Code exited with code {claude_result['exit_code']}"
        if claude_result["stderr"]:
            output["error"] += f": {claude_result['stderr'][:500]}"

        # Step 5: Self-heal if enabled and Claude failed
        if args.self_heal:
            heal_result = run_self_heal(
                args.project, args.task, error_text, args.max_heal_attempts,
            )
            output["self_heal"] = heal_result

            if heal_result["healed"]:
                output["success"] = True
                output["error"] = None
                output["files_changed"] = get_changed_files(args.project)
                log_audit(args.task, gate_result["tier"], "self_healed",
                          gate_result["mode"],
                          f"Healed after {heal_result['attempts']} attempt(s)")
                write_state(args.project, args.task, "self_healed", gate_result,
                            claude_result, output["files_changed"],
                            self_heal_attempts=heal_result["attempts"])
            else:
                log_audit(args.task, gate_result["tier"], "self_heal_failed",
                          gate_result["mode"],
                          f"Failed after {heal_result['attempts']} attempt(s)")
                write_state(args.project, args.task, "self_heal_failed", gate_result,
                            claude_result, self_heal_attempts=heal_result["attempts"],
                            error=output["error"])
        else:
            log_audit(args.task, gate_result["tier"], "claude_failed",
                      gate_result["mode"], output["error"])
            write_state(args.project, args.task, "failed", gate_result, claude_result,
                        error=output["error"])

    else:
        # Success
        output["success"] = True
        output["files_changed"] = get_changed_files(args.project)

        # Parse Claude's JSON output for extra data
        parsed = parse_claude_json_output(claude_result["output"])
        output["cost_usd"] = parsed.get("cost_usd", 0)

        log_audit(args.task, gate_result["tier"], "completed",
                  gate_result["mode"], f"Files changed: {len(output['files_changed'])}")
        write_state(args.project, args.task, "success", gate_result, claude_result,
                    output["files_changed"],
                    next_suggested=f"Review changes: {', '.join(output['files_changed'][:5])}")

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
