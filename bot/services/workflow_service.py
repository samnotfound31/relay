"""
Relay Bot — Workflow Service
Handles ticket state transitions, channel renaming, and inactivity.

Ticket lifecycle: open → active → claimed/transferred → inactive(opt) → closed → deleted
Closed tickets are immediately removed. No archive, no persistence.

Channel name format: [staff emoji][status emoji]-username
Max 2 emojis: 1 ownership + 1 workflow status.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import discord
from bot.config import STATUS_EMOJIS
from bot.database import queries
from bot.services.emoji_service import get_emoji_for_staff
from bot.services import permission_audit, rename_governance

log = logging.getLogger("relay.workflow")

RENAME_DEFERRED_MSG = (
    "⚠️ Channel rename deferred to avoid rate limits. "
    "Workflow state updated successfully."
)


def _sanitize_username(name: str) -> str:
    """Sanitize a username for use as a Discord channel name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", name.lower())
    return sanitized[:80] or "user"


async def build_channel_name(
    guild_id: int,
    username: str,
    *,
    claimed_by: int | None = None,
    ticket_status: str = "open",
    is_inactive: bool = False,
) -> str:
    """
    Build the ticket channel name based on current state.

    Priority: inactive ⏰ > owner emoji + status emoji > owner emoji > plain
    Max 2 emojis total (1 owner + 1 status).

    Examples:
        ⏰-sam           (inactive override)
        🧛🔍-sam         (claimed + investigating)
        🧛⏳-sam         (claimed + waiting-user)
        🧛-sam           (claimed, no workflow status)
        sam              (unclaimed, open)
    """
    clean_name = _sanitize_username(username)

    # Inactivity overrides everything
    if is_inactive:
        return f"⏰-{clean_name}"

    prefix = ""

    # Owner emoji (max 1)
    if claimed_by:
        staff_emoji = await get_emoji_for_staff(guild_id, claimed_by)
        if staff_emoji:
            prefix += staff_emoji

    # Workflow status emoji (max 1)
    status_emoji = STATUS_EMOJIS.get(ticket_status, "")
    if status_emoji:
        prefix += status_emoji

    if prefix:
        return f"{prefix}-{clean_name}"

    return clean_name


async def rename_ticket_channel(
    channel: discord.TextChannel,
    ticket: dict,
    bot: discord.Client,
) -> str | None:
    """
    Rename a ticket channel to reflect current state.
    Skips if name unchanged. Returns rate-limit warning or None.
    """
    try:
        user = await bot.fetch_user(ticket["user_id"])
        username = user.name
    except Exception:
        username = "user"

    new_name = await build_channel_name(
        guild_id=ticket["guild_id"],
        username=username,
        claimed_by=ticket.get("claimed_by"),
        ticket_status=ticket.get("ticket_status", "open"),
        is_inactive=bool(ticket.get("is_inactive", 0)),
    )

    # Rate-limit protection: skip if name unchanged
    if channel.name == new_name:
        return None

    try:
        await channel.edit(name=new_name)
        return None
    except discord.HTTPException as e:
        if e.status == 429:
            log.warning(f"Rate limited renaming channel {channel.id}")
            await queries.flag_rename_resync(channel.id)
            return RENAME_DEFERRED_MSG
        log.warning(f"Failed to rename channel {channel.id}: {e}")
        return None


async def governed_rename_ticket_channel(
    channel: discord.TextChannel,
    ticket: dict,
    bot: discord.Client,
) -> str | None:
    """
    Centralized cosmetic rename path with proactive local rate-budget governance.
    Skips Discord API entirely if budget exhausted; defers to resync loop.
    """
    if not rename_governance.can_rename(channel.id):
        log.info(
            f"Rename budget exhausted for channel {channel.id}; deferring to resync."
        )
        await queries.flag_rename_resync(channel.id)
        return RENAME_DEFERRED_MSG

    result = await rename_ticket_channel(channel, ticket, bot)
    if result is None:
        # Success or already correct — record consumption
        rename_governance.record_rename(channel.id)
        return None

    # Defensive: rename_ticket_channel hit an actual 429 (shouldn't happen with
    # governance, but preserve fallback safety). Don't consume budget.
    return result


async def perform_rename_resync(
    bot: discord.Client,
    ticket: dict,
) -> bool:
    """
    Attempt a deferred cosmetic rename resync.
    Always recomputes the desired name from CURRENT authoritative DB state.
    Returns True if the rename was applied (or already correct), False if deferred again.
    """
    # Refresh ticket from DB to get latest authoritative state
    fresh = await queries.get_ticket_by_channel(ticket["channel_id"])
    if fresh is None:
        # Ticket closed or channel gone — clear flag
        await queries.clear_rename_resync(ticket["channel_id"])
        return True

    channel = bot.get_channel(ticket["channel_id"])
    if channel is None:
        await queries.clear_rename_resync(ticket["channel_id"])
        return True

    result = await governed_rename_ticket_channel(channel, fresh, bot)
    if result is None:
        # Success or already correct — clear flag
        await queries.clear_rename_resync(ticket["channel_id"])
        return True

    # Budget exhausted or actual 429 — flag remains set, retry next cycle
    return False


async def claim_ticket(
    channel: discord.TextChannel,
    staff_member: discord.Member,
    bot: discord.Client,
) -> tuple[bool, str]:
    """Claim a ticket and rename the channel with owner emoji."""
    ticket = await queries.get_ticket_by_channel(channel.id)
    if ticket is None:
        return False, "No open ticket found in this channel."

    if ticket["claimed_by"]:
        claimer = channel.guild.get_member(ticket["claimed_by"])
        name = claimer.display_name if claimer else f"User {ticket['claimed_by']}"
        return False, f"Already claimed by **{name}**."

    await queries.claim_ticket(channel.id, staff_member.id)
    ticket["claimed_by"] = staff_member.id
    warning = await governed_rename_ticket_channel(channel, ticket, bot)
    msg = f"Ticket claimed by {staff_member.mention}."
    if warning:
        msg += f"\n\n{warning}"
    return True, msg


async def transfer_ticket(
    channel: discord.TextChannel,
    new_staff: discord.Member,
    bot: discord.Client,
) -> tuple[bool, str]:
    """Transfer ticket ownership. Preserves workflow status emoji."""
    ticket = await queries.get_ticket_by_channel(channel.id)
    if ticket is None:
        return False, "No open ticket found in this channel."

    await queries.transfer_ticket(channel.id, new_staff.id)
    ticket["claimed_by"] = new_staff.id
    warning = await governed_rename_ticket_channel(channel, ticket, bot)
    msg = f"Ticket transferred to {new_staff.mention}."
    if warning:
        msg += f"\n\n{warning}"
    return True, msg


async def set_ticket_status(
    channel: discord.TextChannel,
    status: str,
    bot: discord.Client,
) -> tuple[bool, str]:
    """
    Update the workflow status of a ticket and rename the channel.

    Special: '/status open' returns ticket to queue —
    clears ownership, removes all emojis, allows re-claim.
    """
    ticket = await queries.get_ticket_by_channel(channel.id)
    if ticket is None:
        return False, "No open ticket found in this channel."

    if status == "open":
        # Return to queue: clear ownership + status
        await queries.unclaim_ticket(channel.id)
        await queries.update_ticket_status(channel.id, "open")
        ticket["claimed_by"] = None
        ticket["ticket_status"] = "open"
        warning = await governed_rename_ticket_channel(channel, ticket, bot)
        msg = "Ticket returned to open queue. Ownership cleared."
        if warning:
            msg += f"\n\n{warning}"
        return True, msg

    await queries.update_ticket_status(channel.id, status)
    ticket["ticket_status"] = status
    warning = await governed_rename_ticket_channel(channel, ticket, bot)

    status_emoji = STATUS_EMOJIS.get(status, "")
    label = f"{status_emoji} **{status}**" if status_emoji else f"**{status}**"
    msg = f"Status updated to {label}."
    if warning:
        msg += f"\n\n{warning}"
    return True, msg


async def move_ticket(
    channel: discord.TextChannel,
    target_category_name: str,
    guild: discord.Guild,
) -> tuple[bool, str]:
    """
    Move a ticket channel to a different ticket category.
    Preserves all workflow state.
    """
    ticket = await queries.get_ticket_by_channel(channel.id)
    if ticket is None:
        return False, "No open ticket found in this channel."

    # Check if already in the target category
    if ticket.get("category_name") == target_category_name:
        return False, f"Ticket is already in **{target_category_name}**."

    # Validate target category exists
    cat_data = await queries.get_category_by_name(guild.id, target_category_name)
    if cat_data is None:
        return False, f"Category **{target_category_name}** does not exist."

    # Find the Discord category
    dc_cat_id = cat_data.get("discord_category_id")
    if not dc_cat_id:
        return False, f"Category **{target_category_name}** has no Discord category."

    dc_category = guild.get_channel(dc_cat_id)
    if dc_category is None:
        return False, f"Discord category for **{target_category_name}** not found."

    # Move channel
    try:
        await channel.edit(category=dc_category)
    except discord.HTTPException as e:
        if e.status == 429:
            return True, f"📂 DB updated but rate limited — channel will appear in the new category shortly."
        return False, f"Failed to move channel: {e}"

    # Update DB
    await queries.move_ticket_category(channel.id, target_category_name)

    emoji = cat_data.get("emoji") or "📂"
    return True, f"📂 Ticket moved to {emoji} **{target_category_name}**."


async def _get_or_create_transcript_log_channel(
    guild: discord.Guild,
) -> discord.TextChannel | None:
    """Return the configured transcript log channel, or auto-create one."""
    audit = permission_audit.audit_permissions(
        guild,
        "transcript_logging",
        context="transcript_log_channel_init",
    )
    if not audit.ok:
        log.error(
            "[TRANSCRIPT_LOG_PERMISSIONS] Cannot initialize transcript logging in guild %s missing=%s",
            guild.id, audit.missing_labels,
        )
        return None

    settings = await queries.get_transcript_settings(guild.id)
    if settings and settings.get("log_channel_id"):
        ch = guild.get_channel(settings["log_channel_id"])
        if isinstance(ch, discord.TextChannel):
            audit = permission_audit.audit_permissions(
                guild,
                "transcript_logging",
                channel=ch,
                context="transcript_log_channel_existing",
            )
            if not audit.ok:
                log.error(
                    "[TRANSCRIPT_LOG_PERMISSIONS] Existing transcript channel %s missing=%s",
                    ch.id, audit.missing_labels,
                )
                return None
            return ch

    # Auto-create relay-transcripts channel
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True, attach_files=True,
        ),
    }
    # Grant admins visibility
    for role in guild.roles:
        if role.permissions.administrator and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
            )

    try:
        channel = await guild.create_text_channel(
            "🔒 relay-transcripts",
            overwrites=overwrites,
            reason="Relay auto-created transcript log channel",
        )
    except discord.Forbidden as e:
        audit = permission_audit.audit_permissions(
            guild,
            "transcript_logging",
            context="transcript_log_channel_forbidden",
        )
        log.error(
            "[TRANSCRIPT_LOG_PERMISSIONS] Forbidden creating transcript channel in guild %s: %s missing=%s",
            guild.id, e, audit.missing_labels,
        )
        return None

    await queries.set_transcript_log_channel(guild.id, channel.id)
    return channel


async def close_ticket(
    channel: discord.TextChannel,
    closed_by: discord.Member,
    bot: discord.Client,
    closure_message: str | None = None,
) -> tuple[bool, dict | None]:
    """
    Close a ticket — FINAL action.
    1. Mark closed in DB
    2. Generate transcript PDF
    3. Log transcript to internal transcript channel
    4. DM user closure confirmation
    5. Delete the channel entirely
    """
    ticket = await queries.close_ticket(channel.id)
    if ticket is None:
        return False, None

    # Clear all response reminders for this ticket
    await queries.delete_all_reminders_for_ticket(ticket["id"])

    guild = channel.guild
    source_guild_id = ticket.get("source_guild_id")

    # ── Transcript generation ──
    from bot.services import transcript_service
    try:
        filepath = await transcript_service.generate_transcript(channel, ticket, closed_by)
    except Exception as e:
        log.warning(f"Transcript generation failed for ticket {ticket['id']}: {e}")
        filepath = None

    if filepath:
        try:
            transcript_id = await queries.create_transcript(
                ticket_id=ticket["id"],
                user_id=ticket["user_id"],
                source_guild_id=source_guild_id,
                guild_id=guild.id,
                channel_id=channel.id,
                file_path=filepath,
                closed_by=closed_by.id,
            )

            # Log to transcript channel
            log_channel = await _get_or_create_transcript_log_channel(guild)
            if log_channel:
                try:
                    file = discord.File(filepath, filename=Path(filepath).name)
                    transcript_msg = await log_channel.send(
                        content=f"🔒 Transcript for ticket `#{ticket.get('community_ticket_number') or ticket['id']}` "
                                f"closed by <@{closed_by.id}>.",
                        file=file,
                    )
                    await queries.update_transcript_log_reference(
                        transcript_id,
                        log_channel.id,
                        transcript_msg.id,
                    )
                except Exception as e:
                    log.warning(f"Failed to send transcript to log channel: {e}")
        except Exception as e:
            log.warning(f"Transcript logging failed for ticket {ticket['id']}: {e}")

    # DM the user (closure confirmation)
    try:
        from bot.services import message_style
        user = await bot.fetch_user(ticket["user_id"])
        display_ticket_number = ticket.get("community_ticket_number") or ticket["id"]
        source_guild = bot.get_guild(source_guild_id) if source_guild_id else None
        display_guild = source_guild or guild
        guild_icon_url = display_guild.icon.url if display_guild.icon else None
        final_message = closure_message or ticket.get("autoclose_closure_message")
        embed = message_style.ticket_closed_user_embed(
            display_ticket_number,
            display_guild.name,
            guild_icon_url,
            final_message,
        )
        await user.send(embed=embed)
    except Exception:
        pass

    # Delete the channel
    try:
        await channel.delete(reason=f"Ticket #{ticket['id']} closed by {closed_by}")
    except discord.Forbidden as e:
        audit = permission_audit.audit_permissions(
            guild,
            "category_management",
            channel=channel,
            context="close_ticket_delete_forbidden",
        )
        log.error(
            "[PERMISSION_ERROR] Cannot delete ticket channel %s: %s missing=%s",
            channel.id, e, audit.missing_labels,
        )
    except (discord.HTTPException, discord.NotFound) as e:
        log.warning("Failed to delete ticket channel %s: %s", channel.id, e)

    return True, ticket


async def mark_inactive(
    channel: discord.TextChannel,
    ticket: dict,
    bot: discord.Client,
) -> None:
    """Mark a ticket as inactive and rename with ⏰ override."""
    await queries.mark_ticket_inactive(ticket["id"])
    ticket["is_inactive"] = 1
    await governed_rename_ticket_channel(channel, ticket, bot)
