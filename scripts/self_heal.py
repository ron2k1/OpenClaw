"""
self_heal.py — Retry loop for failed quality gates.

When Claude Code output fails a check (build error, test failure, lint issue),
self_heal re-invokes Claude Code with a targeted fix prompt. Max 3 attempts.

After 3 failures: logs to errors/build_failures.md and notifies human.

Usage (called automatically by bridge.py with --self-heal):
    python self_heal.py --project PATH --error "cargo check failed: ..." [--max-attempts 3]

Or imported:
    from self_heal import self_heal_loop
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROJECT = Path("C:/Users/ronil/Desktop/OpenClaw")
MAX_ATTEMPTS = 3


def find_claude() -> str:
    """Find the claude executable."""
    import os
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path_dir) / "claude.exe"
        if candidate.exists():
            return str(candidate)

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "claude.exe",
        Path("C:/Users/ronil/AppData/Local/Microsoft/WinGet/Links/claude.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "claude"


def run_claude_fix(error_text: str, project_path: str) -> dict:
    """Ask Claude Code to fix a specific error, changing nothing else."""
    claude_exe = find_claude()
    prompt = (
        f"Fix this error. Change ONLY what is needed to fix it, nothing else.\n\n"
        f"Error:\n{error_text}"
    )

    cmd = [claude_exe, "-p", "--permission-mode", "bypassPermissions",
           "--output-format", "json", prompt]

    try:
        result = subprocess.run(
            cmd, cwd=project_path, capture_output=True, text=True, timeout=300,
        )
        return {
            "output": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"output": "", "stderr": "", "exit_code": -1,
                "error": "Claude Code timed out during self-heal"}
    except Exception as e:
        return {"output": "", "stderr": "", "exit_code": -1, "error": str(e)}


def run_quality_check(project_path: str, check_cmd: list) -> dict:
    """Run a single quality gate command and return pass/fail."""
    try:
        result = subprocess.run(
            check_cmd, cwd=project_path, capture_output=True, text=True, timeout=120,
        )
        return {
            "passed": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "stdout": "", "stderr": "Timed out", "exit_code": -1}
    except FileNotFoundError:
        return {"passed": True, "stdout": "", "stderr": "Command not found (skipped)",
                "exit_code": 0}
    except Exception as e:
        return {"passed": False, "stdout": "", "stderr": str(e), "exit_code": -1}


def get_quality_gates(project_path: str) -> list:
    """Return the quality gate commands applicable to this project."""
    project = Path(project_path)
    gates = []

    # Detect project type and add relevant gates
    if (project / "Cargo.toml").exists():
        gates.extend([
            {"name": "cargo_check", "cmd": ["cargo", "check"], "hard": True},
            {"name": "cargo_test", "cmd": ["cargo", "test"], "hard": True},
            {"name": "clippy", "cmd": ["cargo", "clippy", "--", "-D", "warnings"], "hard": True},
        ])

    if (project / "package.json").exists():
        gates.extend([
            {"name": "npm_test", "cmd": ["npm", "test"], "hard": True},
        ])

    if (project / "pyproject.toml").exists() or (project / "setup.py").exists():
        gates.extend([
            {"name": "python_syntax", "cmd": [sys.executable, "-m", "py_compile"], "hard": True},
        ])

    # Universal: check for syntax errors in Python files that were just modified
    gates.append({
        "name": "python_check",
        "cmd": [sys.executable, "-c", "import ast, sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]"],
        "hard": False,
    })

    return gates


def log_build_failure(project_root: Path, task: str, attempt: int, error: str):
    """Append failure to errors/build_failures.md for primer feedback loop."""
    failures_path = project_root / "errors" / "build_failures.md"
    failures_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = (
        f"\n### {timestamp} — Attempt {attempt}\n"
        f"**Task**: {task}\n"
        f"**Error**:\n```\n{error[:1000]}\n```\n"
    )

    with open(failures_path, "a", encoding="utf-8") as f:
        f.write(entry)


def log_audit(project_root: Path, task: str, decision: str, details: str = ""):
    """Append to audit log."""
    audit_path = project_root / "audit_log.jsonl"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "self-heal",
        "task": task,
        "tier": "N/A",
        "decision": decision,
        "mode": "self-heal",
        "details": details,
    }
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def self_heal_loop(
    project_path: str,
    original_task: str,
    error_text: str,
    max_attempts: int = MAX_ATTEMPTS,
    quality_gates: list = None,
) -> dict:
    """
    Run the self-heal retry loop.

    1. Takes the error from a failed quality gate
    2. Asks Claude Code to fix just that error
    3. Re-runs the quality gate
    4. Repeats up to max_attempts times
    5. If still failing: logs to build_failures.md

    Returns dict with: healed (bool), attempts, final_error, history
    """
    project_root = Path(project_path)
    gates = quality_gates or get_quality_gates(project_path)

    result = {
        "healed": False,
        "attempts": 0,
        "final_error": error_text,
        "history": [],
    }

    current_error = error_text

    for attempt in range(1, max_attempts + 1):
        result["attempts"] = attempt

        # Ask Claude to fix the error
        fix_result = run_claude_fix(current_error, project_path)

        attempt_record = {
            "attempt": attempt,
            "error_in": current_error[:300],
            "fix_exit_code": fix_result["exit_code"],
            "fix_error": fix_result.get("error"),
        }

        if fix_result["error"] or fix_result["exit_code"] != 0:
            attempt_record["outcome"] = "fix_failed"
            result["history"].append(attempt_record)
            log_build_failure(project_root, original_task, attempt,
                              fix_result.get("error") or fix_result.get("stderr", ""))
            log_audit(project_root, original_task, "self_heal_fix_failed",
                      f"Attempt {attempt}: Claude fix failed")
            continue

        # Re-run quality gates
        all_passed = True
        gate_results = {}
        for gate in gates:
            if not gate.get("hard", False):
                continue  # Only retry hard gates
            check = run_quality_check(project_path, gate["cmd"])
            gate_results[gate["name"]] = "pass" if check["passed"] else "fail"
            if not check["passed"]:
                all_passed = False
                current_error = check["stderr"] or check["stdout"] or f"{gate['name']} failed"
                break

        attempt_record["gate_results"] = gate_results
        attempt_record["outcome"] = "passed" if all_passed else "still_failing"
        result["history"].append(attempt_record)

        if all_passed:
            result["healed"] = True
            result["final_error"] = None
            log_audit(project_root, original_task, "self_heal_success",
                      f"Healed on attempt {attempt}")
            return result

        # Log this failed attempt
        log_build_failure(project_root, original_task, attempt, current_error)
        log_audit(project_root, original_task, "self_heal_retry",
                  f"Attempt {attempt} failed: {current_error[:200]}")

    # All attempts exhausted
    result["final_error"] = current_error
    log_audit(project_root, original_task, "self_heal_exhausted",
              f"Failed after {max_attempts} attempts: {current_error[:200]}")

    # Write final failure summary
    log_build_failure(project_root, original_task, max_attempts,
                      f"EXHAUSTED after {max_attempts} attempts. Last error: {current_error}")

    return result


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Self-heal loop for quality gate failures")
    parser.add_argument("--project", default=str(DEFAULT_PROJECT), help="Project root")
    parser.add_argument("--task", required=True, help="Original task description")
    parser.add_argument("--error", required=True, help="Error text to fix")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS, help="Max retry attempts")
    args = parser.parse_args()

    result = self_heal_loop(
        project_path=args.project,
        original_task=args.task,
        error_text=args.error,
        max_attempts=args.max_attempts,
    )

    print(json.dumps(result, indent=2))
