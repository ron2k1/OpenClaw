"""
OpenClaw Telegram Bot — Remote pipeline monitoring and control.

Tails audit_log.jsonl for events, polls pending_approval.json for gating,
exposes /status /approve /deny /kill /resume /mode /history commands.

Zero coupling to bridge.py — reads/writes the same files the pipeline uses.

Usage:
    python scripts/telegram_bot.py

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env or environment.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

AUDIT_LOG = PROJECT_ROOT / "audit_log.jsonl"
STATE_JSON = PROJECT_ROOT / "tasks" / "state.json"
PENDING_APPROVAL = PROJECT_ROOT / "tasks" / "pending_approval.json"
AGENT_ENABLED = PROJECT_ROOT / "agent_control" / "AGENT_ENABLED"
AGENT_MODE = PROJECT_ROOT / "agent_control" / "AGENT_MODE"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", "5"))

# ── Gatekeeper imports (for approve/deny) ──────────────────────────────
sys.path.insert(0, str(PROJECT_ROOT / "gatekeeper"))
try:
    from gatekeeper import approve_task, deny_task
except ImportError:
    # Fallback: direct file manipulation
    def approve_task(approver="telegram"):
        if not PENDING_APPROVAL.exists():
            return False
        data = json.loads(PENDING_APPROVAL.read_text())
        data["status"] = "approved"
        data["approved_by"] = approver
        data["approved_at"] = datetime.now(timezone.utc).isoformat()
        PENDING_APPROVAL.write_text(json.dumps(data, indent=2))
        return True

    def deny_task(reason=""):
        if not PENDING_APPROVAL.exists():
            return False
        data = json.loads(PENDING_APPROVAL.read_text())
        data["status"] = "denied"
        data["denied_reason"] = reason
        data["denied_at"] = datetime.now(timezone.utc).isoformat()
        PENDING_APPROVAL.write_text(json.dumps(data, indent=2))
        return True


# ── Helpers ────────────────────────────────────────────────────────────

def _authorized(update: Update) -> bool:
    """Only process messages from the configured chat ID."""
    return str(update.effective_chat.id) == CHAT_ID


def _read_state() -> dict:
    """Read current pipeline state."""
    if not STATE_JSON.exists():
        return {}
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_pending() -> dict | None:
    """Read pending approval if exists and is pending."""
    if not PENDING_APPROVAL.exists():
        return None
    try:
        data = json.loads(PENDING_APPROVAL.read_text(encoding="utf-8"))
        if data.get("status") == "pending":
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _tail_audit(n: int = 5) -> list[dict]:
    """Read last N entries from audit log."""
    if not AUDIT_LOG.exists():
        return []
    try:
        lines = AUDIT_LOG.read_text(encoding="utf-8").strip().split("\n")
        entries = []
        for line in lines[-n:]:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries
    except OSError:
        return []


def _agent_status() -> tuple[bool, str]:
    """Return (enabled, mode)."""
    enabled = AGENT_ENABLED.exists()
    mode = "safe"
    if AGENT_MODE.exists():
        try:
            mode = AGENT_MODE.read_text(encoding="utf-8").strip().lstrip("\ufeff")
        except OSError:
            pass
    return enabled, mode


def _decision_icon(decision: str) -> str:
    icons = {
        "completed": "\u2705",       # checkmark
        "proceed": "\u25b6\ufe0f",   # play
        "blocked": "\u26d4",         # no entry
        "killed": "\U0001f6d1",      # stop sign
        "needs_approval": "\u26a0\ufe0f",  # warning
        "self_healed": "\U0001f504",  # cycle
        "self_heal_failed": "\u274c", # X
        "self_heal_exhausted": "\U0001f6a8",  # siren
        "quality_failed": "\U0001f6a7",  # construction
        "claude_error": "\U0001f4a5",  # boom
        "branch_failed": "\U0001f500",  # shuffle
    }
    return icons.get(decision, "\u2022")


def _format_entry(entry: dict) -> str:
    icon = _decision_icon(entry.get("decision", ""))
    task = entry.get("task", "?")[:60]
    decision = entry.get("decision", "?")
    ts = entry.get("timestamp", "")[:16]
    return f"{icon} {decision}: {task}\n   {ts}"


# ── Command Handlers ──────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    enabled, mode = _agent_status()
    state = _read_state()
    pending = _read_pending()

    status_icon = "\U0001f7e2" if enabled else "\U0001f534"  # green/red circle
    status_text = "ENABLED" if enabled else "DISABLED"

    lines = [
        f"\U0001f4ca OpenClaw Status",
        f"\u2501" * 20,
        f"{status_icon} Agent: {status_text}",
        f"\U0001f527 Mode: {mode}",
    ]

    if state:
        last_task = state.get("last_task", "none")[:50]
        last_status = state.get("status", "?")
        icon = _decision_icon(last_status)
        lines.append(f"\U0001f4cb Last: {last_task}")
        lines.append(f"{icon} Status: {last_status}")

        ts = state.get("timestamp", "")
        if ts:
            lines.append(f"\u23f1\ufe0f {ts[:16]}")

    if pending:
        lines.append(f"\n\u26a0\ufe0f PENDING APPROVAL:")
        lines.append(f"  {pending.get('task', '?')[:50]}")
        lines.append(f"  Tier: {pending.get('tier', '?')}")

    recent = _tail_audit(5)
    if recent:
        lines.append(f"\nRecent:")
        for entry in reversed(recent):
            icon = _decision_icon(entry.get("decision", ""))
            task = entry.get("task", "?")[:40]
            lines.append(f" {icon} {task}")

    await update.message.reply_text("\n".join(lines))


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    pending = _read_pending()
    if not pending:
        await update.message.reply_text("\u2705 No pending approvals.")
        return

    if approve_task("telegram"):
        await update.message.reply_text(
            f"\u2705 Approved: {pending.get('task', '?')[:60]}"
        )
    else:
        await update.message.reply_text("\u274c Failed to approve — file error.")


async def cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    pending = _read_pending()
    if not pending:
        await update.message.reply_text("\u2705 No pending approvals.")
        return

    reason = " ".join(context.args) if context.args else "Denied via Telegram"
    if deny_task(reason):
        await update.message.reply_text(
            f"\u274c Denied: {pending.get('task', '?')[:60]}\nReason: {reason}"
        )
    else:
        await update.message.reply_text("\u274c Failed to deny — file error.")


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    if AGENT_ENABLED.exists():
        try:
            AGENT_ENABLED.unlink()
            await update.message.reply_text(
                "\U0001f6d1 Agent KILLED. Pipeline will stop at next gate check.\n"
                "Use /resume to re-enable."
            )
        except OSError as e:
            await update.message.reply_text(f"\u274c Failed to kill: {e}")
    else:
        await update.message.reply_text("\U0001f534 Agent already disabled.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    try:
        AGENT_ENABLED.parent.mkdir(parents=True, exist_ok=True)
        AGENT_ENABLED.write_text("enabled")
        await update.message.reply_text("\U0001f7e2 Agent RESUMED. Pipeline is active.")
    except OSError as e:
        await update.message.reply_text(f"\u274c Failed to resume: {e}")


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    if not context.args:
        _, current = _agent_status()
        await update.message.reply_text(
            f"\U0001f527 Current mode: {current}\n"
            f"Usage: /mode safe|supervised|autonomous"
        )
        return

    new_mode = context.args[0].lower()
    if new_mode not in ("safe", "supervised", "autonomous"):
        await update.message.reply_text(
            f"\u274c Invalid mode: {new_mode}\n"
            f"Valid: safe, supervised, autonomous"
        )
        return

    try:
        AGENT_MODE.parent.mkdir(parents=True, exist_ok=True)
        AGENT_MODE.write_text(new_mode)
        await update.message.reply_text(f"\U0001f527 Mode changed to: {new_mode}")
    except OSError as e:
        await update.message.reply_text(f"\u274c Failed to set mode: {e}")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    n = 10
    if context.args:
        try:
            n = int(context.args[0])
        except ValueError:
            pass
    n = min(n, 20)

    entries = _tail_audit(n)
    if not entries:
        await update.message.reply_text("No audit entries found.")
        return

    lines = [f"\U0001f4dc Last {len(entries)} entries:", "\u2501" * 20]
    for entry in reversed(entries):
        lines.append(_format_entry(entry))

    await update.message.reply_text("\n".join(lines))


# ── Callback for inline approve/deny buttons ──────────────────────────

async def callback_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not str(query.message.chat_id) == CHAT_ID:
        return

    await query.answer()
    action = query.data

    if action == "approve":
        if approve_task("telegram"):
            await query.edit_message_text(
                f"\u2705 Approved via button.\n"
                f"Original: {query.message.text}"
            )
        else:
            await query.edit_message_text("\u274c Approve failed — no pending task.")
    elif action == "deny":
        if deny_task("Denied via Telegram button"):
            await query.edit_message_text(
                f"\u274c Denied via button.\n"
                f"Original: {query.message.text}"
            )
        else:
            await query.edit_message_text("\u274c Deny failed — no pending task.")


# ── Background monitor ─────────────────────────────────────────────────

class PipelineMonitor:
    """Tails audit_log.jsonl and polls pending_approval.json."""

    def __init__(self, app: Application):
        self.app = app
        self._audit_offset = 0
        self._last_approval_key = None
        self._running = False

    async def start(self):
        """Initialize offset to end of file and send startup summary."""
        if AUDIT_LOG.exists():
            self._audit_offset = AUDIT_LOG.stat().st_size

        # Send startup summary
        enabled, mode = _agent_status()
        status_icon = "\U0001f7e2" if enabled else "\U0001f534"
        await self.app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"\U0001f916 OpenClaw Bot Online\n"
                f"\u2501" * 20 + "\n"
                f"{status_icon} Agent: {'ENABLED' if enabled else 'DISABLED'}\n"
                f"\U0001f527 Mode: {mode}\n"
                f"\U0001f4e1 Monitoring audit log...\n"
                f"\nCommands: /status /approve /deny /kill /resume /mode /history"
            ),
        )
        self._running = True

    async def poll(self):
        """Single poll iteration — check audit log + approval queue."""
        if not self._running:
            return

        await self._check_audit()
        await self._check_approval()

    async def _check_audit(self):
        """Read new lines from audit_log.jsonl since last offset."""
        if not AUDIT_LOG.exists():
            return

        try:
            size = AUDIT_LOG.stat().st_size
            if size <= self._audit_offset:
                if size < self._audit_offset:
                    self._audit_offset = 0  # file was truncated/rotated
                return

            with open(AUDIT_LOG, "r", encoding="utf-8") as f:
                f.seek(self._audit_offset)
                new_lines = f.read()
                self._audit_offset = f.tell()

            entries = []
            for line in new_lines.strip().split("\n"):
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

            # Batch and send notifications
            for entry in entries:
                await self._notify_entry(entry)

        except OSError:
            pass

    async def _notify_entry(self, entry: dict):
        """Send notification for a single audit entry."""
        decision = entry.get("decision", "")
        task = entry.get("task", "?")[:70]
        tier = entry.get("tier", "")
        details = entry.get("details", "")[:200]

        # Skip low-noise events
        if decision == "proceed":
            return

        if decision == "completed":
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\u2705 Task completed\n"
                    f"\u2501" * 20 + "\n"
                    f"\U0001f4cb {task}\n"
                    f"{details if details else ''}"
                ),
            )
        elif decision == "blocked":
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\u26d4 Task BLOCKED\n"
                    f"\u2501" * 20 + "\n"
                    f"\U0001f4cb {task}\n"
                    f"\U0001f512 Tier: {tier}\n"
                    f"\U0001f4ac {details if details else 'Blocked by gatekeeper'}"
                ),
            )
        elif decision == "killed":
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\U0001f6d1 Agent DISABLED\n"
                    f"\u2501" * 20 + "\n"
                    f"Kill switch triggered.\n"
                    f"Use /resume to re-enable."
                ),
            )
        elif decision in ("self_heal_exhausted", "self_heal_failed"):
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\U0001f6a8 Self-heal FAILED\n"
                    f"\u2501" * 20 + "\n"
                    f"\U0001f4cb {task}\n"
                    f"\u274c Retries exhausted\n"
                    f"\u26a1 Manual fix required"
                ),
            )
        elif decision in ("self_healed", "quality_healed"):
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\U0001f504 Self-healed\n"
                    f"\u2501" * 20 + "\n"
                    f"\U0001f4cb {task}\n"
                    f"\u2705 Recovered automatically"
                ),
            )
        elif decision == "quality_failed":
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\U0001f6a7 Quality gate FAILED\n"
                    f"\u2501" * 20 + "\n"
                    f"\U0001f4cb {task}\n"
                    f"{details if details else 'Check quality gate results'}"
                ),
            )
        elif decision in ("claude_error", "claude_failed"):
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\U0001f4a5 Claude error\n"
                    f"\u2501" * 20 + "\n"
                    f"\U0001f4cb {task}\n"
                    f"{details[:150] if details else 'Claude subprocess failed'}"
                ),
            )
        elif decision == "branch_failed":
            await self.app.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"\U0001f500 Branch creation failed\n"
                    f"\u2501" * 20 + "\n"
                    f"\U0001f4cb {task}\n"
                    f"{details[:150] if details else ''}"
                ),
            )

    async def _check_approval(self):
        """Poll for new pending approval requests."""
        pending = _read_pending()

        if pending is None:
            self._last_approval_key = None
            return

        # Dedup: don't re-notify for same approval
        key = f"{pending.get('task', '')}-{pending.get('requested_at', '')}"
        if key == self._last_approval_key:
            return
        self._last_approval_key = key

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\u2705 Approve", callback_data="approve"),
                InlineKeyboardButton("\u274c Deny", callback_data="deny"),
            ]
        ])

        await self.app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"\u26a0\ufe0f Approval Required\n"
                f"\u2501" * 20 + "\n"
                f"\U0001f4cb {pending.get('task', '?')[:70]}\n"
                f"\U0001f512 Tier: {pending.get('tier', '?')}\n"
                f"\U0001f527 Mode: {pending.get('mode', '?')}\n"
                f"\nTap a button or use /approve | /deny [reason]"
            ),
            reply_markup=keyboard,
        )


# ── Main ───────────────────────────────────────────────────────────────

async def _background_poller(app: Application, monitor: PipelineMonitor):
    """Background loop that polls pipeline state every POLL_INTERVAL seconds."""
    await monitor.start()
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            await monitor.poll()
        except Exception as e:
            print(f"Poll error: {e}")


def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env or environment")
        sys.exit(1)
    if not CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID not set in .env or environment")
        sys.exit(1)

    print(f"Starting OpenClaw Telegram Bot...")
    print(f"  Project: {PROJECT_ROOT}")
    print(f"  Poll interval: {POLL_INTERVAL}s")
    print(f"  Chat ID: {CHAT_ID}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Register command handlers
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("deny", cmd_deny))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(callback_approval))

    # Background monitor
    monitor = PipelineMonitor(app)
    app.bot_data["monitor"] = monitor

    async def post_init(application: Application):
        asyncio.create_task(_background_poller(application, monitor))

    app.post_init = post_init
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
