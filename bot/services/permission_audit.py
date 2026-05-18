"""
Relay Bot — Permission Audit Service
Centralized Discord permission diagnostics for critical workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Iterable

import discord

from bot.services import message_style

log = logging.getLogger(__name__)


PERMISSION_LABELS: dict[str, str] = {
    "view_channel": "View Channels",
    "manage_channels": "Manage Channels",
    "send_messages": "Send Messages",
    "manage_messages": "Manage Messages",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "read_message_history": "Read Message History",
}

REQUIRED_PERMISSION_SETS: dict[str, tuple[str, ...]] = {
    "ticket_workflow": (
        "view_channel",
        "manage_channels",
        "send_messages",
        "manage_messages",
        "embed_links",
        "attach_files",
        "read_message_history",
    ),
    "category_management": (
        "view_channel",
        "manage_channels",
    ),
    "panel_posting": (
        "view_channel",
        "send_messages",
        "embed_links",
    ),
    "dashboard_setup": (
        "view_channel",
        "send_messages",
        "embed_links",
    ),
    "relay_channel": (
        "view_channel",
        "send_messages",
    ),
    "transcript_logging": (
        "view_channel",
        "manage_channels",
        "send_messages",
        "embed_links",
        "attach_files",
        "read_message_history",
    ),
    "staff_role_sync": (
        "view_channel",
        "manage_channels",
    ),
}


@dataclass(frozen=True)
class PermissionAuditResult:
    workflow: str
    guild_id: int
    channel_id: int | None
    required: tuple[str, ...]
    granted: tuple[str, ...]
    missing: tuple[str, ...]
    unavailable_reason: str | None = None

    @property
    def ok(self) -> bool:
        return not self.missing and self.unavailable_reason is None

    @property
    def missing_labels(self) -> list[str]:
        return [PERMISSION_LABELS.get(perm, perm.replace("_", " ").title()) for perm in self.missing]

    @property
    def granted_labels(self) -> list[str]:
        return [PERMISSION_LABELS.get(perm, perm.replace("_", " ").title()) for perm in self.granted]


def _required_permissions(workflow: str, extra: Iterable[str] | None = None) -> tuple[str, ...]:
    required = list(REQUIRED_PERMISSION_SETS.get(workflow, ()))
    if extra:
        for perm in extra:
            if perm not in required:
                required.append(perm)
    return tuple(required)


def _format_labels(perms: Iterable[str]) -> list[str]:
    return [PERMISSION_LABELS.get(perm, perm.replace("_", " ").title()) for perm in perms]


def _resolve_permissions(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel | discord.Thread | None = None,
) -> tuple[discord.Permissions | None, str | None]:
    bot_member = guild.me
    if bot_member is None:
        return None, "Relay could not verify its member object in this server."

    if channel is not None and hasattr(channel, "permissions_for"):
        try:
            return channel.permissions_for(bot_member), None
        except Exception as e:
            return None, f"Relay could not inspect channel permissions: {e}"

    return bot_member.guild_permissions, None


def audit_permissions(
    guild: discord.Guild,
    workflow: str,
    *,
    channel: discord.abc.GuildChannel | discord.Thread | None = None,
    extra_required: Iterable[str] | None = None,
    context: str | None = None,
) -> PermissionAuditResult:
    required = _required_permissions(workflow, extra_required)
    channel_id = getattr(channel, "id", None)

    log.info(
        "[PERMISSION_AUDIT_START] workflow=%s guild=%s channel=%s context=%s required=%s",
        workflow, guild.id, channel_id, context, _format_labels(required),
    )

    permissions, unavailable_reason = _resolve_permissions(guild, channel)
    if unavailable_reason or permissions is None:
        log.error(
            "[PERMISSION_AUDIT_FAILURE] workflow=%s guild=%s channel=%s context=%s reason=%s",
            workflow, guild.id, channel_id, context, unavailable_reason,
        )
        return PermissionAuditResult(
            workflow=workflow,
            guild_id=guild.id,
            channel_id=channel_id,
            required=required,
            granted=(),
            missing=(),
            unavailable_reason=unavailable_reason,
        )

    if permissions.administrator:
        granted = required
        missing: tuple[str, ...] = ()
    else:
        granted = tuple(perm for perm in required if bool(getattr(permissions, perm, False)))
        missing = tuple(perm for perm in required if perm not in granted)

    log.info(
        "[PERMISSION_AUDIT_DETECTED] workflow=%s guild=%s channel=%s context=%s granted=%s",
        workflow, guild.id, channel_id, context, _format_labels(granted),
    )

    if missing:
        log.warning(
            "[PERMISSION_AUDIT_MISSING] workflow=%s guild=%s channel=%s context=%s missing=%s",
            workflow, guild.id, channel_id, context, _format_labels(missing),
        )
        log.warning(
            "[PERMISSION_AUDIT_FAILURE] workflow=%s guild=%s channel=%s context=%s",
            workflow, guild.id, channel_id, context,
        )
    else:
        log.info(
            "[PERMISSION_AUDIT_SUCCESS] workflow=%s guild=%s channel=%s context=%s",
            workflow, guild.id, channel_id, context,
        )

    return PermissionAuditResult(
        workflow=workflow,
        guild_id=guild.id,
        channel_id=channel_id,
        required=required,
        granted=granted,
        missing=missing,
    )


def missing_permissions_message(result: PermissionAuditResult) -> str:
    if result.missing:
        bullets = "\n".join(f"• {label}" for label in result.missing_labels)
        return (
            "Relay is missing the following required permissions:\n\n"
            f"{bullets}\n\n"
            "Please grant these permissions and try again."
        )

    if result.unavailable_reason:
        return (
            "Relay could not verify its current permissions.\n\n"
            f"Reason: {result.unavailable_reason}\n\n"
            "Please try again after the bot is fully connected."
        )

    return "Relay has the required permissions."


def missing_permissions_embed(result: PermissionAuditResult) -> discord.Embed:
    return message_style.error_embed(missing_permissions_message(result))
