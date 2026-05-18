"""
Relay Bot — Relay Service
Handles the core DM ↔ staff channel message relay.

All relay messages are plain text for operational readability.
Embeds are reserved for system events only (ticket open/close/claim).
"""

from __future__ import annotations

import logging

import discord
from bot.database import queries
from bot.services.permission_service import resolve_staff_role_label

log = logging.getLogger(__name__)


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
    log.info(
        "[DM_RELAY_USER] User %s (%s) attempting to relay message to staff",
        message.author.id, message.author.name
    )
    
    from bot.services import ticket_service
    ticket = await ticket_service.get_active_ticket_for_user(bot, message.author.id)
    if ticket is None:
        log.warning(
            "[DM_RELAY_USER] No active ticket found for user %s (%s)",
            message.author.id, message.author.name
        )
        return False

    log.debug(
        "[DM_RELAY_USER] Found active ticket %s for user %s in channel %s",
        ticket["id"], message.author.id, ticket["channel_id"]
    )
    
    channel = bot.get_channel(ticket["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(ticket["channel_id"])
            log.debug("[DM_RELAY_USER] Fetched channel %s via API", ticket["channel_id"])
        except discord.NotFound:
            log.error(
                "[DM_RELAY_USER] Channel %s not found for ticket %s - channel may have been deleted",
                ticket["channel_id"], ticket["id"]
            )
            return False
        except discord.Forbidden as e:
            log.error(
                "[DM_RELAY_USER] Forbidden when fetching channel %s for ticket %s: %s",
                ticket["channel_id"], ticket["id"], e
            )
            return False
        except Exception as e:
            log.error(
                "[DM_RELAY_USER] Unexpected error fetching channel %s for ticket %s: %s",
                ticket["channel_id"], ticket["id"], e, exc_info=True
            )
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
    
    try:
        await channel.send(relay_text)
        log.info(
            "[DM_RELAY_USER] Successfully relayed message from user %s to channel %s (ticket %s)",
            message.author.id, channel.id, ticket["id"]
        )
        return True
    except discord.Forbidden as e:
        log.error(
            "[DM_RELAY_USER] Forbidden when sending to channel %s (ticket %s): %s - missing Send Messages permission",
            channel.id, ticket["id"], e
        )
        return False
    except discord.HTTPException as e:
        log.error(
            "[DM_RELAY_USER] HTTP error when sending to channel %s (ticket %s): %s",
            channel.id, ticket["id"], e, exc_info=True
        )
        return False
    except Exception as e:
        log.error(
            "[DM_RELAY_USER] Unexpected error when sending to channel %s (ticket %s): %s",
            channel.id, ticket["id"], e, exc_info=True
        )
        return False


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
    log.info(
        "[DM_RELAY_STAFF] Staff %s (%s) attempting to relay %s reply to channel %s",
        staff_member.id, staff_member.name, "anonymous" if anonymous else "visible", channel_id
    )
    
    ticket = await queries.get_ticket_by_channel(channel_id)
    if ticket is None:
        log.warning(
            "[DM_RELAY_STAFF] No ticket found for channel %s",
            channel_id
        )
        return False
    
    if ticket.get("relay_session_status", "active") != "active":
        log.warning(
            "[DM_RELAY_STAFF] Relay session not active for ticket %s (status: %s)",
            ticket["id"], ticket.get("relay_session_status")
        )
        return False

    try:
        user = await bot.fetch_user(ticket["user_id"])
    except discord.NotFound:
        log.error(
            "[DM_RELAY_STAFF] User %s not found for ticket %s - user may have been deleted",
            ticket["user_id"], ticket["id"]
        )
        return False
    except discord.HTTPException as e:
        log.error(
            "[DM_RELAY_STAFF] HTTP error fetching user %s for ticket %s: %s",
            ticket["user_id"], ticket["id"], e, exc_info=True
        )
        return False

    log.debug(
        "[DM_RELAY_STAFF] Fetched user %s for ticket %s",
        ticket["user_id"], ticket["id"]
    )

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
        log.info(
            "[DM_SUCCESS] Successfully sent DM to user %s for ticket %s",
            ticket["user_id"], ticket["id"]
        )
    except discord.Forbidden as e:
        log.error(
            "[DM_FAILED] Forbidden when sending DM to user %s (ticket %s): %s - user has DMs disabled or blocked the bot",
            ticket["user_id"], ticket["id"], e
        )
        return False
    except discord.HTTPException as e:
        log.error(
            "[DM_FAILED] HTTP error when sending DM to user %s (ticket %s): %s",
            ticket["user_id"], ticket["id"], e, exc_info=True
        )
        return False
    except Exception as e:
        log.error(
            "[DM_FAILED] Unexpected error when sending DM to user %s (ticket %s): %s",
            ticket["user_id"], ticket["id"], e, exc_info=True
        )
        return False

    # Post confirmation in staff channel (same plain format)
    staff_channel = bot.get_channel(channel_id)
    if staff_channel:
        prefix = "📨 *Anonymous reply sent*" if anonymous else "📨 *Reply sent*"
        confirm_parts = [prefix, "", header, content]
        if attachment_text:
            confirm_parts.append(attachment_text)
        try:
            await staff_channel.send("\n".join(confirm_parts))
            log.debug(
                "[DM_RELAY_STAFF] Posted confirmation to staff channel %s",
                channel_id
            )
        except discord.Forbidden as e:
            log.error(
                "[DM_RELAY_STAFF] Forbidden when posting confirmation to channel %s: %s",
                channel_id, e
            )
        except Exception as e:
            log.error(
                "[DM_RELAY_STAFF] Error posting confirmation to channel %s: %s",
                channel_id, e, exc_info=True
            )

    return True
