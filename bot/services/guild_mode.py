"""
Relay Bot — Guild Mode Service
Centralized guild role resolution: local / source / support.

- LOCAL: standalone Relay, no cross-server routing
- SOURCE: users live here, tickets route to a linked support guild
- SUPPORT: staff works here, receives routed tickets
"""

from __future__ import annotations

from enum import Enum

import discord
from bot.database import queries
from bot.services import message_style


class GuildMode(Enum):
    LOCAL = "local"
    SOURCE = "source"
    SUPPORT = "support"


async def resolve_guild_mode(guild_id: int) -> GuildMode:
    """
    Determine the operational mode of a guild.

    - If guild has an outgoing link → SOURCE
    - If guild receives links from others → SUPPORT
    - Otherwise → LOCAL
    """
    # Check if this guild routes tickets outward
    outgoing = await queries.get_support_guild_id(guild_id)
    if outgoing is not None:
        return GuildMode.SOURCE

    # Check if this guild receives tickets from others
    is_target = await queries.is_support_guild(guild_id)
    if is_target:
        return GuildMode.SUPPORT

    return GuildMode.LOCAL


# ── Command Guards ───────────────────────────────────

SOURCE_BLOCKED_MSG = (
    "⚠️ This server is configured as a Relay source guild.\n"
    "Ticket management must be performed in the linked support server."
)

SUPPORT_BLOCKED_MSG = (
    "⚠️ This server is configured as a Relay support server.\n"
    "Configuration is managed from the linked source server."
)

# Commands allowed in SOURCE guilds (everything else blocked)
SOURCE_ALLOWED_COMMANDS = {"announce", "unlinksupport"}

# Configuration commands blocked in SUPPORT guilds
# These commands modify settings and must run on source/local guilds
SUPPORT_BLOCKED_COMMANDS = {
    "announce", "category", "staffroles", "logchannel",
    "staffperms", "linksupport", "unlinksupport",
}


async def require_not_source(interaction: discord.Interaction) -> bool:
    """
    Guard: returns True if the guild is SOURCE (command should abort).
    Sends a warning embed via interaction.response and returns True to
    signal the caller to ``return`` early.

    Use this BEFORE any ``interaction.response.defer()`` call.
    """
    if interaction.guild is None:
        return False

    mode = await resolve_guild_mode(interaction.guild_id)
    if mode == GuildMode.SOURCE:
        await interaction.response.send_message(
            embed=message_style.error_embed(SOURCE_BLOCKED_MSG),
            ephemeral=True,
        )
        return True  # Blocked
    return False  # Allowed


async def require_not_source_deferred(interaction: discord.Interaction) -> bool:
    """
    Same guard but uses followup (for already-deferred interactions).
    Use this AFTER ``interaction.response.defer()`` has already been called.
    """
    if interaction.guild is None:
        return False

    mode = await resolve_guild_mode(interaction.guild_id)
    if mode == GuildMode.SOURCE:
        await interaction.followup.send(
            embed=message_style.error_embed(SOURCE_BLOCKED_MSG),
            ephemeral=True,
        )
        return True  # Blocked
    return False  # Allowed


async def require_not_support(interaction: discord.Interaction) -> bool:
    """
    Guard: returns True if the guild is SUPPORT (command should abort).
    Blocks configuration commands in support guilds.

    Use this BEFORE any ``interaction.response.defer()`` call.
    """
    if interaction.guild is None:
        return False

    mode = await resolve_guild_mode(interaction.guild_id)
    if mode == GuildMode.SUPPORT:
        await interaction.response.send_message(
            embed=message_style.error_embed(SUPPORT_BLOCKED_MSG),
            ephemeral=True,
        )
        return True  # Blocked
    return False  # Allowed


async def require_not_support_deferred(interaction: discord.Interaction) -> bool:
    """
    Same guard but uses followup (for already-deferred interactions).
    Use this AFTER ``interaction.response.defer()`` has already been called.
    """
    if interaction.guild is None:
        return False

    mode = await resolve_guild_mode(interaction.guild_id)
    if mode == GuildMode.SUPPORT:
        await interaction.followup.send(
            embed=message_style.error_embed(SUPPORT_BLOCKED_MSG),
            ephemeral=True,
        )
        return True  # Blocked
    return False  # Allowed
