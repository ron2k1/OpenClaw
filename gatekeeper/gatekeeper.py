"""
OpenClaw Gatekeeper — Permission classification and enforcement layer.

Classifies task intents into three tiers:
  - ALLOWED: auto-approved, no human needed
  - SENSITIVE: writes pending_approval.json, halts until approved
  - BLOCKED: rejected immediately, logged, human notified

Reads AGENT_ENABLED (kill switch) and AGENT_MODE (safe/supervised/autonomous).
Every decision is logged to audit_log.jsonl.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — all relative to the OpenClaw project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # Desktop/OpenClaw
AGENT_CONTROL = PROJECT_ROOT / "agent_control"
AGENT_ENABLED_FILE = AGENT_CONTROL / "AGENT_ENABLED"
AGENT_MODE_FILE = AGENT_CONTROL / "AGENT_MODE"
AUDIT_LOG = PROJECT_ROOT / "audit_log.jsonl"
PENDING_APPROVAL = PROJECT_ROOT / "tasks" / "pending_approval.json"

# ---------------------------------------------------------------------------
# Permission tier definitions
# ---------------------------------------------------------------------------

ALLOWED_PATTERNS = [
    r"create\s+.*file",
    r"read\s+file",
    r"run\s+cargo\s+check",
    r"run\s+cargo\s+test",
    r"add\s+dependency",
    r"add\s+.*comment",
    r"add\s+.*docstring",
    r"add\s+.*import",
    r"add\s+.*type\s+hint",
    r"edit\s+file",
    r"update\s+file",
    r"write\s+.*to\s+",
    r"format\s+code",
    r"refactor\b",
    r"rename\b",
    r"fix\s+",
    r"implement\b",
    r"build\s+",
    r"cargo\s+check",
    r"cargo\s+test",
    r"cargo\s+build",
    r"npm\s+test",
    r"npm\s+run\b",
    r"python\s+.*\.py",
    r"list\s+files",
    r"explain\b",
    r"review\b",
    r"analyze\b",
    r"search\b",
    r"find\b",
    r"show\s+",
    r"print\s+",
    r"echo\s+",
    r"cat\s+",
    r"ls\b",
    r"dir\b",
    r"type\s+",
]

SENSITIVE_PATTERNS = [
    r"delete\b",
    r"remove\b",
    r"git\s+push\b",
    r"modify\s+(existing\s+)?src/core",
    r"drop\s+table",
    r"rm\s+-rf\b",
    r"rm\s+",
    r"truncate\b",
    r"alter\s+table",
    r"git\s+reset",
    r"git\s+rebase",
    r"overwrite\b",
    r"replace\s+all",
]

BLOCKED_PATTERNS = [
    r"git\s+push\s+--force",
    r"git\s+push\s+-f\b",
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\\\.",
    r"curl\s+.*\|\s*bash",
    r"wget\s+.*\|\s*bash",
    r"chmod\s+777",
    r":(){ :\|:& };:",          # fork bomb
    r"mkfs\b",
    r"dd\s+if=",
    r"format\s+[a-zA-Z]:",     # Windows format drive
    r"del\s+/[sS]\s+/[qQ]",   # Windows recursive silent delete
    r"shutdown\b",
    r"reboot\b",
]

# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------

class Tier:
    ALLOWED = "ALLOWED"
    SENSITIVE = "SENSITIVE"
    BLOCKED = "BLOCKED"

# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def is_agent_enabled() -> bool:
    """Check the kill switch. If AGENT_ENABLED file is missing, agent is off."""
    return AGENT_ENABLED_FILE.exists()


def get_agent_mode() -> str:
    """Read the current agent mode. Defaults to 'safe' if file missing."""
    if not AGENT_MODE_FILE.exists():
        return "safe"
    mode = AGENT_MODE_FILE.read_text().strip().lower()
    if mode not in ("safe", "supervised", "autonomous"):
        return "safe"
    return mode


def classify_task(task_description: str) -> str:
    """
    Classify a task description into a permission tier.

    Checks in order: BLOCKED -> SENSITIVE -> ALLOWED.
    Unknown tasks default to SENSITIVE (fail-safe).
    """
    text = task_description.lower().strip()

    # Check blocked first — these are never allowed
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return Tier.BLOCKED

    # Check sensitive — needs human approval
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return Tier.SENSITIVE

    # Check explicitly allowed
    for pattern in ALLOWED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return Tier.ALLOWED

    # Unknown tasks default to SENSITIVE (fail-safe, not fail-open)
    return Tier.SENSITIVE


def enforce_mode(tier: str, mode: str) -> str:
    """
    Apply the agent mode to the classification result.

    - safe mode:       only ALLOWED tasks proceed; everything else needs approval
    - supervised mode: ALLOWED auto-proceeds, SENSITIVE needs approval, BLOCKED rejected
    - autonomous mode: ALLOWED + SENSITIVE auto-proceed, BLOCKED still rejected

    Returns: "proceed" | "needs_approval" | "blocked"
    """
    if tier == Tier.BLOCKED:
        return "blocked"

    if mode == "safe":
        if tier == Tier.ALLOWED:
            return "proceed"
        return "needs_approval"

    if mode == "supervised":
        if tier == Tier.ALLOWED:
            return "proceed"
        if tier == Tier.SENSITIVE:
            return "needs_approval"
        return "blocked"

    if mode == "autonomous":
        if tier in (Tier.ALLOWED, Tier.SENSITIVE):
            return "proceed"
        return "blocked"

    # Unrecognized mode — treat as safe
    return "needs_approval"


def write_pending_approval(task_description: str, tier: str, mode: str):
    """Write a pending_approval.json file so the human can review and approve."""
    PENDING_APPROVAL.parent.mkdir(parents=True, exist_ok=True)
    approval = {
        "task": task_description,
        "tier": tier,
        "mode": mode,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "approved_by": None,
        "approved_at": None,
    }
    PENDING_APPROVAL.write_text(json.dumps(approval, indent=2))
    return approval


def check_pending_approval() -> dict | None:
    """Check if there's a pending approval and whether it's been approved."""
    if not PENDING_APPROVAL.exists():
        return None
    try:
        data = json.loads(PENDING_APPROVAL.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return None


def approve_task(approver: str = "human"):
    """Mark the pending task as approved."""
    if not PENDING_APPROVAL.exists():
        return False
    data = json.loads(PENDING_APPROVAL.read_text())
    data["status"] = "approved"
    data["approved_by"] = approver
    data["approved_at"] = datetime.now(timezone.utc).isoformat()
    PENDING_APPROVAL.write_text(json.dumps(data, indent=2))
    return True


def deny_task(reason: str = ""):
    """Mark the pending task as denied."""
    if not PENDING_APPROVAL.exists():
        return False
    data = json.loads(PENDING_APPROVAL.read_text())
    data["status"] = "denied"
    data["denied_reason"] = reason
    data["denied_at"] = datetime.now(timezone.utc).isoformat()
    PENDING_APPROVAL.write_text(json.dumps(data, indent=2))
    return True


def log_audit(task_description: str, tier: str, decision: str, mode: str, details: str = ""):
    """Append an entry to the audit log."""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task": task_description,
        "tier": tier,
        "decision": decision,
        "mode": mode,
        "details": details,
    }
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Main gate function — call this from ClaudeCodeBridge
# ---------------------------------------------------------------------------

def gate(task_description: str) -> dict:
    """
    Main entry point. Runs the full gatekeeper check:
    1. Kill switch check
    2. Read agent mode
    3. Classify task
    4. Enforce mode rules
    5. Write pending_approval if needed
    6. Log everything

    Returns dict with: allowed (bool), tier, decision, mode, reason
    """
    # 1. Kill switch
    if not is_agent_enabled():
        result = {
            "allowed": False,
            "tier": "N/A",
            "decision": "killed",
            "mode": "disabled",
            "reason": "AGENT_ENABLED file missing — agent is off",
        }
        log_audit(task_description, "N/A", "killed", "disabled", "Kill switch active")
        return result

    # 2. Read mode
    mode = get_agent_mode()

    # 3. Classify
    tier = classify_task(task_description)

    # 4. Enforce
    decision = enforce_mode(tier, mode)

    # 5. Handle decision
    reason = ""
    if decision == "blocked":
        reason = f"Task classified as {tier} — always blocked regardless of mode"
        allowed = False
    elif decision == "needs_approval":
        reason = f"Task classified as {tier} in {mode} mode — requires human approval"
        write_pending_approval(task_description, tier, mode)
        allowed = False
    else:  # proceed
        reason = f"Task classified as {tier} in {mode} mode — auto-approved"
        allowed = True

    # 6. Log
    log_audit(task_description, tier, decision, mode, reason)

    return {
        "allowed": allowed,
        "tier": tier,
        "decision": decision,
        "mode": mode,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# CLI interface — for testing and manual use
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python gatekeeper.py <task_description>")
        print("       python gatekeeper.py --approve [approver]")
        print("       python gatekeeper.py --deny [reason]")
        print("       python gatekeeper.py --status")
        print("       python gatekeeper.py --enable")
        print("       python gatekeeper.py --disable")
        print("       python gatekeeper.py --mode <safe|supervised|autonomous>")
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--approve":
        approver = sys.argv[2] if len(sys.argv) > 2 else "human"
        if approve_task(approver):
            print(f"Task approved by {approver}")
        else:
            print("No pending task to approve")

    elif arg == "--deny":
        reason = sys.argv[2] if len(sys.argv) > 2 else ""
        if deny_task(reason):
            print(f"Task denied. Reason: {reason or 'none given'}")
        else:
            print("No pending task to deny")

    elif arg == "--status":
        enabled = is_agent_enabled()
        mode = get_agent_mode()
        pending = check_pending_approval()
        print(f"Agent enabled: {enabled}")
        print(f"Agent mode:    {mode}")
        if pending:
            print(f"Pending task:  {pending['task']}")
            print(f"  Status:      {pending['status']}")
            print(f"  Requested:   {pending['requested_at']}")
        else:
            print("Pending task:  none")

    elif arg == "--enable":
        AGENT_CONTROL.mkdir(parents=True, exist_ok=True)
        AGENT_ENABLED_FILE.write_text("enabled")
        print("Agent ENABLED")

    elif arg == "--disable":
        if AGENT_ENABLED_FILE.exists():
            AGENT_ENABLED_FILE.unlink()
        print("Agent DISABLED (kill switch active)")

    elif arg == "--mode":
        if len(sys.argv) < 3:
            print(f"Current mode: {get_agent_mode()}")
        else:
            new_mode = sys.argv[2].lower()
            if new_mode not in ("safe", "supervised", "autonomous"):
                print(f"Invalid mode: {new_mode}. Use: safe, supervised, autonomous")
                sys.exit(1)
            AGENT_CONTROL.mkdir(parents=True, exist_ok=True)
            AGENT_MODE_FILE.write_text(new_mode)
            print(f"Agent mode set to: {new_mode}")

    else:
        # Treat as a task description to classify
        result = gate(arg)
        print(f"Tier:     {result['tier']}")
        print(f"Decision: {result['decision']}")
        print(f"Mode:     {result['mode']}")
        print(f"Allowed:  {result['allowed']}")
        print(f"Reason:   {result['reason']}")


if __name__ == "__main__":
    main()
