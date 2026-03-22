# Architecture Decisions

Log of key architectural decisions for OpenClaw. Newest first.

---

## 2026-03-22 — Direct CLI bridge instead of ACP/acpx

**Decision**: Bypass ACPX plugin entirely. Bridge.py spawns `claude.exe` directly as a subprocess.

**Context**: ACPX plugin had Windows-specific issues — `.cmd` wrapper resolution, missing `@zed-industries/claude-agent-acp` package, spawn hanging indefinitely even after "ready" reported.

**Consequence**: Simpler, faster, no dependency on third-party ACP adapters. Trade-off is no built-in session persistence across turns (each bridge call is a fresh Claude invocation).

---

## 2026-03-22 — Fail-safe default to SENSITIVE

**Decision**: Unknown task intents default to SENSITIVE tier (requires approval), not ALLOWED.

**Context**: Fail-open would let unrecognized commands execute without gating. Fail-safe means worst case is a false positive that requires approval.

**Consequence**: May need to expand ALLOWED_PATTERNS over time as common safe operations get flagged. Acceptable trade-off for security.

---

## 2026-03-22 — File-based kill switch

**Decision**: Agent on/off controlled by presence of `agent_control/AGENT_ENABLED` file, not a database or API.

**Context**: Needs to be instantly toggleable, even if gateway/services are down. File deletion is atomic and works from any terminal.

**Consequence**: No remote kill capability without file access. Acceptable since this runs on the user's local machine.
