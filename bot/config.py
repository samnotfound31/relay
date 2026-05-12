"""
Relay Bot — Configuration
Loads environment variables and defines constants.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Core ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
BOT_PREFIX: str = os.getenv("BOT_PREFIX", "!")

# ── Database ──────────────────────────────────────────
DB_PATH: str = os.path.join(os.path.dirname(__file__), "..", "data", "relay.db")

# ── Branding ──────────────────────────────────────────
RELAY_COLOR       = 0x5865F2   # Discord blurple – calm, modern
RELAY_SUCCESS     = 0x57F287   # Green
RELAY_WARNING     = 0xFEE75C   # Yellow
RELAY_ERROR       = 0xED4245   # Red
RELAY_NEUTRAL     = 0x2F3136   # Dark embed bg

DEFAULT_BANNER_URL = (
    "https://cdn.discordapp.com/attachments/"
    "1234567890/placeholder_banner.png"
)

# ── Reserved System Emojis ────────────────────────────
# Staff may NOT select these as personal emojis.
RESERVED_EMOJIS = {"👤", "🔄", "📌", "🔒", "⏰", "🔍", "⏳", "⬆️", "✅"}

# ── Ticket Statuses ──────────────────────────────────
TICKET_STATUSES = [
    "open",
    "investigating",
    "waiting-user",
    "escalated",
    "resolved",
    "closed",
]

# ── Status → Emoji Mapping ───────────────────────────
STATUS_EMOJIS: dict[str, str] = {
    "investigating": "🔍",
    "waiting-user":  "⏳",
    "escalated":     "⬆️",
    "resolved":      "✅",
    # "open" and "closed" have no workflow emoji
}

# ── Inactivity ────────────────────────────────────────
# Seconds before a ticket is considered inactive (default 24h)
INACTIVITY_THRESHOLD_SECONDS = int(os.getenv("INACTIVITY_THRESHOLD", "86400"))
# How often the inactivity check loop runs (default 10 min)
INACTIVITY_CHECK_INTERVAL = int(os.getenv("INACTIVITY_CHECK_INTERVAL", "600"))

# ── Cosmetic Rename Resync ──────────────────────────────
# How often the rename resync loop runs (default 15 sec)
RENAME_RESYNC_INTERVAL = int(os.getenv("RENAME_RESYNC_INTERVAL", "15"))

# ── Autoclose ───────────────────────────────────────────
# How often the autoclose scheduler loop runs (default 30 sec)
AUTOCLOSE_CHECK_INTERVAL = int(os.getenv("AUTOCLOSE_CHECK_INTERVAL", "30"))

# ── Priority ──────────────────────────────────────────
PRIORITY_EMOJIS: dict[str, str] = {
    "low":    "🟢",
    "medium": "🟡",
    "high":   "🔴",
}
PRIORITY_LEVELS = ["low", "medium", "high"]
