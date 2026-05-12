"""
Relay Bot — Relay Service
Handles the core DM ↔ staff channel message relay.

All relay messages are plain text for operational readability.
Embeds are reserved for system events only (ticket open/close/claim).
"""

from __future__ import annotations

import discord
from bot.database import queries
from bot.services.permission_service import resolve_staff_role_label


def _format_attachment_links(attachments: list[discord.Attachment]) -> str:
    """Format attachment URLs as plain links, one per line."""
    if not attachments:
        return ""
    lines = ["\n[Attachment]"]
    for att in attachments:
        lines.append(att.url)
    return "\n".join(lines)


async def relay_user_to_staff(
    bot: discord.Client,
    message: discord.Message,
) -> bool:
    """
    Forward a user's DM into the corresponding staff ticket channel.
    Plain text format:  __username:\n message
    Returns True if the message was relayed successfully.
    """
    from bot.services import ticket_service
    ticket = await ticket_service.get_active_ticket_for_user(bot, message.author.id)
    if ticket is None:
        return False

    channel = bot.get_channel(ticket["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(ticket["channel_id"])
        except discord.NotFound:
            return False

    # Build plain text message
    username = message.author.name
    content = message.content or ""

    parts = [f"__{username}:__"]
    if content:
        parts.append(content)

    # Append attachment URLs (no file uploads, no embeds)
    if message.attachments:
        parts.append(_format_attachment_links(message.attachments))

    relay_text = "\n".join(parts)
    await channel.send(relay_text)
    return True


async def relay_staff_to_user(
    bot: discord.Client,
    channel_id: int,
    staff_member: discord.Member,
    content: str,
    *,
    anonymous: bool = False,
    attachments: list[discord.Attachment] | None = None,
) -> bool:
    """
    Relay a staff reply (via /reply or /anreply) back to the user's DMs.
    Plain text format for both user DM and staff channel confirmation.
    Returns True on success.
    """
    ticket = await queries.get_ticket_by_channel(channel_id)
    if ticket is None:
        return False
    if ticket.get("relay_session_status", "active") != "active":
        return False

    try:
        user = await bot.fetch_user(ticket["user_id"])
    except discord.NotFound:
        return False

    # Resolve the staff member's highest configured support role
    role_label = await resolve_staff_role_label(staff_member, ticket["guild_id"])

    # Build plain text for user DM
    if anonymous:
        header = f"[{role_label}]:"
    else:
        header = f"[{role_label}] {staff_member.display_name}:"

    parts = [header, content]

    # Append attachment URLs only (no file uploads, no embeds)
    attachment_text = ""
    if attachments:
        attachment_text = _format_attachment_links(attachments)
        parts.append(attachment_text)

    dm_text = "\n".join(parts)

    # Send to user DM
    try:
        await user.send(dm_text)
    except discord.Forbidden:
        return False

    # Post confirmation in staff channel (same plain format)
    staff_channel = bot.get_channel(channel_id)
    if staff_channel:
        prefix = "📨 *Anonymous reply sent*" if anonymous else "📨 *Reply sent*"
        confirm_parts = [prefix, "", header, content]
        if attachment_text:
            confirm_parts.append(attachment_text)
        await staff_channel.send("\n".join(confirm_parts))

    return True
