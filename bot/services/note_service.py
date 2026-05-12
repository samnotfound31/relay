"""
Relay Bot — Note Service
Community-scoped staff notes (user_id + source_guild_id).
Moderation continuity, not chat history.
"""

from __future__ import annotations

from bot.database import queries


async def add_note(
    user_id: int,
    source_guild_id: int,
    author_id: int,
    author_name: str,
    content: str,
) -> int:
    """Add a staff note. Returns note ID."""
    return await queries.add_staff_note(
        user_id, source_guild_id, author_id, author_name, content,
    )


async def get_notes(user_id: int, source_guild_id: int) -> list[dict]:
    """Get all notes for a user scoped to a source guild."""
    return await queries.get_staff_notes(user_id, source_guild_id)


async def can_remove(note: dict, actor_id: int, is_admin: bool) -> bool:
    """Check if actor may remove a note. Admins may remove any note."""
    if is_admin:
        return True
    return note.get("author_id") == actor_id


async def remove_note(note_id: int, actor_id: int, is_admin: bool) -> tuple[bool, str]:
    """Remove a single note with permission check."""
    note = await queries.get_staff_note_by_id(note_id)
    if note is None:
        return False, "Note not found."
    if not await can_remove(note, actor_id, is_admin):
        return False, "You can only remove your own notes."
    success = await queries.remove_staff_note(note_id)
    return success, "Note removed." if success else "Failed to remove note."


async def remove_all_notes(
    user_id: int, source_guild_id: int, actor_id: int, is_admin: bool,
) -> tuple[int, str]:
    """Remove all notes for a user in a community. Admin-only."""
    if not is_admin:
        return 0, "Only admins can clear all notes."
    count = await queries.remove_all_staff_notes(user_id, source_guild_id)
    return count, f"Removed {count} note(s)."
