# OpenClaw — System Primer

## What is OpenClaw?
An agentic development pipeline that bridges Grok (orchestrator) and Claude Code (executor) into a coherent, memory-persistent, security-gated workflow. It solves four fundamental problems with stateless AI coding agents:

1. **Memory gap** — AI agents forget everything between sessions
2. **Output parsing fragility** — no structured handoff between invocations
3. **Security** — all-or-nothing permission models are dangerous
4. **Quality assurance** — code that compiles isn't necessarily correct

## How It Works

### The Flow
```
You decide what to build
    -> Grok plans the work, reads Obsidian memory
    -> update_primer.py refreshes CLAUDE.md with latest context
    -> gatekeeper.py classifies the task's permission tier
    -> git branch created for isolation
    -> Claude Code executes with full context
    -> 8-layer quality pipeline validates output
    -> Self-heal loop retries failures (max 3)
    -> Results written to state.json + Obsidian
    -> Branch pushed for your review
```

### The Memory Architecture
Three layers ensure nothing is lost between sessions:

| Layer | What | Who Reads | Who Writes |
|-------|------|-----------|------------|
| `state.json` | Structured task results | Grok, update_primer.py | Post-invocation parser |
| Git diff/log | Ground truth filesystem changes | Grok | Git (automatic) |
| Obsidian session log | Human-readable narrative | You, Grok | Grok, you manually |

### The Security Model
Three permission tiers, enforced by `gatekeeper.py`:

| Tier | Actions | Behavior |
|------|---------|----------|
| **Safe** | Read files, cargo check/test, create files | Auto-approved |
| **Sensitive** | Delete, git push, modify core modules | Writes `pending_approval.json`, halts |
| **Blocked** | Force push, rm -rf, curl\|bash | Rejected + logged + Grok notified |

Global mode set via `AGENT_MODE` file: `safe`, `supervised`, or `autonomous`.

### The Quality Pipeline
8 layers catch progressively subtler problems:

1. **cargo check** — does it compile?
2. **cargo clippy -D warnings** — 450+ antipattern checks, treated as errors
3. **cargo audit + deny** — known CVEs and license violations
4. **grep unwrap/unsafe** — panic surface + unsafe blocks flagged for manual review
5. **semgrep OWASP rules** — injection, input validation, header manipulation
6. **API surface diff** — detects silent breaking changes to public interfaces
7. **cargo tarpaulin** — coverage must stay above 80%
8. **cargo mutants** — mutation testing exposes hollow tests

Plus three verification passes:
- **Adversarial Claude review** — separate invocation primed to find problems, not validate
- **Grok spec-vs-implementation** — requirement-by-requirement audit
- **Runtime smoke test** — actually start the server and hit endpoints

### The Self-Heal Loop
```
Quality gate fails
    -> Parse error output
    -> Claude Code: "Fix this error, change nothing else"
    -> Re-run quality gates
    -> Max 3 attempts
    -> If still failing: blocked_tasks.md + human notification
```

Over time, `errors/build_failures.md` feeds back into CLAUDE.md as a "Known Pitfalls" section. The primer gets smarter — Claude Code stops making repeated mistakes. This is a genuine learning loop without fine-tuning.

### Branch Isolation
Every task runs on its own branch:
```
agent/fastrush-auth-middleware-20260321
agent/redline-diff-engine-fix-20260321
```

Benefits:
- No race conditions between parallel tasks
- `--yes` flag is safe on a throwaway branch
- Git history = complete audit log
- Rollback = delete the branch

### The Kill Switch
File-based, instant:
- **Stop everything**: delete `agent_control/AGENT_ENABLED`
- **Resume**: recreate the file
- **Change mode**: edit `agent_control/AGENT_MODE` to `safe`/`supervised`/`autonomous`

## Key Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Self-heal burns tokens on broken tasks | Hard cap of 3 attempts + human escalation |
| Tests pass but don't test real behavior | Mutation testing (cargo mutants) exposes hollow tests |
| Claude reviews its own bugs favorably | Adversarial review is a separate invocation with skeptical prompt |
| Code compiles but doesn't match spec | Grok does requirement-by-requirement spec audit |
| Architectural drift over time | Obsidian decisions log + human code review |
| Unsafe blocks with subtle UB | Every unsafe requires explicit human sign-off, no exceptions |

## What You Control
- On/off switch (file-based, instant)
- Permission mode (safe/supervised/autonomous)
- Approval queue for sensitive operations
- Final merge decision on every branch
- Obsidian vault is always human-readable and editable
