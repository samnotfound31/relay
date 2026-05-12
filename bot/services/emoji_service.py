"""
Relay Bot — Emoji Service
Staff personal emoji management, validation, and persistent registry.
"""

from __future__ import annotations

import discord
from bot.config import RESERVED_EMOJIS
from bot.database import queries
from bot.services import message_style


async def validate_and_set_emoji(
    guild_id: int, user_id: int, emoji: str,
) -> tuple[bool, str]:
    """
    Validate and set a staff emoji.
    Returns (success, message).
    """
    # Check reserved
    if emoji in RESERVED_EMOJIS:
        reserved_list = "  ".join(sorted(RESERVED_EMOJIS))
        return False, f"That emoji is reserved for system use.\nReserved: {reserved_list}"

    # Check if taken by another staff member
    if await queries.is_emoji_taken(guild_id, emoji, exclude_user_id=user_id):
        return False, "That emoji is already claimed by another staff member."

    # Set it
    success = await queries.set_staff_emoji(guild_id, user_id, emoji)
    if success:
        return True, f"Your staff emoji is now {emoji}"
    else:
        return False, "Failed to set emoji — it may already be in use."


async def get_emoji_for_staff(guild_id: int, user_id: int) -> str | None:
    """Get a staff member's configured emoji, or None."""
    return await queries.get_staff_emoji(guild_id, user_id)


async def get_emoji_registry(guild_id: int) -> list[dict]:
    """Get all staff emojis for a guild: [{user_id, emoji}, ...]"""
    return await queries.get_all_staff_emojis(guild_id)


def _build_registry_content(
    registry: list[dict],
    guild: discord.Guild,
) -> discord.Embed | None:
    """Build the emoji registry embed. Returns None if no entries."""
    if not registry:
        return None

    lines = []
    for entry in registry:
        member = guild.get_member(entry["user_id"])
        name = member.display_name if member else f"User {entry['user_id']}"
        lines.append(f"{entry['emoji']}  {name}")

    return message_style.relay_embed(
        title="Staff Emoji Registry",
        description="\n".join(lines),
        footer=f"{len(registry)} staff members",
    )


async def refresh_emojilist(guild: discord.Guild, bot: discord.Client) -> None:
    """
    Auto-update the persistent emoji registry message if one exists.
    Compares content before editing to avoid unnecessary PATCH calls.
    """
    settings = await queries.get_guild_settings(guild.id)
    if not settings:
        return

    channel_id = settings.get("emojilist_channel_id")
    message_id = settings.get("emojilist_message_id")
    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        return

    registry = await get_emoji_registry(guild.id)
    new_embed = _build_registry_content(registry, guild)

    try:
        msg = await channel.fetch_message(message_id)  # type: ignore
    except (discord.NotFound, discord.Forbidden):
        # Message deleted — clear the reference
        await queries.upsert_guild_settings(
            guild.id, emojilist_channel_id=None, emojilist_message_id=None,
        )
        return

    if new_embed is None:
        # No entries — edit to show empty state
        empty_embed = message_style.relay_embed(
            title="Staff Emoji Registry",
            description="No staff emojis configured yet.",
        )
        await msg.edit(embed=empty_embed)
        return

    # Compare description to avoid unnecessary edits
    if msg.embeds and msg.embeds[0].description == new_embed.description:
        return  # Content unchanged

    await msg.edit(embed=new_embed)
