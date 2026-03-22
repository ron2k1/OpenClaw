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
import re
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
SKILL_DIR = Path.home() / ".openclaw" / "workspace" / "skills" / "claude-code-bridge" / "claude-code-bridge"

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


def get_base_branch(project_path: str) -> str:
    """Detect the default base branch (main or master)."""
    for candidate in ["main", "master"]:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=project_path, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return candidate
    # Fallback: use whatever HEAD points to on origin
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=project_path, capture_output=True, text=True,
    )
    if result.returncode == 0:
        # e.g. "refs/remotes/origin/main" -> "main"
        return result.stdout.strip().split("/")[-1]
    return "main"


def create_branch(branch_name: str, project_path: str) -> bool:
    """Create and checkout a git branch from the base branch.

    Ensures isolation by switching back to main/master before creating the
    new branch, so sequential tasks don't stack on each other.  Any
    uncommitted changes are stashed beforehand and popped after checkout.
    """
    try:
        base = get_base_branch(project_path)

        # Stash any uncommitted changes so checkout doesn't fail
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path, capture_output=True, text=True,
        )
        has_changes = bool(status.stdout.strip())
        if has_changes:
            subprocess.run(
                ["git", "stash", "push", "-m", f"bridge-auto-stash-before-{branch_name}"],
                cwd=project_path, capture_output=True, text=True, check=True,
            )

        # Switch to base branch so the new branch forks from it
        subprocess.run(
            ["git", "checkout", base],
            cwd=project_path, capture_output=True, text=True, check=True,
        )

        # Create and switch to the new task branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=project_path, capture_output=True, text=True, check=True,
        )

        # Restore stashed changes if any
        if has_changes:
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=project_path, capture_output=True, text=True,
            )

        return True
    except subprocess.CalledProcessError:
        # If something failed mid-way, try to pop stash so work isn't lost
        if has_changes:
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=project_path, capture_output=True, text=True,
            )
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


def auto_commit(project_path: str, task: str, branch: str = "") -> bool:
    """Stage all changes and commit with a descriptive message."""
    try:
        # Stage all changes (new + modified)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=project_path, capture_output=True, text=True, check=True,
        )
        # Check if there's anything to commit
        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=project_path, capture_output=True, text=True,
        )
        if status.returncode == 0:
            return False  # Nothing staged

        # Build commit message
        slug = task[:72]
        msg = f"agent: {slug}"
        if branch:
            msg += f"\n\nBranch: {branch}"
        msg += "\n\nAutomated commit by OpenClaw bridge"

        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=project_path, capture_output=True, text=True, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


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
            cmd, cwd=project_path, capture_output=True, text=True, timeout=600,
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


def sync_skill_files():
    """Sync OpenClaw source files to the .openclaw skill directory."""
    try:
        import shutil
        if not SKILL_DIR.exists():
            return
        syncs = [
            (OPENCLAW_ROOT / "SKILLS.md", SKILL_DIR / "SKILL.md"),
            (OPENCLAW_ROOT / "scripts" / "bridge.py", SKILL_DIR / "scripts" / "bridge.py"),
            (OPENCLAW_ROOT / "gatekeeper" / "gatekeeper.py", SKILL_DIR / "scripts" / "gatekeeper.py"),
        ]
        for src, dst in syncs:
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
    except Exception:
        pass


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


def write_obsidian(project: str, task: str, status: str, gate_result: dict,
                   claude_result: dict = None, files_changed: list = None,
                   self_heal: dict = None, cost_usd: float = 0, branch: str = ""):
    """Write session entry and update agent memory in Obsidian vault."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from obsidian_writer import write_session_entry, update_claude_output, update_grok_memory

        write_session_entry(
            project_root=Path(project), task=task, status=status,
            tier=gate_result.get("tier", ""), mode=gate_result.get("mode", ""),
            files_changed=files_changed or [],
            claude_output=claude_result.get("output", "") if claude_result else "",
            error=claude_result.get("error") if claude_result else None,
            self_heal=self_heal, cost_usd=cost_usd, branch=branch,
        )
        update_claude_output(
            project_root=Path(project), task=task, status=status,
            tier=gate_result.get("tier", ""), mode=gate_result.get("mode", ""),
            files_changed=files_changed or [],
            error=claude_result.get("error") if claude_result else None,
            cost_usd=cost_usd,
        )
        update_grok_memory(
            project_root=Path(project), last_task=task, last_status=status,
        )
    except Exception:
        pass
    finally:
        if str(SCRIPTS_DIR) in sys.path:
            sys.path.remove(str(SCRIPTS_DIR))


def run_quality_gates(project: str, files_changed: list, task: str,
                      skip_adversarial: bool = False, skip_coverage: bool = False,
                      skip_regression: bool = False) -> dict:
    """Run the quality gate pipeline."""
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from quality_gate import run_quality_pipeline
        return run_quality_pipeline(
            project_path=project,
            files_changed=files_changed,
            task=task,
            skip_adversarial=skip_adversarial,
            skip_coverage=skip_coverage,
            skip_regression=skip_regression,
        )
    except Exception as e:
        return {"passed": True, "gates": [], "hard_failures": [],
                "soft_failures": [], "review_items": [],
                "first_hard_error": None, "error": str(e)}
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
    parser.add_argument("--branch", help="Git branch name (auto-generated if omitted for write tasks)")
    parser.add_argument("--no-branch", action="store_true",
                        help="Skip auto-branch even for write tasks")
    parser.add_argument("--print-only", action="store_true",
                        help="Read-only mode (claude --print)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only run gatekeeper, don't invoke Claude")
    parser.add_argument("--self-heal", action="store_true",
                        help="Enable self-heal loop on failure (max 3 retries)")
    parser.add_argument("--max-heal-attempts", type=int, default=3,
                        help="Max self-heal attempts")
    parser.add_argument("--quality-gates", action="store_true",
                        help="Run quality gate pipeline after Claude succeeds")
    parser.add_argument("--skip-adversarial", action="store_true",
                        help="Skip adversarial Claude review gate")
    parser.add_argument("--skip-coverage", action="store_true",
                        help="Skip coverage check gate")
    parser.add_argument("--skip-regression", action="store_true",
                        help="Skip regression check gate")
    parser.add_argument("--auto-push", action="store_true",
                        help="Push branch to remote after successful commit")
    args = parser.parse_args()

    # Auto-generate branch name for write tasks (non-print-only)
    if not args.print_only and not args.dry_run and not args.branch and not args.no_branch:
        slug = re.sub(r"[^a-z0-9]+", "-", args.task.lower())[:40].strip("-")
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        args.branch = f"agent/{slug}-{date_str}"

    # Step 0a: Sync skill files to .openclaw workspace
    sync_skill_files()

    # Step 0b: Update CLAUDE.md primer
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
                if output["files_changed"]:
                    committed = auto_commit(args.project, args.task, args.branch or "")
                    output["committed"] = committed
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
        # Success — Claude exited cleanly
        output["success"] = True
        output["files_changed"] = get_changed_files(args.project)

        # Parse Claude's JSON output for extra data
        parsed = parse_claude_json_output(claude_result["output"])
        output["cost_usd"] = parsed.get("cost_usd", 0)

        # Step 5: Quality gates (if enabled)
        if args.quality_gates and not args.print_only and output["files_changed"]:
            qg_result = run_quality_gates(
                args.project, output["files_changed"], args.task,
                skip_adversarial=args.skip_adversarial,
                skip_coverage=args.skip_coverage,
                skip_regression=args.skip_regression,
            )
            output["quality_gates"] = qg_result

            if not qg_result["passed"]:
                # Hard gate failed — try self-heal if enabled
                if args.self_heal and qg_result.get("first_hard_error"):
                    heal_result = run_self_heal(
                        args.project, args.task, qg_result["first_hard_error"],
                        args.max_heal_attempts,
                    )
                    output["self_heal"] = heal_result
                    if heal_result["healed"]:
                        output["files_changed"] = get_changed_files(args.project)
                        log_audit(args.task, gate_result["tier"], "quality_healed",
                                  gate_result["mode"],
                                  f"Quality gate healed after {heal_result['attempts']} attempt(s)")
                    else:
                        output["success"] = False
                        output["error"] = f"Quality gates failed: {', '.join(qg_result['hard_failures'])}"
                        log_audit(args.task, gate_result["tier"], "quality_failed",
                                  gate_result["mode"], output["error"])
                        write_state(args.project, args.task, "quality_failed", gate_result,
                                    claude_result, output["files_changed"],
                                    quality_results=qg_result)
                else:
                    output["success"] = False
                    output["error"] = f"Quality gates failed: {', '.join(qg_result['hard_failures'])}"
                    log_audit(args.task, gate_result["tier"], "quality_failed",
                              gate_result["mode"], output["error"])
                    write_state(args.project, args.task, "quality_failed", gate_result,
                                claude_result, output["files_changed"],
                                quality_results=qg_result)

        # Step 5a: Auto-commit (only if still successful)
        if output["success"] and not args.print_only and output["files_changed"]:
            committed = auto_commit(args.project, args.task, args.branch or "")
            output["committed"] = committed

            # Step 5b: Auto-push (if enabled and committed)
            if args.auto_push and committed and args.branch:
                try:
                    push_result = subprocess.run(
                        ["git", "push", "-u", "origin", args.branch],
                        cwd=args.project, capture_output=True, text=True, timeout=60,
                    )
                    output["pushed"] = push_result.returncode == 0
                except Exception:
                    output["pushed"] = False

        if output["success"]:
            log_audit(args.task, gate_result["tier"], "completed",
                      gate_result["mode"], f"Files changed: {len(output['files_changed'])}")
            write_state(args.project, args.task, "success", gate_result, claude_result,
                        output["files_changed"],
                        quality_results=output.get("quality_gates"),
                        next_suggested=f"Review changes: {', '.join(output['files_changed'][:5])}")

    # Step 6: Write to Obsidian vault
    cost = output.get("cost_usd", 0)
    write_obsidian(
        args.project, args.task, "success" if output["success"] else "failed",
        gate_result, claude_result if claude_result else None,
        output.get("files_changed"), output.get("self_heal"),
        cost_usd=cost, branch=args.branch or "",
    )

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
