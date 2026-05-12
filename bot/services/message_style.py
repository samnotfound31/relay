"""
Relay Bot — Message Style Service
Centralized embed builder for system events only.

Embeds are used ONLY for:
  - ticket panel
  - ticket creation notice
  - ticket close notice
  - claim confirmation
  - status/error/success feedback

Relay conversation messages are plain text (handled in relay_service.py).
"""

from __future__ import annotations

import discord
from bot.config import (
    RELAY_COLOR,
    RELAY_SUCCESS,
    RELAY_WARNING,
    RELAY_ERROR,
    RELAY_NEUTRAL,
)


# ── Core Embed Builders ──────────────────────────────

def relay_embed(
    title: str | None = None,
    description: str | None = None,
    *,
    color: int = RELAY_COLOR,
    footer: str | None = None,
    thumbnail: str | None = None,
    image: str | None = None,
) -> discord.Embed:
    """Base embed with Relay branding."""
    embed = discord.Embed(color=color)
    if title:
        embed.title = title
    if description:
        embed.description = description
    if footer:
        embed.set_footer(text=footer)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    return embed


def success_embed(description: str, **kwargs) -> discord.Embed:
    return relay_embed(description=f"✅  {description}", color=RELAY_SUCCESS, **kwargs)


def warning_embed(description: str, **kwargs) -> discord.Embed:
    return relay_embed(description=f"⚠️  {description}", color=RELAY_WARNING, **kwargs)


def error_embed(description: str, **kwargs) -> discord.Embed:
    return relay_embed(description=f"❌  {description}", color=RELAY_ERROR, **kwargs)


# ── Ticket-Specific Embeds (System Events Only) ──────

def ticket_panel_embed(
    guild_name: str,
    banner_url: str | None = None,
    categories: list[dict] | None = None,
    color: int | None = None,
) -> discord.Embed:
    """
    The public-facing support panel — acts as the primary onboarding gateway.
    Dynamically includes configured categories when available.
    """
    # Use provided color or default Relay color
    embed_color = color if color is not None else RELAY_COLOR

    # ── Build description ─────────────────────────────
    desc = (
        f"Welcome to **{guild_name}**.\n\n"
        "Need assistance? Follow the steps below to open a private support ticket.\n"
        "\u200b\n"  # zero-width space for visual spacing
        "### How It Works\n"
        "1️⃣  Press **Open Ticket**\n"
    )

    if categories:
        desc += "2️⃣  Select a support category\n"
        desc += "3️⃣  Explain your issue in DMs\n"
        desc += "4️⃣  A staff member will assist you shortly\n"
    else:
        desc += "2️⃣  Explain your issue in DMs\n"
        desc += "3️⃣  A staff member will assist you shortly\n"

    # ── Categories section ────────────────────────────
    if categories:
        desc += "\u200b\n### Available Categories\n"
        for cat in categories:
            emoji = cat.get("emoji") or "📂"
            name = cat.get("name", "Unknown")
            desc += f"{emoji}  {name}\n"

    # ── Footer note ───────────────────────────────────
    desc += "\u200b\n*Please avoid opening duplicate tickets.*"

    embed = relay_embed(
        title="Relay Support",
        description=desc,
        color=embed_color,
        footer=f"{guild_name} • Relay",
    )

    if banner_url:
        embed.set_image(url=banner_url)

    return embed


def ticket_created_staff_embed(
    ticket_id: int,
    user: discord.User | discord.Member,
    category: str | None = None,
) -> discord.Embed:
    """Sent into the staff ticket channel when a ticket opens."""
    desc = (
        f"**Ticket** `#{ticket_id}`\n"
        f"**User** {user.mention} (`{user.id}`)\n"
    )
    if category:
        desc += f"**Category** {category}\n"
    desc += (
        "\n"
        "Use `/reply` to respond to the user.\n"
        "Use `/anreply` to respond anonymously.\n"
        "Use `/claim` to claim this ticket.\n"
        "Use `/close` to close this ticket."
    )
    return relay_embed(
        title="📨  New Ticket",
        description=desc,
        color=RELAY_COLOR,
        footer=f"Ticket #{ticket_id}",
    )


def ticket_created_user_embed(
    ticket_id: int,
    guild_name: str,
    guild_icon_url: str | None = None,
) -> discord.Embed:
    """DM confirmation sent to the user."""
    return relay_embed(
        title="Ticket Opened",
        description=(
            f"Your ticket `#{ticket_id}` has been created in **{guild_name}**.\n\n"
            "You can send messages here and they'll be relayed to our staff.\n"
            "You'll receive replies right here in your DMs."
        ),
        color=RELAY_SUCCESS,
        footer=f"Ticket #{ticket_id} • {guild_name}",
        thumbnail=guild_icon_url,
    )


def ticket_closed_user_embed(
    ticket_id: int,
    guild_name: str,
    guild_icon_url: str | None = None,
    closure_message: str | None = None,
) -> discord.Embed:
    """DM sent to the user when their ticket is closed."""
    final_message = (closure_message or "").strip()
    if len(final_message) > 1200:
        final_message = final_message[:1197].rstrip() + "..."
    description = (
        f"Your ticket `#{ticket_id}` in **{guild_name}** has been closed.\n\n"
        "If you need further assistance, feel free to open a new ticket."
    )
    if final_message:
        description += (
            "\n\n"
            "### FINAL STAFF MESSAGE\n"
            f"{final_message}"
        )
    return relay_embed(
        title="Ticket Closed",
        description=description,
        color=RELAY_NEUTRAL,
        footer=f"Ticket #{ticket_id} • Closed",
        thumbnail=guild_icon_url,
    )


def ticket_context_embed(
    ticket_number: int | str,
    owner_name: str,
    assigned_staff: str | None,
    owner_emoji: str | None,
    status: str,
    status_emoji: str | None,
    priority: str,
    priority_emoji: str | None,
    relay_session: str,
    issue: str | None,
) -> discord.Embed:
    """Authoritative operational context embed for /context."""
    issue_text = (issue or "").strip() or "No issue summary set."
    if len(issue_text) > 900:
        issue_text = issue_text[:897].rstrip() + "..."

    embed = relay_embed(
        title="🎫  Ticket Context",
        description=(
            "### WORKFLOW STATE\n"
            f"**Owner**\n{owner_name}\n\n"
            f"**Assigned Staff**\n{owner_emoji or '—'} {assigned_staff or 'Unassigned'}\n\n"
            f"**Status**\n{status_emoji or '—'} {status.title()}\n\n"
            f"**Priority**\n{priority_emoji or '—'} {priority.title()}\n\n"
            f"**Relay Session**\n{relay_session}\n\n"
            "### CURRENT ISSUE\n"
            f"{issue_text}"
        ),
        color=RELAY_NEUTRAL,
        footer=f"Ticket #{ticket_number} • Authoritative",
    )
    return embed


def user_info_embed(
    ticket_number: int | str,
    display_name: str,
    username: str,
    user_id: int,
    source_name: str,
    created_at: str,
    joined_at: str,
    total_tickets: int,
    note_count: int,
    transcript_count: int,
    source_guild_id: int,
    avatar_url: str | None = None,
) -> discord.Embed:
    """Readable continuity intelligence snapshot for /info."""
    embed = relay_embed(
        title=f"🔎  User Info • Ticket #{ticket_number}",
        description=(
            "### USER IDENTITY\n"
            f"**Display Name**\n{display_name}\n\n"
            f"**Username**\n`{username}`\n\n"
            f"**User ID**\n`{user_id}`\n\n"
            f"**Source Server**\n{source_name}\n\n"
            "### ACCOUNT TIMELINE\n"
            f"**Account Created**\n{created_at}\n\n"
            f"**Joined Source**\n{joined_at}\n\n"
            "### CONTINUITY METRICS\n"
            f"**Tickets in Community**\n{total_tickets}\n\n"
            f"**Notes**\n{note_count}\n\n"
            f"**Transcripts**\n{transcript_count}\n\n"
            "### INVESTIGATION SCOPE\n"
            f"**Community scoped**\n`{source_guild_id}`"
        ),
        color=RELAY_NEUTRAL,
        footer="Continuity intelligence • Not workflow state",
        thumbnail=avatar_url,
    )
    return embed
