"""
Relay Bot — Ticket Service
Handles ticket creation and category-based Discord organization.
Phase 3: cross-server routing — tickets may be created in a linked support guild.
"""

from __future__ import annotations

import logging
import re

import discord
from bot.database import queries
from bot.services.permission_service import build_category_overwrites
from bot.services import message_style

log = logging.getLogger(__name__)


def _sanitize_username(name: str) -> str:
    """Sanitize a username for use as a Discord channel name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", name.lower())
    return sanitized[:80] or "user"


async def _get_or_create_discord_category(
    guild: discord.Guild,
    category_name: str | None = None,
) -> discord.CategoryChannel:
    """
    Find or create the Discord channel category for a ticket category.
    Each ticket category gets its own Discord category.
    Falls back to a general 'Relay Tickets' category if no category specified.
    
    Raises:
        discord.Forbidden: If bot lacks Manage Channels permission
        discord.HTTPException: If category creation fails
    """
    if category_name:
        # Look up the ticket category in DB
        cat_data = await queries.get_category_by_name(guild.id, category_name)
        if cat_data and cat_data.get("discord_category_id"):
            existing = guild.get_channel(cat_data["discord_category_id"])
            if existing:
                log.info("Using existing category %s for category %s in guild %s", existing.id, category_name, guild.id)
                return existing  # type: ignore

        # Create with emoji prefix
        emoji = (cat_data.get("emoji") if cat_data else None) or "📂"
        display_name = f"{emoji} {category_name}"

        log.info("Creating category %s in guild %s", display_name, guild.id)
        overwrites = await build_category_overwrites(guild)
        category = await guild.create_category(display_name, overwrites=overwrites)
        log.info("Successfully created category %s (ID: %s) in guild %s", display_name, category.id, guild.id)

        if cat_data:
            await queries.update_category_discord_id(
                guild.id, category_name, category.id,
            )
        return category
    else:
        # No category specified — use guild-level fallback
        settings = await queries.get_guild_settings(guild.id)
        if settings and settings.get("ticket_category_id"):
            existing = guild.get_channel(settings["ticket_category_id"])
            if existing:
                log.info("Using existing fallback category %s in guild %s", existing.id, guild.id)
                return existing  # type: ignore

        log.info("Creating fallback 'Relay Tickets' category in guild %s", guild.id)
        overwrites = await build_category_overwrites(guild)
        category = await guild.create_category("Relay Tickets", overwrites=overwrites)
        log.info("Successfully created fallback category %s (ID: %s) in guild %s", category.name, category.id, guild.id)
        await queries.upsert_guild_settings(guild.id, ticket_category_id=category.id)
        return category


async def resolve_target_guild(
    source_guild: discord.Guild,
    bot: discord.Client,
) -> tuple[discord.Guild, int | None]:
    """
    Determine where a ticket should be created.
    Returns (target_guild, source_guild_id_or_None).

    If the source guild is linked → return (support_guild, source_guild.id)
    If not linked → return (source_guild, None) — local mode.
    """
    support_id = await queries.get_support_guild_id(source_guild.id)
    if support_id is None:
        return source_guild, None  # Local mode

    support_guild = bot.get_guild(support_id)
    if support_guild is None:
        # Support guild unavailable — fallback to local
        return source_guild, None

    return support_guild, source_guild.id


async def get_active_ticket_for_user(
    bot: discord.Client,
    user_id: int,
) -> dict | None:
    tickets = await queries.get_open_tickets_by_user_any_guild(user_id)
    for ticket in tickets:
        channel = bot.get_channel(ticket["channel_id"])
        if channel is None:
            # Close stale records regardless of session status so
            # community uniqueness locks don't stay wedged forever.
            await queries.close_stale_ticket(ticket["id"])
            continue
        if ticket.get("relay_session_status", "active") != "active":
            continue
        return ticket
    return None


async def open_ticket(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    category_name: str | None = None,
    *,
    bot: discord.Client | None = None,
    source_guild: discord.Guild | None = None,
) -> tuple[int, discord.TextChannel] | str | None:
    """
    Open a new ticket.
    Returns (ticket_id, channel) on success,
    a str with block reason, or None on generic failure.

    Phase 3: If source_guild is provided and linked, the ticket channel is
    created in the support guild. source_guild tracks where the user came from.
    
    This function now includes comprehensive exception handling to prevent
    silent failures when the bot lacks permissions.
    """
    log.info(
        "Ticket creation started for user %s (%s) in guild %s, category: %s",
        user.name, user.id, guild.id, category_name or "default"
    )
    
    # Determine target guild for channel creation
    target_guild = guild
    source_guild_id = None

    try:
        if bot and source_guild:
            target_guild, source_guild_id = await resolve_target_guild(source_guild, bot)
        elif bot:
            # Auto-resolve: if guild is linked, route to support
            target_guild, source_guild_id = await resolve_target_guild(guild, bot)
        
        log.info("Target guild resolved to %s (source_guild_id: %s)", target_guild.id, source_guild_id)
    except Exception as e:
        log.error("Failed to resolve target guild: %s", e, exc_info=True)
        return "Failed to resolve target server for ticket creation."

    # Community uniqueness: one open ticket per source guild (persists after /leave)
    community_guild_id = source_guild_id or guild.id
    try:
        existing_community = await queries.get_open_ticket_by_user_and_source_guild(
            user.id, community_guild_id,
        )
        if existing_community:
            if bot:
                channel = bot.get_channel(existing_community["channel_id"])
                if channel is None:
                    await queries.close_stale_ticket(existing_community["id"])
                else:
                    log.info("Ticket creation blocked: user %s has existing ticket %s in community %s", user.id, existing_community["id"], community_guild_id)
                    return (
                        "You already have an existing ticket open in this community.\n"
                        "Please wait for staff to close it before opening another."
                    )
            else:
                return (
                    "You already have an existing ticket open in this community.\n"
                    "Please wait for staff to close it before opening another."
                )
    except Exception as e:
        log.error("Failed to check existing tickets: %s", e, exc_info=True)
        return "Failed to check for existing tickets. Please try again."

    # Global relay session lock: one active DM session globally
    if bot:
        try:
            existing_session = await get_active_ticket_for_user(bot, user.id)
            if existing_session:
                log.info("Ticket creation blocked: user %s has active relay session in ticket %s", user.id, existing_session["id"])
                return "You already have an active Relay session in another community."
        except Exception as e:
            log.error("Failed to check active relay session: %s", e, exc_info=True)
            return "Failed to check for active sessions. Please try again."

    # Category creation - this is where permission errors typically occur
    discord_category = None
    try:
        discord_category = await _get_or_create_discord_category(target_guild, category_name)
    except discord.Forbidden as e:
        log.error(
            "Permission denied when creating category in guild %s: %s. Bot lacks Manage Channels permission.",
            target_guild.id, e
        )
        return "FORBIDDEN: Relay is missing required permissions to create ticket channels. Please ensure the bot has Manage Channels permission."
    except discord.HTTPException as e:
        log.error("HTTP error when creating category in guild %s: %s", target_guild.id, e, exc_info=True)
        return "Failed to create ticket category due to a Discord API error. Please try again."
    except Exception as e:
        log.error("Unexpected error when creating category in guild %s: %s", target_guild.id, e, exc_info=True)
        return "Failed to create ticket category. Please try again."

    # Channel creation
    channel = None
    try:
        # Channel name: just the username
        channel_name = _sanitize_username(user.name)

        # Channel inherits permissions from the category
        log.info("Creating channel %s in category %s in guild %s", channel_name, discord_category.id, target_guild.id)
        channel = await target_guild.create_text_channel(
            name=channel_name,
            category=discord_category,
            topic=f"Relay ticket for {user} ({user.id})",
        )
        log.info("Successfully created channel %s (ID: %s) in guild %s", channel_name, channel.id, target_guild.id)
    except discord.Forbidden as e:
        log.error(
            "Permission denied when creating channel in guild %s: %s. Bot lacks Manage Channels permission.",
            target_guild.id, e
        )
        return "FORBIDDEN: Relay is missing required permissions to create ticket channels. Please ensure the bot has Manage Channels permission."
    except discord.HTTPException as e:
        log.error("HTTP error when creating channel in guild %s: %s", target_guild.id, e, exc_info=True)
        return "Failed to create ticket channel due to a Discord API error. Please try again."
    except Exception as e:
        log.error("Unexpected error when creating channel in guild %s: %s", target_guild.id, e, exc_info=True)
        return "Failed to create ticket channel. Please try again."

    # Database record creation
    ticket = None
    try:
        ticket = await queries.create_ticket(
            guild_id=target_guild.id,
            user_id=user.id,
            channel_id=channel.id,
            category_name=category_name,
            source_guild_id=source_guild_id,
        )
        ticket_id = ticket["id"]
        display_ticket_number = ticket["community_ticket_number"]
        log.info("Created ticket record %s (display #%s) for user %s in guild %s", ticket_id, display_ticket_number, user.id, target_guild.id)
    except Exception as e:
        log.error("Failed to create ticket record in database: %s", e, exc_info=True)
        # Clean up the channel we created since database record failed
        try:
            await channel.delete()
            log.info("Cleaned up orphaned channel %s after database failure", channel.id)
        except Exception:
            pass
        return "Failed to create ticket record. Please try again."

    # Post opening header in staff channel
    # Include source guild identity if cross-server
    try:
        if source_guild_id and source_guild:
            source_name = source_guild.name
            header = f"🌐 Source: **{source_name}**\n🎫 Ticket `#{display_ticket_number}`"
            if category_name:
                header += f"\n📂 Category: **{category_name}**"
            await channel.send(header)

        embed = message_style.ticket_created_staff_embed(
            display_ticket_number, user, category_name,
        )
        # Attach pre-claim dashboard (lightweight, only Claim button)
        # Transforms to full operational dashboard after claim
        from bot.views.dashboard import PreClaimView
        dashboard = PreClaimView()
        await channel.send(embed=embed, view=dashboard)
        log.info("Successfully sent staff embed to channel %s", channel.id)
    except Exception as e:
        log.error("Failed to send staff embed to channel %s: %s", channel.id, e, exc_info=True)
        # Don't fail the entire ticket creation if embed fails

    # DM the user
    try:
        # Show the source guild name to the user, not the support guild
        display_guild_name = source_guild.name if source_guild else target_guild.name
        display_guild = source_guild if source_guild else target_guild
        guild_icon_url = display_guild.icon.url if display_guild.icon else None
        dm_embed = message_style.ticket_created_user_embed(
            display_ticket_number, display_guild_name, guild_icon_url,
        )
        await user.send(embed=dm_embed)
        log.info("Successfully sent DM to user %s for ticket %s", user.id, ticket_id)

        # /leave warning tip — sent every ticket open
        leave_warning = message_style.relay_embed(
            description=(
                "**Quick Reminder**\n\n"
                "Using `/leave` will **disconnect your relay session**. "
                "Once disconnected, you will not be able to send or receive "
                "messages in this ticket.\n\n"
                "Only use `/leave` if you intentionally want to disconnect. "
                "If you leave accidentally, a staff member may need to intervene.\n\n"
                "*Your session is active — just reply here to communicate with staff.*"
            ),
            footer="Relay • Session Info",
        )
        await user.send(embed=leave_warning)
    except discord.Forbidden:
        log.warning("Could not DM user %s - DMs may be closed", user.id)
        await channel.send(
            embed=message_style.warning_embed(
                "Could not DM this user — their DMs may be closed."
            )
        )
    except Exception as e:
        log.error("Unexpected error when sending DM to user %s: %s", user.id, e, exc_info=True)
        # Don't fail the entire ticket creation if DM fails

    log.info("Ticket creation completed successfully for user %s, ticket %s", user.id, ticket_id)
    return display_ticket_number, channel
