"""
Relay Bot — Database Queries
All DB access goes through these async helpers.
"""

from __future__ import annotations
from typing import Any
from bot.database.connection import get_connection


# ── Guild Settings ────────────────────────────────────

async def get_guild_settings(guild_id: int) -> dict | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def upsert_guild_settings(guild_id: int, **kwargs: Any) -> None:
    db = await get_connection()
    existing = await get_guild_settings(guild_id)
    if existing is None:
        cols = ", ".join(["guild_id"] + list(kwargs.keys()))
        placeholders = ", ".join(["?"] * (1 + len(kwargs)))
        await db.execute(
            f"INSERT INTO guild_settings ({cols}) VALUES ({placeholders})",
            (guild_id, *kwargs.values()),
        )
    else:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        await db.execute(
            f"UPDATE guild_settings SET {sets} WHERE guild_id = ?",
            (*kwargs.values(), guild_id),
        )
    await db.commit()


# ── Support Roles ─────────────────────────────────────

async def add_support_role(guild_id: int, role_id: int) -> bool:
    db = await get_connection()
    try:
        await db.execute(
            "INSERT INTO support_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, role_id),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_support_role(guild_id: int, role_id: int) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        "DELETE FROM support_roles WHERE guild_id = ? AND role_id = ?",
        (guild_id, role_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_support_roles(guild_id: int) -> list[int]:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT role_id FROM support_roles WHERE guild_id = ?", (guild_id,)
    )
    rows = await cursor.fetchall()
    return [row["role_id"] for row in rows]


# ── Ticket Categories ────────────────────────────────

async def add_category(
    guild_id: int, name: str, description: str, emoji: str,
    discord_category_id: int | None = None,
) -> bool:
    db = await get_connection()
    try:
        await db.execute(
            """INSERT INTO ticket_categories
               (guild_id, name, description, emoji, discord_category_id)
               VALUES (?, ?, ?, ?, ?)""",
            (guild_id, name, description, emoji, discord_category_id),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_category(guild_id: int, name: str) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        "DELETE FROM ticket_categories WHERE guild_id = ? AND name = ?",
        (guild_id, name),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_categories(guild_id: int) -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM ticket_categories WHERE guild_id = ?", (guild_id,)
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_category_by_name(guild_id: int, name: str) -> dict | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM ticket_categories WHERE guild_id = ? AND name = ?",
        (guild_id, name),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def update_category_discord_id(
    guild_id: int, name: str, discord_category_id: int,
) -> None:
    db = await get_connection()
    await db.execute(
        """UPDATE ticket_categories SET discord_category_id = ?
           WHERE guild_id = ? AND name = ?""",
        (discord_category_id, guild_id, name),
    )
    await db.commit()


# ── Tickets ───────────────────────────────────────────

async def create_ticket(
    guild_id: int,
    user_id: int,
    channel_id: int,
    category_name: str | None = None,
    source_guild_id: int | None = None,
) -> dict:
    db = await get_connection()
    # Continuity numbering: scoped per (user_id, source_guild_id).
    # Ticket numbers represent THIS USER'S history inside THIS COMMUNITY,
    # not global infrastructure volume.
    community_guild_id = source_guild_id or guild_id
    cursor = await db.execute(
        """SELECT COALESCE(MAX(community_ticket_number), 0) + 1 AS next_number
           FROM tickets
           WHERE user_id = ?
             AND COALESCE(source_guild_id, guild_id) = ?""",
        (user_id, community_guild_id),
    )
    row = await cursor.fetchone()
    community_ticket_number = row["next_number"] if row else 1
    cursor = await db.execute(
        """INSERT INTO tickets
           (guild_id, user_id, channel_id, category_name, source_guild_id,
            community_ticket_number, ticket_status, relay_session_status, last_activity_at)
           VALUES (?, ?, ?, ?, ?, ?, 'open', 'active', datetime('now'))""",
        (
            guild_id, user_id, channel_id, category_name, source_guild_id,
            community_ticket_number,
        ),
    )
    await db.commit()
    return {
        "id": cursor.lastrowid,
        "community_ticket_number": community_ticket_number,
    }


async def get_ticket_by_channel(channel_id: int) -> dict | None:
    """Get an OPEN ticket by channel ID."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'",
        (channel_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_ticket_by_id(ticket_id: int) -> dict | None:
    """Get a ticket by its ID (any status)."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE id = ?",
        (ticket_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None




async def get_open_ticket_by_user(guild_id: int, user_id: int) -> dict | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'open'",
        (guild_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_open_ticket_by_user_any_guild(user_id: int) -> dict | None:
    """Find any open ticket for a user across all guilds (for DM routing)."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_open_ticket_by_user_and_source_guild(
    user_id: int, source_guild_id: int,
) -> dict | None:
    """Find any open ticket for a user scoped to a specific source/local guild."""
    db = await get_connection()
    cursor = await db.execute(
        """SELECT * FROM tickets
           WHERE user_id = ? AND status = 'open'
             AND COALESCE(source_guild_id, guild_id) = ?
           ORDER BY created_at DESC LIMIT 1""",
        (user_id, source_guild_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_open_tickets_by_user_any_guild(user_id: int) -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY created_at DESC",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def close_stale_ticket(ticket_id: int) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets
           SET status = 'closed', ticket_status = 'deleted',
               closed_at = datetime('now'), last_activity_at = datetime('now')
           WHERE id = ? AND status = 'open'""",
        (ticket_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def disconnect_relay_session(ticket_id: int) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets
           SET relay_session_status = 'disconnected',
               relay_session_left_at = datetime('now'),
               last_activity_at = datetime('now')
           WHERE id = ? AND status = 'open' AND relay_session_status = 'active'""",
        (ticket_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def claim_ticket(channel_id: int, staff_id: int) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets SET claimed_by = ?, last_activity_at = datetime('now')
           WHERE channel_id = ? AND status = 'open'""",
        (staff_id, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def transfer_ticket(channel_id: int, new_staff_id: int) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets SET claimed_by = ?, last_activity_at = datetime('now')
           WHERE channel_id = ? AND status = 'open'""",
        (new_staff_id, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def unclaim_ticket(channel_id: int) -> bool:
    """Clear ownership — return ticket to open queue."""
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets SET claimed_by = NULL, last_activity_at = datetime('now')
           WHERE channel_id = ? AND status = 'open'""",
        (channel_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_ticket_status(channel_id: int, ticket_status: str) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets SET ticket_status = ?, last_activity_at = datetime('now')
           WHERE channel_id = ? AND status = 'open'""",
        (ticket_status, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def close_ticket(channel_id: int) -> dict | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'",
        (channel_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    ticket = dict(row)
    await db.execute(
        """UPDATE tickets
           SET status = 'closed', ticket_status = 'closed',
               closed_at = datetime('now'), last_activity_at = datetime('now')
           WHERE id = ?""",
        (ticket["id"],),
    )
    await db.commit()
    return ticket



async def update_ticket_priority(channel_id: int, priority: str) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets SET priority = ?, last_activity_at = datetime('now')
           WHERE channel_id = ? AND status = 'open'""",
        (priority, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_ticket_context_issue(channel_id: int, issue: str | None) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets SET ticket_context_issue = ?
           WHERE channel_id = ? AND status = 'open'""",
        (issue, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def move_ticket_category(channel_id: int, new_category_name: str) -> bool:
    """Update the ticket's category_name in DB."""
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets SET category_name = ?, last_activity_at = datetime('now')
           WHERE channel_id = ? AND status = 'open'""",
        (new_category_name, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0

async def touch_ticket_activity(channel_id: int) -> None:
    """Update the last_activity_at timestamp."""
    db = await get_connection()
    await db.execute(
        """UPDATE tickets SET last_activity_at = datetime('now'), is_inactive = 0
           WHERE channel_id = ? AND status = 'open'""",
        (channel_id,),
    )
    await db.commit()


async def get_inactive_tickets(guild_id: int, threshold_seconds: int) -> list[dict]:
    """Get open tickets inactive longer than threshold."""
    db = await get_connection()
    cursor = await db.execute(
        """SELECT * FROM tickets
           WHERE guild_id = ? AND status = 'open' AND is_inactive = 0
             AND (julianday('now') - julianday(last_activity_at)) * 86400 > ?""",
        (guild_id, threshold_seconds),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def mark_ticket_inactive(ticket_id: int) -> None:
    db = await get_connection()
    await db.execute(
        "UPDATE tickets SET is_inactive = 1 WHERE id = ?",
        (ticket_id,),
    )
    await db.commit()


async def flag_rename_resync(channel_id: int) -> None:
    """Mark a ticket channel as needing a cosmetic rename resync."""
    db = await get_connection()
    await db.execute(
        """UPDATE tickets
           SET needs_rename_resync = 1, last_activity_at = datetime('now')
           WHERE channel_id = ? AND status = 'open'""",
        (channel_id,),
    )
    await db.commit()


async def clear_rename_resync(channel_id: int) -> None:
    """Clear the rename resync flag."""
    db = await get_connection()
    await db.execute(
        "UPDATE tickets SET needs_rename_resync = 0 WHERE channel_id = ?",
        (channel_id,),
    )
    await db.commit()


async def get_pending_rename_resyncs(guild_id: int) -> list[dict]:
    """Get open tickets in a guild that need a cosmetic rename resync."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND status = 'open' AND needs_rename_resync = 1",
        (guild_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_all_open_tickets(guild_id: int) -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND status = 'open'",
        (guild_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


# ── Staff Emojis ──────────────────────────────────────

async def set_staff_emoji(guild_id: int, user_id: int, emoji: str) -> bool:
    """Set or update a staff member's personal emoji. Returns False if emoji is taken."""
    db = await get_connection()
    try:
        # Try insert first
        await db.execute(
            """INSERT INTO staff_emojis (guild_id, user_id, emoji)
               VALUES (?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET emoji = excluded.emoji""",
            (guild_id, user_id, emoji),
        )
        await db.commit()
        return True
    except Exception:
        # Emoji uniqueness constraint violated
        return False


async def get_staff_emoji(guild_id: int, user_id: int) -> str | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT emoji FROM staff_emojis WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    row = await cursor.fetchone()
    return row["emoji"] if row else None


async def get_all_staff_emojis(guild_id: int) -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT user_id, emoji FROM staff_emojis WHERE guild_id = ?",
        (guild_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def is_emoji_taken(guild_id: int, emoji: str, exclude_user_id: int | None = None) -> bool:
    db = await get_connection()
    if exclude_user_id:
        cursor = await db.execute(
            "SELECT 1 FROM staff_emojis WHERE guild_id = ? AND emoji = ? AND user_id != ?",
            (guild_id, emoji, exclude_user_id),
        )
    else:
        cursor = await db.execute(
            "SELECT 1 FROM staff_emojis WHERE guild_id = ? AND emoji = ?",
            (guild_id, emoji),
        )
    return await cursor.fetchone() is not None


# ── Guild Links (Phase 3) ──────────────────────────────

async def get_support_guild_id(source_guild_id: int) -> int | None:
    """Get the support guild ID linked to a source guild, or None."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT support_guild_id FROM guild_links WHERE source_guild_id = ?",
        (source_guild_id,),
    )
    row = await cursor.fetchone()
    return row["support_guild_id"] if row else None


async def get_guild_link(source_guild_id: int) -> dict | None:
    """Get the full guild link record."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM guild_links WHERE source_guild_id = ?",
        (source_guild_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_guild_link(
    source_guild_id: int, support_guild_id: int, linked_by: int,
) -> bool:
    """Link a source guild to a support guild. Returns False if already linked."""
    db = await get_connection()
    try:
        await db.execute(
            """INSERT INTO guild_links (source_guild_id, support_guild_id, linked_by)
               VALUES (?, ?, ?)""",
            (source_guild_id, support_guild_id, linked_by),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_guild_link(source_guild_id: int) -> bool:
    """Remove a guild link. Returns False if no link existed."""
    db = await get_connection()
    cursor = await db.execute(
        "DELETE FROM guild_links WHERE source_guild_id = ?",
        (source_guild_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def is_support_guild(guild_id: int) -> bool:
    """Check if a guild is acting as a support guild for any source."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT 1 FROM guild_links WHERE support_guild_id = ? LIMIT 1",
        (guild_id,),
    )
    return await cursor.fetchone() is not None


async def get_source_guilds_for_support(support_guild_id: int) -> list[int]:
    """Get all source guild IDs linked to a support guild."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT source_guild_id FROM guild_links WHERE support_guild_id = ?",
        (support_guild_id,),
    )
    rows = await cursor.fetchall()
    return [row["source_guild_id"] for row in rows]


# ── Autoclose ─────────────────────────────────────────

async def schedule_autoclose(
    channel_id: int,
    close_at_iso: str,
    duration_label: str,
    closure_message: str | None = None,
) -> None:
    db = await get_connection()
    await db.execute(
        """UPDATE tickets
           SET scheduled_close_at = ?,
               autoclose_duration = ?,
               autoclose_closure_message = ?
           WHERE channel_id = ? AND status = 'open'""",
        (close_at_iso, duration_label, closure_message, channel_id),
    )
    await db.commit()


async def cancel_autoclose(channel_id: int) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE tickets
           SET scheduled_close_at = NULL,
               autoclose_duration = NULL,
               autoclose_closure_message = NULL
           WHERE channel_id = ? AND status = 'open'""",
        (channel_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_due_autocloses() -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        """SELECT * FROM tickets
           WHERE status = 'open'
             AND scheduled_close_at IS NOT NULL
             AND scheduled_close_at <= datetime('now')""",
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def clear_autoclose_flag(ticket_id: int) -> None:
    db = await get_connection()
    await db.execute(
        """UPDATE tickets
           SET scheduled_close_at = NULL,
               autoclose_duration = NULL,
               autoclose_closure_message = NULL
           WHERE id = ?""",
        (ticket_id,),
    )
    await db.commit()


# ── Response Reminders ───────────────────────────────────

async def create_reminder(ticket_id: int, staff_id: int, guild_id: int) -> bool:
    """Create a response reminder. Returns True if created, False if duplicate."""
    db = await get_connection()
    try:
        await db.execute(
            """INSERT INTO ticket_reminders (ticket_id, staff_id, guild_id)
               VALUES (?, ?, ?)""",
            (ticket_id, staff_id, guild_id),
        )
        await db.commit()
        return True
    except Exception:
        # UNIQUE constraint violation - duplicate reminder
        return False


async def get_reminders_for_ticket(ticket_id: int) -> list[dict]:
    """Get all active reminders for a ticket."""
    db = await get_connection()
    cursor = await db.execute(
        """SELECT * FROM ticket_reminders WHERE ticket_id = ?""",
        (ticket_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_reminder(ticket_id: int, staff_id: int) -> None:
    """Delete a specific reminder."""
    db = await get_connection()
    await db.execute(
        """DELETE FROM ticket_reminders WHERE ticket_id = ? AND staff_id = ?""",
        (ticket_id, staff_id),
    )
    await db.commit()


async def delete_all_reminders_for_ticket(ticket_id: int) -> None:
    """Delete all reminders for a ticket (e.g., on closure)."""
    db = await get_connection()
    await db.execute(
        """DELETE FROM ticket_reminders WHERE ticket_id = ?""",
        (ticket_id,),
    )
    await db.commit()


async def get_reminder_for_staff(ticket_id: int, staff_id: int) -> dict | None:
    """Check if a staff member has an active reminder on a ticket."""
    db = await get_connection()
    cursor = await db.execute(
        """SELECT * FROM ticket_reminders WHERE ticket_id = ? AND staff_id = ?""",
        (ticket_id, staff_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


# ── Transcripts ───────────────────────────────────────

async def create_transcript(
    ticket_id: int,
    user_id: int,
    source_guild_id: int | None,
    guild_id: int,
    channel_id: int,
    file_path: str,
    closed_by: int | None,
) -> int:
    db = await get_connection()
    cursor = await db.execute(
        """INSERT INTO transcripts
           (ticket_id, user_id, source_guild_id, guild_id, channel_id, file_path, closed_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ticket_id, user_id, source_guild_id, guild_id, channel_id, file_path, closed_by),
    )
    await db.commit()
    return cursor.lastrowid


async def update_transcript_log_reference(
    transcript_id: int,
    log_channel_id: int,
    transcript_message_id: int,
) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        """UPDATE transcripts
           SET log_channel_id = ?, transcript_message_id = ?
           WHERE id = ?""",
        (log_channel_id, transcript_message_id, transcript_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_transcript_by_ticket(ticket_id: int) -> dict | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM transcripts WHERE ticket_id = ? ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_transcripts_for_user(user_id: int, source_guild_id: int) -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        """SELECT * FROM transcripts
           WHERE user_id = ? AND COALESCE(source_guild_id, guild_id) = ?
           ORDER BY created_at DESC""",
        (user_id, source_guild_id),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_transcript_count_for_user(user_id: int, source_guild_id: int) -> int:
    db = await get_connection()
    cursor = await db.execute(
        """SELECT COUNT(*) AS count FROM transcripts
           WHERE user_id = ? AND COALESCE(source_guild_id, guild_id) = ?""",
        (user_id, source_guild_id),
    )
    row = await cursor.fetchone()
    return int(row["count"]) if row else 0


async def get_ticket_history_for_user(user_id: int, source_guild_id: int) -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        """SELECT t.*,
                  tr.id AS transcript_id,
                  tr.file_path AS transcript_file_path,
                  tr.created_at AS transcript_created_at
           FROM tickets t
           LEFT JOIN transcripts tr ON tr.ticket_id = t.id
           WHERE t.user_id = ?
             AND COALESCE(t.source_guild_id, t.guild_id) = ?
           ORDER BY t.created_at DESC""",
        (user_id, source_guild_id),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_ticket_history_count_for_user(user_id: int, source_guild_id: int) -> int:
    db = await get_connection()
    cursor = await db.execute(
        """SELECT COUNT(*) AS count FROM tickets
           WHERE user_id = ?
             AND COALESCE(source_guild_id, guild_id) = ?""",
        (user_id, source_guild_id),
    )
    row = await cursor.fetchone()
    return int(row["count"]) if row else 0


# ── Transcript Settings ───────────────────────────────

async def get_transcript_settings(guild_id: int) -> dict | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM transcript_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def set_transcript_log_channel(guild_id: int, channel_id: int | None) -> None:
    db = await get_connection()
    await db.execute(
        """INSERT INTO transcript_settings (guild_id, log_channel_id)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id""",
        (guild_id, channel_id),
    )
    await db.commit()


async def remove_transcript_log_channel(guild_id: int) -> None:
    db = await get_connection()
    await db.execute(
        """UPDATE transcript_settings SET log_channel_id = NULL WHERE guild_id = ?""",
        (guild_id,),
    )
    await db.commit()


async def set_transcript_user_sharing(guild_id: int, enabled: bool) -> None:
    db = await get_connection()
    await db.execute(
        """INSERT INTO transcript_settings (guild_id, allow_user_sharing)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET allow_user_sharing = excluded.allow_user_sharing""",
        (guild_id, 0),
    )
    await db.commit()


async def get_transcript_user_sharing(guild_id: int) -> bool:
    return False


# ── Staff Notes ───────────────────────────────────────

async def add_staff_note(
    user_id: int, source_guild_id: int,
    author_id: int, author_name: str, content: str,
) -> int:
    db = await get_connection()
    cursor = await db.execute(
        """INSERT INTO staff_notes
           (user_id, source_guild_id, author_id, author_name, content)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, source_guild_id, author_id, author_name, content),
    )
    await db.commit()
    return cursor.lastrowid


async def get_staff_notes(user_id: int, source_guild_id: int) -> list[dict]:
    db = await get_connection()
    cursor = await db.execute(
        """SELECT * FROM staff_notes
           WHERE user_id = ? AND source_guild_id = ?
           ORDER BY created_at DESC""",
        (user_id, source_guild_id),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_staff_note_count(user_id: int, source_guild_id: int) -> int:
    db = await get_connection()
    cursor = await db.execute(
        """SELECT COUNT(*) AS count FROM staff_notes
           WHERE user_id = ? AND source_guild_id = ?""",
        (user_id, source_guild_id),
    )
    row = await cursor.fetchone()
    return int(row["count"]) if row else 0


async def get_staff_note_by_id(note_id: int) -> dict | None:
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM staff_notes WHERE id = ?",
        (note_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def remove_staff_note(note_id: int) -> bool:
    db = await get_connection()
    cursor = await db.execute(
        "DELETE FROM staff_notes WHERE id = ?",
        (note_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


async def remove_all_staff_notes(user_id: int, source_guild_id: int) -> int:
    db = await get_connection()
    cursor = await db.execute(
        "DELETE FROM staff_notes WHERE user_id = ? AND source_guild_id = ?",
        (user_id, source_guild_id),
    )
    await db.commit()
    return cursor.rowcount


# ── Onboarding Persistence ─────────────────────────────

async def has_onboarding_state(scope: str, entity_id: int, guild_id: int) -> bool:
    """Check if a specific onboarding tip has already been delivered."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT 1 FROM onboarding_state WHERE scope = ? AND entity_id = ? AND guild_id = ?",
        (scope, entity_id, guild_id),
    )
    row = await cursor.fetchone()
    return row is not None


async def set_onboarding_state(
    scope: str,
    entity_id: int,
    guild_id: int,
) -> None:
    """Mark an onboarding tip/guidance as delivered."""
    db = await get_connection()
    await db.execute(
        "INSERT OR REPLACE INTO onboarding_state (scope, entity_id, guild_id, delivered_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (scope, entity_id, guild_id),
    )
    await db.commit()


# ── Role Permissions (Phase 6: Granular Staff Permissions) ──

async def get_role_denied_capabilities(guild_id: int, role_id: int) -> list[str]:
    """Get all denied capabilities for a specific role."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT capability FROM role_permissions WHERE guild_id = ? AND role_id = ?",
        (guild_id, role_id),
    )
    rows = await cursor.fetchall()
    return [row["capability"] for row in rows]


async def deny_capability_for_role(guild_id: int, role_id: int, capability: str) -> None:
    """Deny a capability for a specific role."""
    db = await get_connection()
    await db.execute(
        "INSERT OR IGNORE INTO role_permissions (guild_id, role_id, capability) VALUES (?, ?, ?)",
        (guild_id, role_id, capability),
    )
    await db.commit()


async def allow_capability_for_role(guild_id: int, role_id: int, capability: str) -> None:
    """Remove a denial for a capability for a specific role."""
    db = await get_connection()
    await db.execute(
        "DELETE FROM role_permissions WHERE guild_id = ? AND role_id = ? AND capability = ?",
        (guild_id, role_id, capability),
    )
    await db.commit()


async def get_all_role_permission_profiles(guild_id: int) -> dict[str, list[int]]:
    """
    Get all role permission profiles grouped by denied capability set.
    Returns a dict mapping frozenset of denied capabilities to list of role IDs.
    """
    db = await get_connection()
    cursor = await db.execute(
        "SELECT role_id, capability FROM role_permissions WHERE guild_id = ?",
        (guild_id,),
    )
    rows = await cursor.fetchall()

    # Build mapping: role_id -> set of denied capabilities
    role_to_denied: dict[int, set[str]] = {}
    for row in rows:
        role_id = row["role_id"]
        capability = row["capability"]
        if role_id not in role_to_denied:
            role_to_denied[role_id] = set()
        role_to_denied[role_id].add(capability)

    # Group roles by denied capability set
    profiles: dict[str, list[int]] = {}
    for role_id, denied_set in role_to_denied.items():
        # Convert set to sorted tuple for dict key
        key = ",".join(sorted(denied_set)) if denied_set else ""
        if key not in profiles:
            profiles[key] = []
        profiles[key].append(role_id)

    # Also include roles with no denials (empty set)
    support_role_ids = await get_support_roles(guild_id)
    for role_id in support_role_ids:
        if role_id not in role_to_denied:
            if "" not in profiles:
                profiles[""] = []
            profiles[""].append(role_id)

    return profiles
