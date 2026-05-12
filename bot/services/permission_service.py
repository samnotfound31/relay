"""
Relay Bot — Permission Service
Checks whether a user has staff access for tickets.
Phase 2: category-level permissions for inheritance.
Phase 6: granular command-level permissions for staff roles.
"""

from __future__ import annotations

import discord
from bot.database.queries import get_support_roles, get_role_denied_capabilities


async def is_staff(member: discord.Member, guild_id: int) -> bool:
    """Return True if the member holds any configured support role."""
    role_ids = await get_support_roles(guild_id)
    if not role_ids:
        # Fallback: server administrators are always staff
        return member.guild_permissions.administrator
    return any(r.id in role_ids for r in member.roles) or member.guild_permissions.administrator


async def can_access_investigative_history(
    member: discord.Member,
    guild_id: int,
    capability: str = "history",
) -> bool:
    """Centralized investigative continuity access check."""
    return await is_staff(member, guild_id)


def is_admin(member: discord.Member) -> bool:
    """Return True if the member is a server administrator."""
    return member.guild_permissions.administrator


async def resolve_staff_role_label(member: discord.Member, guild_id: int) -> str:
    """
    Resolve the highest configured support role the member holds.
    Returns the role name (e.g. "Moderator", "Admin").
    Falls back to "Staff" if no configured role matches.

    member.roles is ordered lowest→highest by Discord position,
    so we iterate in reverse to find the highest match first.
    """
    role_ids = set(await get_support_roles(guild_id))
    if not role_ids:
        # No roles configured — use top role if it's not @everyone
        top = member.top_role
        if top and top.name != "@everyone":
            return top.name
        return "Staff"

    # Walk roles highest→lowest
    for role in reversed(member.roles):
        if role.id in role_ids:
            return role.name

    return "Staff"


async def build_category_overwrites(
    guild: discord.Guild,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """
    Build permission overwrites for a Discord CATEGORY.
    All ticket channels inside it inherit these permissions automatically.
    - @everyone: denied visibility
    - support roles: view + send + read history + attach
    - bot: full required permissions
    """
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    # Bot permissions
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
        )

    # Support role permissions
    role_ids = await get_support_roles(guild.id)
    for role_id in role_ids:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
            )

    return overwrites


# Keep backward compat alias
async def build_ticket_overwrites(
    guild: discord.Guild,
    user: discord.User | discord.Member | None = None,
    bot_member: discord.Member | None = None,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Backward-compatible alias — delegates to build_category_overwrites."""
    return await build_category_overwrites(guild)


# ── Phase 6: Granular Command-Level Permissions ──

async def can_use_command(
    member: discord.Member,
    guild_id: int,
    capability: str,
) -> bool:
    """
    Centralized authority check for Relay operational commands/controls.
    Returns True if the member can use the specified capability.

    Capability examples: 'reply', 'close', 'history', 'note_add', etc.

    Logic:
    1. Server administrators always have access (bypass)
    2. If not staff, deny
    3. Check if ANY of the member's staff roles has this capability denied
    4. If denied by any role, deny
    5. Otherwise, allow (default allow for staff)
    """
    # Admins bypass all restrictions
    if member.guild_permissions.administrator:
        return True

    # Check if member is staff at all
    role_ids = await get_support_roles(guild_id)
    if not role_ids:
        # No roles configured — admins only (already checked above)
        return False

    member_role_ids = {r.id for r in member.roles}
    if not any(rid in member_role_ids for rid in role_ids):
        # Member has no staff roles
        return False

    # Check if ANY of the member's staff roles denies this capability
    for role_id in member_role_ids:
        if role_id in role_ids:
            denied = await get_role_denied_capabilities(guild_id, role_id)
            if capability in denied:
                return False

    # No denial found — allow access
    return True
