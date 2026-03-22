# Code Patterns

Patterns Claude Code should follow when working in this project. Loaded into CLAUDE.md context.

---

## Python Style
- Use pathlib.Path, not os.path
- Type hints on public functions
- Structured JSON output for all scripts (machine-readable)
- Every script works standalone via CLI and as an importable module

## Error Handling
- Never silently swallow errors in core pipeline (gatekeeper, bridge)
- Non-fatal errors (primer update, state write) can be swallowed with pass
- Always log errors to audit_log.jsonl

## Security
- Never pass secrets in task descriptions or CLI args
- Gatekeeper runs BEFORE any Claude Code invocation, no exceptions
- Default to SENSITIVE (fail-safe) for unknown intents
- Blocked tier is absolute — no override, no bypass

## Git
- One branch per task: `agent/{slug}-{YYYYMMDD}`
- Never commit to main directly from the pipeline
- Never force push
