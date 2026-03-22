# OpenClaw — ClaudeCodeBridge Skill Spec

## Skill Definition

**Name**: ClaudeCodeBridge
**Platform**: Windows (Node.js subprocess execution)
**Purpose**: Orchestrate Claude Code CLI invocations with security gating, memory persistence, and quality validation.

## Input Schema
```json
{
  "task_description": "string — what Claude Code should do",
  "project_path": "string — working directory for the task",
  "mode": "enum: safe | supervised | autonomous"
}
```

## Output Schema
```json
{
  "success": "boolean",
  "files_changed": ["list of paths from git diff"],
  "claude_output": "string — full stdout",
  "git_diff": "string — git diff --stat output",
  "next_suggested": "string — recommended follow-up task",
  "quality_results": {
    "cargo_check": "pass | fail",
    "clippy": "pass | fail",
    "audit": "pass | fail",
    "coverage": "number — percentage",
    "mutations_caught": "number — percentage",
    "adversarial_review": "pass | fail | skipped",
    "spec_accuracy": "pass | partial | fail",
    "smoke_test": "pass | fail | skipped",
    "regression": "pass | fail"
  },
  "self_heal_attempts": "number",
  "blocked_reason": "string | null"
}
```

## Execution Flow

### Pre-Execution
1. Check `agent_control/AGENT_ENABLED` exists — abort if missing
2. Read `agent_control/AGENT_MODE` — enforce permission tier
3. Run `update_primer.py` to refresh CLAUDE.md with latest state.json + git state
4. Classify task intent against permission tiers (gatekeeper.py)
5. If sensitive: write `pending_approval.json`, halt until approved
6. If blocked: log to `audit_log.jsonl`, reject, notify Grok with reason

### Execution
7. `git checkout -b agent/{task_slug}-{YYYYMMDD}`
8. `claude --print "{task_description}"` in project_path directory
9. Capture full stdout, stderr, exit code

### Post-Execution
10. `git diff --stat` to verify actual filesystem changes
11. Run quality gate pipeline (all 8 layers + verification passes)
12. If hard gate fails: enter self-heal loop (max 3 attempts)
13. Write results to `tasks/state.json`
14. Write session narrative to Obsidian `session_log.md`
15. If exit code != 0 or quality gates failed after self-heal: write to `errors/build_failures.md`
16. If all pass: `git push origin {branch}`, notify human for review
17. Log full invocation to `audit_log.jsonl`

## Security Constraints
- Blocked commands list checked before execution
- Sensitive operations require `pending_approval.json` and halt
- Never pass API keys or secrets in task_description
- Every invocation logged with timestamp to `audit_log.jsonl`
- Skill must be idempotent — running twice for the same task is safe

## Gatekeeper Permission Tiers

### ALLOWED_WITHOUT_CONFIRMATION
```
create new file
read file
run cargo check
run cargo test
add dependency to Cargo.toml
```

### REQUIRE_CONFIRMATION
```
delete
git push
modify existing src/core/
drop table
rm -rf
```

### ALWAYS_BLOCKED
```
git push --force
rm -rf /
curl * | bash
chmod 777
```

## Self-Heal Loop
```
for attempt in 1..3:
    result = run_quality_gates()
    if result.all_pass:
        break
    error = result.first_failure
    run_claude("Fix this error, change nothing else: {error}")
    log_to_obsidian("Self-heal attempt {attempt}: {error}")
else:
    notify_human("Self-heal failed after 3 attempts")
    write_to_blocked_tasks(task)
```

## Adversarial Review Prompt
```
You are a senior security engineer reviewing code written by an AI agent.
Your job is to find problems, not to validate the work.

Review the following diff for:
1. Security vulnerabilities
2. Logic errors that tests would not catch
3. Inconsistency with the patterns in CLAUDE.md
4. Any behavior that differs from the task description
5. Panics that could occur at runtime

Be skeptical. The code was written by an AI and may contain subtle errors.
If you find nothing wrong, explain specifically why each risk area was
checked and cleared.
```

## Obsidian Vault Structure
```
vault/
├── projects/
│   ├── {project_name}/
│   │   ├── decisions.md        <- architecture decisions log
│   │   ├── session_log.md      <- what happened last session
│   │   ├── open_tasks.md       <- pending work items
│   │   └── patterns.md         <- code patterns Claude should follow
├── agent_memory/
│   ├── grok_working_memory.md  <- Grok's current task state
│   └── claude_last_output.md   <- parsed results of last Claude Code run
└── errors/
    └── build_failures.md       <- self-healing log
```

## Research Prerequisites
Before implementation, verify:
1. How OpenClaw skills are structured (input schema, handler, output schema)
2. How to run subprocess shell commands on Windows from Node.js
3. How Claude Code CLI flags work: `--yes`, `--print`, `--output-format`
4. How to parse Claude Code stdout reliably
