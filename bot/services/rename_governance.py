"""
Relay Bot — Rename Governance Service
Proactive local rate-budget for cosmetic channel renames.

Rules: max 2 successful rename attempts per ticket channel within
any rolling 10-minute window.  Discord API is skipped entirely when
budget is exhausted; the resync loop applies the final state later.

Storage: in-memory only.  On restart the budget resets, but
needs_rename_resync flags in the DB are still picked up by the
resync loop, so cosmetic state self-heals.
"""

from __future__ import annotations

import time
from collections import defaultdict

# ── Constants ────────────────────────────────────────────────────────────
_MAX_RENAMES = 2
_WINDOW_SECONDS = 600  # 10 minutes

# channel_id -> list of monotonic timestamps of successful Discord rename calls
_history: defaultdict[int, list[float]] = defaultdict(list)


# ── Public API ────────────────────────────────────────────────────────────
def can_rename(channel_id: int) -> bool:
    """Return True if this channel still has rename budget remaining."""
    now = time.monotonic()
    history = _history[channel_id]
    cutoff = now - _WINDOW_SECONDS
    pruned = [ts for ts in history if ts > cutoff]
    _history[channel_id] = pruned
    return len(pruned) < _MAX_RENAMES


def record_rename(channel_id: int) -> None:
    """Record a successful Discord rename attempt for budget tracking."""
    _history[channel_id].append(time.monotonic())


def budget_remaining(channel_id: int) -> int:
    """Return remaining rename budget for this channel (0–2)."""
    now = time.monotonic()
    history = _history[channel_id]
    cutoff = now - _WINDOW_SECONDS
    pruned = [ts for ts in history if ts > cutoff]
    _history[channel_id] = pruned
    return max(0, _MAX_RENAMES - len(pruned))
