"""
Relay Bot — Tickets Cog
Commands: /reply, /anreply, /claim, /transfer, /status, /close,
          /move, /priority, /emoji, /emojilist
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import TICKET_STATUSES, STATUS_EMOJIS, PRIORITY_EMOJIS, PRIORITY_LEVELS
from bot.database import queries
from bot.services import guild_mode, message_style, relay_service, permission_service
from bot.services import ticket_service
from bot.services import workflow_service, emoji_service, note_service
from bot.config import RELAY_COLOR


# ── Permission Guard ─────────────────────────────────

async def _require_capability(
    interaction: discord.Interaction,
    capability: str,
) -> bool:
    """Check if the member has the required capability. Returns True if authorized, False otherwise."""
    if interaction.guild is None:
        return False
    
    if await permission_service.can_use_command(interaction.user, interaction.guild.id, capability):
        return True
    
    await interaction.response.send_message(
        embed=message_style.warning_embed(
            "⚠️ Your Relay role does not allow access to this operation."
        ),
        ephemeral=True,
    )
    return False

log = logging.getLogger(__name__)


def _dm_command(func):
    """Mark an app command as DM-accessible across discord.py versions.

    discord.py 2.4+ uses allowed_contexts/allowed_installs decorators.
    On 2.3, app commands are DM-accessible by default; this is a safe no-op.
    """
    if hasattr(app_commands, "allowed_contexts"):
        func = app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)(func)
    if hasattr(app_commands, "allowed_installs"):
        func = app_commands.allowed_installs(guilds=True, users=True)(func)
    return func


# ── Leave Confirmation View ─────────────────────────────

class LeaveConfirmationView(discord.ui.View):
    """Confirmation view for /leave destructive action."""

    def __init__(self, user_id: int, ticket_id: int, bot: commands.Bot):
        super().__init__(timeout=120)  # 2 minute timeout
        self.user_id = user_id
        self.ticket_id = ticket_id
        self.bot = bot
        self.add_item(_ConfirmDisconnectButton(self))
        self.add_item(_CancelButton(self))

    async def on_timeout(self) -> None:
        """Disable components when confirmation expires."""
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        log.warning("Leave confirmation view error: %s", error)
        try:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "Leave confirmation expired. Please rerun `/leave`."
                ),
                ephemeral=True,
            )
        except Exception:
            pass


class _ConfirmDisconnectButton(discord.ui.Button):
    """Button to confirm and execute session disconnection."""

    def __init__(self, view: LeaveConfirmationView):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Yes, Disconnect",
            emoji="🔌",
            row=0,
        )
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        # Ownership validation: only original user can confirm
        if interaction.user.id != self.view_ref.user_id:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "This confirmation does not belong to you."
                ),
                ephemeral=True,
            )
            return

        # Re-check ticket state (may have been closed/disconnected already)
        ticket = await queries.get_ticket_by_id(self.view_ref.ticket_id)
        if ticket is None or ticket.get("relay_session_status") != "active":
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "Session already disconnected or ticket closed."
                ),
                ephemeral=True,
            )
            return

        # Execute disconnect (reuse existing leave logic)
        await queries.disconnect_relay_session(ticket["id"])
        channel = self.view_ref.bot.get_channel(ticket["channel_id"])
        if channel:
            claimed_by = ticket.get("claimed_by")
            mention = f"<@{claimed_by}>\n" if claimed_by else ""
            await channel.send(
                mention
                + "⚠️ User has left the Relay session.\n"
                "Further replies can no longer be delivered."
            )

        await interaction.response.edit_message(
            embed=message_style.success_embed(
                "You have disconnected from this Relay session.\n"
                "Staff may still continue handling the ticket internally."
            ),
            view=None,  # Remove buttons
        )


class _CancelButton(discord.ui.Button):
    """Button to cancel the leave action."""

    def __init__(self, view: LeaveConfirmationView):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Cancel",
            emoji="✖️",
            row=0,
        )
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        # Ownership validation: only original user can cancel
        if interaction.user.id != self.view_ref.user_id:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "This confirmation does not belong to you."
                ),
                ephemeral=True,
            )
            return

        # Cancel: dismiss the confirmation, no session changes
        await interaction.response.edit_message(
            embed=message_style.relay_embed(
                description="Leave action cancelled. Your session remains active.",
                color=RELAY_COLOR,
            ),
            view=None,  # Remove buttons
        )


def _parse_duration(text: str) -> tuple[int, str] | None:
    """Parse single-unit duration strings like 30s, 25m, 2h, 3d."""
    import re

    text = text.strip().lower()
    match = re.fullmatch(r"(\d+)([smhd])", text)
    if not match:
        return None

    value = int(match.group(1))
    if value <= 0:
        return None

    unit = match.group(2)
    multipliers = {
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
    }
    labels = {
        "s": "second",
        "m": "minute",
        "h": "hour",
        "d": "day",
    }
    label = labels[unit]
    human = f"{value} {label if value == 1 else label + 's'}"
    return value * multipliers[unit], human


class Tickets(commands.Cog):
    """Ticket management and workflow commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Shared Checks ─────────────────────────────────

    async def _check_ticket_channel(
        self, interaction: discord.Interaction,
    ) -> dict | None:
        """Verify this command is run inside a ticket channel."""
        ticket = await queries.get_ticket_by_channel(interaction.channel_id)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.error_embed(
                    "This command can only be used inside a ticket channel."
                ),
                ephemeral=True,
            )
        return ticket



    async def _check_staff(self, interaction: discord.Interaction) -> bool:
        """Verify the user is staff."""
        if await guild_mode.require_not_source(interaction):
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        if not await permission_service.is_staff(interaction.user, interaction.guild_id):
            await interaction.response.send_message(
                embed=message_style.error_embed(
                    "You don't have permission to use this command."
                ),
                ephemeral=True,
            )
            return False
        return True

    async def _check_relay_session_active(
        self, interaction: discord.Interaction, ticket: dict,
    ) -> bool:
        if ticket.get("relay_session_status", "active") == "active":
            return True
        await interaction.response.send_message(
            embed=message_style.warning_embed(
                "The user has left this Relay session.\nReplies can no longer be delivered."
            ),
            ephemeral=True,
        )
        return False

    async def _check_investigative_access(
        self,
        interaction: discord.Interaction,
        capability: str = "history",
    ) -> bool:
        if not isinstance(interaction.user, discord.Member) or not interaction.guild_id:
            return False
        if await permission_service.can_access_investigative_history(
            interaction.user, interaction.guild_id, capability,
        ):
            return True
        await interaction.response.send_message(
            embed=message_style.warning_embed(
                "You do not have permission to access investigative history."
            )
        )
        return False

    async def _resolve_ticket_owner(self, ticket: dict) -> discord.User | None:
        try:
            return await self.bot.fetch_user(ticket["user_id"])
        except Exception:
            return None

    def _source_guild_id(self, ticket: dict, fallback_guild_id: int | None) -> int:
        return ticket.get("source_guild_id") or fallback_guild_id or ticket["guild_id"]

    # ── /leave ────────────────────────────────────────

    @app_commands.command(
        name="leave",
        description="Disconnect from your current Relay DM session.",
    )
    @_dm_command
    async def leave(self, interaction: discord.Interaction) -> None:
        log.info(
            "[TICKET_LEAVE] User %s (%s) attempting to leave session",
            interaction.user.id, interaction.user.name
        )
        
        if interaction.guild is not None:
            log.warning(
                "[TICKET_LEAVE] User %s attempted to use /leave in a server (DM-only command)",
                interaction.user.id
            )
            await interaction.response.send_message(
                embed=message_style.error_embed("This command can only be used in DMs."),
                ephemeral=True,
            )
            return

        ticket = await ticket_service.get_active_ticket_for_user(
            self.bot, interaction.user.id,
        )
        if ticket is None:
            log.warning(
                "[TICKET_LEAVE] User %s has no active session",
                interaction.user.id
            )
            await interaction.response.send_message(
                embed=message_style.warning_embed("You do not have an active Relay session."),
                ephemeral=True,
            )
            return

        log.info(
            "[TICKET_LEAVE] Showing confirmation for user %s (ticket %s)",
            interaction.user.id, ticket["id"]
        )

        # Show confirmation view instead of immediate disconnect
        embed = message_style.relay_embed(
            title="⚠️ Disconnect Relay Session",
            description=(
                "**This will disconnect your relay session.**\n\n"
                "Once disconnected:\n"
                "• You will not be able to send messages in this ticket\n"
                "• You will not receive staff replies\n"
                "• You cannot reconnect yourself\n"
                "• Staff/admin intervention may be required\n\n"
                "Use `/leave` **only** if you are intentionally leaving support.\n"
                "If you leave accidentally, please contact staff."
            ),
            color=RELAY_COLOR,
        )
        embed.set_footer(text="Relay • Session Disconnect Warning")

        view = LeaveConfirmationView(
            user_id=interaction.user.id,
            ticket_id=ticket["id"],
            bot=self.bot,
        )
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )

    # ── /context ──────────────────────────────────────

    @app_commands.command(
        name="context",
        description="View or update the operational investigation context for this ticket.",
    )
    @app_commands.describe(issue="Optional staff-authored issue summary")
    async def context(
        self,
        interaction: discord.Interaction,
        issue: str | None = None,
    ) -> None:
        log.info(
            "[TICKET_CONTEXT] User %s (%s) viewing context in channel %s",
            interaction.user.id, interaction.user.name, interaction.channel_id
        )
        
        if not await self._check_staff(interaction):
            log.warning(
                "[TICKET_CONTEXT] Staff check failed for user %s",
                interaction.user.id
            )
            return
        if not await _require_capability(interaction, "context"):
            log.warning(
                "[TICKET_CONTEXT] Capability check failed for user %s (context)",
                interaction.user.id
            )
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            log.warning(
                "[TICKET_CONTEXT] No ticket found in channel %s",
                interaction.channel_id
            )
            return

        if issue is not None:
            clean_issue = issue.strip()
            if len(clean_issue) > 1200:
                clean_issue = clean_issue[:1197].rstrip() + "..."
            await queries.update_ticket_context_issue(
                interaction.channel_id,
                clean_issue or None,
            )
            ticket["ticket_context_issue"] = clean_issue or None
            log.info(
                "[TICKET_CONTEXT] Updated context issue for ticket %s",
                ticket["id"]
            )

        owner_user = await self._resolve_ticket_owner(ticket)
        owner_name = owner_user.display_name if owner_user else f"User {ticket['user_id']}"
        assigned_staff = None
        owner_emoji = None
        claimed_by = ticket.get("claimed_by")
        if claimed_by:
            member = interaction.guild.get_member(claimed_by) if interaction.guild else None
            assigned_staff = member.display_name if member else f"User {claimed_by}"
            owner_emoji = await emoji_service.get_emoji_for_staff(
                interaction.guild_id, claimed_by,
            )

        ticket_status = ticket.get("ticket_status", "open")
        status_emoji = STATUS_EMOJIS.get(ticket_status)
        priority = ticket.get("priority", "medium")
        priority_emoji = PRIORITY_EMOJIS.get(priority)

        relay_session = (
            "Connected"
            if ticket.get("relay_session_status", "active") == "active"
            else "Disconnected"
        )

        ticket_number = ticket.get("community_ticket_number") or ticket["id"]

        embed = message_style.ticket_context_embed(
            ticket_number,
            owner_name,
            assigned_staff,
            owner_emoji,
            ticket_status,
            status_emoji,
            priority,
            priority_emoji,
            relay_session,
            ticket.get("ticket_context_issue"),
        )
        try:
            await interaction.response.send_message(embed=embed)
            log.debug(
                "[TICKET_CONTEXT] Context displayed for ticket %s",
                ticket_number
            )
        except Exception as e:
            log.error(
                "[TICKET_CONTEXT] Failed to send context embed: %s",
                e, exc_info=True
            )

    # ── /history ──────────────────────────────────────

    @app_commands.command(
        name="history",
        description="Open focused continuity retrieval for this ticket user.",
    )
    async def history(self, interaction: discord.Interaction) -> None:
        log.info(
            "[TICKET_HISTORY] User %s (%s) opening history view in channel %s",
            interaction.user.id, interaction.user.name, interaction.channel_id
        )
        
        if not await self._check_staff(interaction):
            log.warning(
                "[TICKET_HISTORY] Staff check failed for user %s",
                interaction.user.id
            )
            return
        if not await _require_capability(interaction, "history"):
            log.warning(
                "[TICKET_HISTORY] Capability check failed for user %s (history)",
                interaction.user.id
            )
            return
        if not await self._check_investigative_access(interaction, "history"):
            log.warning(
                "[TICKET_HISTORY] Investigative access check failed for user %s",
                interaction.user.id
            )
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            log.warning(
                "[TICKET_HISTORY] No ticket found in channel %s",
                interaction.channel_id
            )
            return

        source_guild_id = self._source_guild_id(ticket, interaction.guild_id)
        embed = message_style.relay_embed(
            title="🧭 Continuity Retrieval",
            description=(
                "Select a focused continuity view for this ticket user.\n\n"
                "**Notes** — moderation continuity lookup\n"
                "**Transcripts** — transcript artifact retrieval"
            ),
            footer=f"User {ticket['user_id']} • Community {source_guild_id}",
        )

        from bot.views.continuity import ContinuityView
        try:
            await interaction.response.send_message(
                embed=embed,
                view=ContinuityView(ticket["user_id"], source_guild_id),
            )
            log.debug(
                "[TICKET_HISTORY] History view displayed for ticket %s",
                ticket["id"]
            )
        except Exception as e:
            log.error(
                "[TICKET_HISTORY] Failed to send history view: %s",
                e, exc_info=True
            )

    # ── /info ─────────────────────────────────────────

    @app_commands.command(
        name="info",
        description="Show investigative context for the current ticket user.",
    )
    async def info(self, interaction: discord.Interaction) -> None:
        if not await self._check_staff(interaction):
            return
        if not await _require_capability(interaction, "info"):
            return
        if not await self._check_investigative_access(interaction, "history"):
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            return

        source_guild_id = self._source_guild_id(ticket, interaction.guild_id)
        user = await self._resolve_ticket_owner(ticket)
        source_guild = self.bot.get_guild(source_guild_id)
        source_member = source_guild.get_member(ticket["user_id"]) if source_guild else None

        total_tickets = await queries.get_ticket_history_count_for_user(
            ticket["user_id"], source_guild_id,
        )
        note_count = await queries.get_staff_note_count(ticket["user_id"], source_guild_id)
        transcript_count = await queries.get_transcript_count_for_user(
            ticket["user_id"], source_guild_id,
        )

        ticket_number = ticket.get("community_ticket_number") or ticket["id"]
        display_name = source_member.display_name if source_member else (user.display_name if user else "Unknown")
        username = user.name if user else "unknown"
        created_at = user.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S") if user else "—"
        joined_at = source_member.joined_at.astimezone().strftime("%Y-%m-%d %H:%M:%S") if source_member and source_member.joined_at else "—"
        source_name = source_guild.name if source_guild else f"Guild {source_guild_id}"
        avatar_url = None
        if source_member:
            avatar_url = source_member.display_avatar.url
        elif user:
            avatar_url = user.display_avatar.url

        embed = message_style.user_info_embed(
            ticket_number=ticket_number,
            display_name=display_name,
            username=username,
            user_id=ticket["user_id"],
            source_name=source_name,
            created_at=created_at,
            joined_at=joined_at,
            total_tickets=total_tickets,
            note_count=note_count,
            transcript_count=transcript_count,
            source_guild_id=source_guild_id,
            avatar_url=avatar_url,
        )

        from bot.views.continuity import ContinuityView
        await interaction.response.send_message(
            embed=embed,
            view=ContinuityView(ticket["user_id"], source_guild_id),
        )

    # ── /reply ────────────────────────────────────────

    @app_commands.command(
        name="reply",
        description="Send a visible reply to the ticket user.",
    )
    @app_commands.describe(
        message="Your reply message",
        attachment="Optional file attachment",
    )
    async def reply(
        self,
        interaction: discord.Interaction,
        message: str,
        attachment: discord.Attachment | None = None,
    ) -> None:
        log.info(
            "[TICKET_REPLY] Staff %s (%s) sending reply in channel %s",
            interaction.user.id, interaction.user.name, interaction.channel_id
        )
        
        if not await self._check_staff(interaction):
            log.warning(
                "[TICKET_REPLY] Staff check failed for user %s",
                interaction.user.id
            )
            return
        if not await _require_capability(interaction, "reply"):
            log.warning(
                "[TICKET_REPLY] Capability check failed for user %s (reply)",
                interaction.user.id
            )
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            log.warning(
                "[TICKET_REPLY] No ticket found in channel %s",
                interaction.channel_id
            )
            return
        if not await self._check_relay_session_active(interaction, ticket):
            log.warning(
                "[TICKET_REPLY] Relay session not active for ticket %s",
                ticket["id"]
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Touch activity timestamp
        await queries.touch_ticket_activity(interaction.channel_id)

        attachments = [attachment] if attachment else None
        success = await relay_service.relay_staff_to_user(
            self.bot,
            interaction.channel_id,
            interaction.user,  # type: ignore
            message,
            anonymous=False,
            attachments=attachments,
        )

        if success:
            log.info(
                "[TICKET_REPLY_SUCCESS] Reply sent for ticket in channel %s",
                interaction.channel_id
            )
            await interaction.followup.send(
                embed=message_style.success_embed("Reply sent."),
                ephemeral=True,
            )
        else:
            log.warning(
                "[TICKET_REPLY_FAILED] Failed to send reply for ticket in channel %s",
                interaction.channel_id
            )
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Failed to send reply. The user may have DMs closed."
                ),
                ephemeral=True,
            )

    # ── /anreply ──────────────────────────────────────

    @app_commands.command(
        name="anreply",
        description="Send an anonymous reply to the ticket user.",
    )
    @app_commands.describe(
        message="Your anonymous reply message",
        attachment="Optional file attachment",
    )
    async def anreply(
        self,
        interaction: discord.Interaction,
        message: str,
        attachment: discord.Attachment | None = None,
    ) -> None:
        log.info(
            "[TICKET_ANREPLY] Staff %s (%s) sending anonymous reply in channel %s",
            interaction.user.id, interaction.user.name, interaction.channel_id
        )
        
        if not await self._check_staff(interaction):
            log.warning(
                "[TICKET_ANREPLY] Staff check failed for user %s",
                interaction.user.id
            )
            return
        if not await _require_capability(interaction, "anreply"):
            log.warning(
                "[TICKET_ANREPLY] Capability check failed for user %s (anreply)",
                interaction.user.id
            )
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            log.warning(
                "[TICKET_ANREPLY] No ticket found in channel %s",
                interaction.channel_id
            )
            return
        if not await self._check_relay_session_active(interaction, ticket):
            log.warning(
                "[TICKET_ANREPLY] Relay session not active for ticket %s",
                ticket["id"]
            )
            return

        await interaction.response.defer(ephemeral=True)

        await queries.touch_ticket_activity(interaction.channel_id)

        attachments = [attachment] if attachment else None
        success = await relay_service.relay_staff_to_user(
            self.bot,
            interaction.channel_id,
            interaction.user,  # type: ignore
            message,
            anonymous=True,
            attachments=attachments,
        )

        if success:
            log.info(
                "[TICKET_ANREPLY_SUCCESS] Anonymous reply sent for ticket in channel %s",
                interaction.channel_id
            )
            await interaction.followup.send(
                embed=message_style.success_embed("Anonymous reply sent."),
                ephemeral=True,
            )
        else:
            log.warning(
                "[TICKET_ANREPLY_FAILED] Failed to send anonymous reply for ticket in channel %s",
                interaction.channel_id
            )
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Failed to send reply. The user may have DMs closed."
                ),
                ephemeral=True,
            )

    # ── /transfer ─────────────────────────────────────

    @app_commands.command(
        name="transfer",
        description="Transfer this ticket to another staff member.",
    )
    @app_commands.describe(staff="Staff member to transfer to")
    async def transfer(
        self,
        interaction: discord.Interaction,
        staff: discord.Member,
    ) -> None:
        log.info(
            "[TICKET_TRANSFER] Staff %s (%s) transferring ticket in channel %s to %s",
            interaction.user.id, interaction.user.name, interaction.channel_id, staff.id
        )
        
        if not await self._check_staff(interaction):
            log.warning(
                "[TICKET_TRANSFER] Staff check failed for user %s",
                interaction.user.id
            )
            return
        if not await _require_capability(interaction, "transfer"):
            log.warning(
                "[TICKET_TRANSFER] Capability check failed for user %s (transfer)",
                interaction.user.id
            )
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            log.warning(
                "[TICKET_TRANSFER] No ticket found in channel %s",
                interaction.channel_id
            )
            return

        # Verify target is staff
        if not await permission_service.is_staff(staff, interaction.guild_id):
            log.warning(
                "[TICKET_TRANSFER] Target %s is not a staff member",
                staff.id
            )
            await interaction.response.send_message(
                embed=message_style.error_embed(
                    f"{staff.mention} is not a support staff member."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        success, msg = await workflow_service.transfer_ticket(
            interaction.channel, staff, self.bot,  # type: ignore
        )

        if success:
            log.info(
                "[TICKET_TRANSFER_SUCCESS] Ticket transferred to %s in channel %s",
                staff.id, interaction.channel_id
            )
            await interaction.followup.send(
                embed=message_style.success_embed(msg),
            )
        else:
            log.warning(
                "[TICKET_TRANSFER_FAILED] Transfer failed in channel %s: %s",
                interaction.channel_id, msg
            )
            await interaction.followup.send(
                embed=message_style.error_embed(msg),
                ephemeral=True,
            )

    # ── /status ───────────────────────────────────────

    @app_commands.command(
        name="status",
        description="Set the workflow status of this ticket.",
    )
    @app_commands.describe(status="New status for the ticket")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Open (return to queue)", value="open"),
            app_commands.Choice(name="Investigating", value="investigating"),
            app_commands.Choice(name="Waiting User", value="waiting-user"),
            app_commands.Choice(name="Escalated", value="escalated"),
            app_commands.Choice(name="Resolved", value="resolved"),
        ]
    )
    async def status(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str],
    ) -> None:
        log.info(
            "[TICKET_STATUS] Staff %s (%s) setting status to %s in channel %s",
            interaction.user.id, interaction.user.name, status.value, interaction.channel_id
        )
        
        if not await self._check_staff(interaction):
            log.warning(
                "[TICKET_STATUS] Staff check failed for user %s",
                interaction.user.id
            )
            return
        if not await _require_capability(interaction, "status"):
            log.warning(
                "[TICKET_STATUS] Capability check failed for user %s (status)",
                interaction.user.id
            )
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            log.warning(
                "[TICKET_STATUS] No ticket found in channel %s",
                interaction.channel_id
            )
            return

        await interaction.response.defer()

        success, msg = await workflow_service.set_ticket_status(
            interaction.channel, status.value, self.bot,  # type: ignore
        )

        if success:
            log.info(
                "[TICKET_STATUS_SUCCESS] Status set to %s for ticket in channel %s",
                status.value, interaction.channel_id
            )
            await interaction.followup.send(
                embed=message_style.success_embed(msg),
            )
        else:
            log.warning(
                "[TICKET_STATUS_FAILED] Status change failed in channel %s: %s",
                interaction.channel_id, msg
            )
            await interaction.followup.send(
                embed=message_style.error_embed(msg),
                ephemeral=True,
            )

    # ── /close ────────────────────────────────────────

    @app_commands.command(
        name="close",
        description="Close this ticket and delete the channel.",
    )
    @app_commands.describe(
        duration="Delay before auto-closing (e.g. 30s, 25m, 2h, 3d). Omit for immediate close.",
        message="Optional final staff message included in the closure confirmation.",
    )
    async def close(
        self,
        interaction: discord.Interaction,
        duration: str | None = None,
        message: str | None = None,
    ) -> None:
        log.info(
            "[TICKET_CLOSE] Staff %s (%s) closing ticket in channel %s (duration: %s)",
            interaction.user.id, interaction.user.name, interaction.channel_id, duration
        )
        
        if not await self._check_staff(interaction):
            log.warning(
                "[TICKET_CLOSE] Staff check failed for user %s",
                interaction.user.id
            )
            return
        if not await _require_capability(interaction, "close"):
            log.warning(
                "[TICKET_CLOSE] Capability check failed for user %s (close)",
                interaction.user.id
            )
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            log.warning(
                "[TICKET_CLOSE] No ticket found in channel %s",
                interaction.channel_id
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            log.warning(
                "[TICKET_CLOSE] Invalid channel type in channel %s",
                interaction.channel_id
            )
            return
        closure_message = message.strip() if message else None

        # ── Duplicate-close guardrail ──
        # If a pending autoclose exists for this ticket, refuse to schedule
        # another one. Manual immediate /close is still allowed below only
        # when no pending schedule exists; staff must cancel (user replies)
        # before re-scheduling.
        existing_schedule = ticket.get("scheduled_close_at") if ticket else None
        if existing_schedule:
            existing_label = ticket.get("autoclose_duration") or "scheduled"
            log.info(
                "[TICKET_CLOSE] Duplicate close blocked for channel %s (already scheduled: %s)",
                channel.id, existing_schedule,
            )
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "Ticket already has a scheduled closure.\n"
                    f"Scheduled for: **{existing_label}** (`{existing_schedule} UTC`).\n"
                    "Wait for the existing closure or cancel it before scheduling another."
                ),
            )
            return

        # ── Immediate close ──
        if not duration:
            log.info(
                "[TICKET_CLOSE_IMMEDIATE] Immediate close for ticket in channel %s",
                channel.id
            )
            await interaction.response.send_message(
                embed=message_style.relay_embed(
                    description="🔒 Closing ticket…",
                    color=message_style.RELAY_NEUTRAL,
                ),
            )
            success, closed_ticket = await workflow_service.close_ticket(
                channel, interaction.user, self.bot, closure_message,  # type: ignore
            )
            if not success:
                log.warning(
                    "[TICKET_CLOSE_FAILED] Immediate close failed for channel %s",
                    channel.id
                )
                try:
                    await interaction.followup.send(
                        embed=message_style.error_embed("No open ticket found in this channel."),
                        ephemeral=True,
                    )
                except Exception:
                    pass
            else:
                log.info(
                    "[TICKET_CLOSE_SUCCESS] Ticket closed in channel %s",
                    channel.id
                )
            return

        # ── Scheduled autoclose ──
        parsed = _parse_duration(duration)
        if parsed is None:
            log.warning(
                "[TICKET_CLOSE] Invalid duration format: %s",
                duration
            )
            await interaction.response.send_message(
                embed=message_style.error_embed(
                    "Invalid duration. Use a single-unit format like `30s`, `25m`, `2h`, or `3d`."
                ),
                ephemeral=True,
            )
            return
        seconds, duration_label = parsed

        from datetime import datetime, timedelta, timezone
        close_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        # Store SQLite-comparable UTC timestamp so the autoclose loop's
        # `scheduled_close_at <= datetime('now')` comparison works correctly.
        close_at_db = close_at.strftime("%Y-%m-%d %H:%M:%S")
        await queries.schedule_autoclose(
            channel.id, close_at_db, duration_label, closure_message,
        )
        log.info(
            "[TICKET_CLOSE_SCHEDULED] Autoclose scheduled for channel %s in %s (at %s UTC)",
            channel.id, duration_label, close_at_db,
        )

        note = "\nFinal staff message will be included." if closure_message else ""
        await interaction.response.send_message(
            embed=message_style.relay_embed(
                description=(
                    f"⏳ Ticket scheduled to close in **{duration_label}** unless the user replies."
                    f"{note}"
                ),
                color=message_style.RELAY_NEUTRAL,
            ),
        )

    # ── /move ─────────────────────────────────────────

    @app_commands.command(
        name="move",
        description="Move this ticket to a different category.",
    )
    @app_commands.describe(category="Target category name")
    async def move(
        self,
        interaction: discord.Interaction,
        category: str,
    ) -> None:
        if not await self._check_staff(interaction):
            return
        if not await _require_capability(interaction, "move"):
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            return

        await interaction.response.defer()

        success, msg = await workflow_service.move_ticket(
            interaction.channel, category.strip(), interaction.guild,  # type: ignore
        )

        if success:
            await interaction.followup.send(
                embed=message_style.success_embed(msg),
            )
        else:
            await interaction.followup.send(
                embed=message_style.error_embed(msg),
                ephemeral=True,
            )

    # ── /priority ─────────────────────────────────────

    @app_commands.command(
        name="priority",
        description="View or set the priority of this ticket.",
    )
    @app_commands.describe(level="Priority level (omit to view current)")
    @app_commands.choices(
        level=[
            app_commands.Choice(name="🟢 Low", value="low"),
            app_commands.Choice(name="🟡 Medium", value="medium"),
            app_commands.Choice(name="🔴 High", value="high"),
        ]
    )
    async def priority(
        self,
        interaction: discord.Interaction,
        level: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await self._check_staff(interaction):
            return
        if not await _require_capability(interaction, "priority"):
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            return

        if level is None:
            # View current priority
            current = ticket.get("priority", "medium")
            emoji = PRIORITY_EMOJIS.get(current, "🟡")
            await interaction.response.send_message(
                embed=message_style.relay_embed(
                    description=f"Priority: {emoji} **{current.title()}**",
                ),
                ephemeral=True,
            )
            return

        # Set priority
        await queries.update_ticket_priority(interaction.channel_id, level.value)
        emoji = PRIORITY_EMOJIS.get(level.value, "🟡")
        await interaction.response.send_message(
            embed=message_style.success_embed(
                f"{emoji} Priority set to **{level.value.title()}**."
            ),
        )

    # ── /emoji ────────────────────────────────────────

    @app_commands.command(
        name="emoji",
        description="Set your personal staff emoji for ticket channels.",
    )
    @app_commands.describe(emoji="Your personal emoji (e.g. 🦊)")
    async def emoji(
        self,
        interaction: discord.Interaction,
        emoji: str,
    ) -> None:
        if not await self._check_staff(interaction):
            return

        success, msg = await emoji_service.validate_and_set_emoji(
            interaction.guild_id, interaction.user.id, emoji.strip(),
        )

        if success:
            await interaction.response.send_message(
                embed=message_style.success_embed(msg),
                ephemeral=True,
            )
            # Auto-refresh persistent emojilist
            await emoji_service.refresh_emojilist(interaction.guild, self.bot)
        else:
            await interaction.response.send_message(
                embed=message_style.error_embed(msg),
                ephemeral=True,
            )

    # ── /emojilist ────────────────────────────────────

    @app_commands.command(
        name="emojilist",
        description="Post or update the staff emoji registry in this channel.",
    )
    async def emojilist(self, interaction: discord.Interaction) -> None:
        if not await self._check_staff(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        registry = await emoji_service.get_emoji_registry(interaction.guild_id)
        embed = emoji_service._build_registry_content(registry, interaction.guild)

        if embed is None:
            await interaction.followup.send(
                embed=message_style.warning_embed(
                    "No staff emojis configured yet. Use `/emoji` to set yours."
                ),
                ephemeral=True,
            )
            return

        # Check for existing persistent message
        settings = await queries.get_guild_settings(interaction.guild_id)
        existing_msg = None

        if settings:
            ch_id = settings.get("emojilist_channel_id")
            msg_id = settings.get("emojilist_message_id")
            if ch_id and msg_id:
                ch = interaction.guild.get_channel(ch_id)
                if ch:
                    try:
                        existing_msg = await ch.fetch_message(msg_id)  # type: ignore
                    except (discord.NotFound, discord.Forbidden):
                        existing_msg = None

        if existing_msg:
            # Update existing message
            await existing_msg.edit(embed=embed)
            await interaction.followup.send(
                embed=message_style.success_embed("Emoji registry updated."),
                ephemeral=True,
            )
        else:
            # Post new registry message and store reference
            msg = await interaction.channel.send(embed=embed)
            await queries.upsert_guild_settings(
                interaction.guild_id,
                emojilist_channel_id=interaction.channel_id,
                emojilist_message_id=msg.id,
            )
            await interaction.followup.send(
                embed=message_style.success_embed("Emoji registry posted."),
                ephemeral=True,
            )


    # ── /note group ───────────────────────────────────

    note_group = app_commands.Group(name="note", description="Community-scoped staff notes.")

    @note_group.command(name="add", description="Add a staff note for this ticket user.")
    @app_commands.describe(content="Note content")
    async def note_add(self, interaction: discord.Interaction, content: str) -> None:
        if not await self._check_staff(interaction):
            return
        if not await _require_capability(interaction, "note_add"):
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            return

        source_guild_id = ticket.get("source_guild_id") or interaction.guild_id
        note_id = await note_service.add_note(
            user_id=ticket["user_id"],
            source_guild_id=source_guild_id,
            author_id=interaction.user.id,
            author_name=interaction.user.display_name,
            content=content,
        )
        await interaction.response.send_message(
            embed=message_style.success_embed(f"Note #{note_id} added."),
            ephemeral=True,
        )

    @note_group.command(name="remove", description="Remove a staff note for this ticket user.")
    async def note_remove(self, interaction: discord.Interaction) -> None:
        if not await self._check_staff(interaction):
            return
        if not await _require_capability(interaction, "note_remove"):
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            return

        source_guild_id = ticket.get("source_guild_id") or interaction.guild_id
        notes = await note_service.get_notes(ticket["user_id"], source_guild_id)

        if not notes:
            await interaction.response.send_message(
                embed=message_style.warning_embed("No notes to remove."),
                ephemeral=True,
            )
            return

        is_admin = permission_service.is_admin(interaction.user)  # type: ignore
        from bot.views.note_remove import NoteRemoveView
        view = NoteRemoveView(
            notes, interaction.user, is_admin, ticket["user_id"], source_guild_id,
        )
        await interaction.response.send_message(
            embed=message_style.relay_embed(title="📝  Remove Note"),
            view=view,
        )

    # ── /remind ───────────────────────────────────────

    @app_commands.command(
        name="remind",
        description="Get notified when the ticket creator replies.",
    )
    @app_commands.describe(cancel="Cancel your active reminder for this ticket")
    async def remind(
        self,
        interaction: discord.Interaction,
        cancel: bool = False,
    ) -> None:
        if not await self._check_staff(interaction):
            return
        if not await _require_capability(interaction, "remind"):
            return
        ticket = await self._check_ticket_channel(interaction)
        if ticket is None:
            return

        if cancel:
            # Cancel reminder
            await queries.delete_reminder(ticket["id"], interaction.user.id)
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    "Your response alert for this ticket has been cancelled."
                ),
                ephemeral=True,
            )
            log.info(
                "Reminder cancelled by %s for ticket %s",
                interaction.user.id, ticket["id"],
            )
            return

        # Check for duplicate reminder
        existing = await queries.get_reminder_for_staff(ticket["id"], interaction.user.id)
        if existing:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "You already have an active response alert for this ticket.\n"
                    "Use `/remind cancel:true` to remove it."
                ),
                ephemeral=True,
            )
            log.info(
                "Duplicate /remind blocked for %s on ticket %s",
                interaction.user.id, ticket["id"],
            )
            return

        # Create reminder
        created = await queries.create_reminder(
            ticket["id"], interaction.user.id, interaction.guild_id,  # type: ignore
        )
        if created:
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    "You will be notified when the ticket creator replies."
                ),
                ephemeral=True,
            )
            log.info(
                "Reminder created by %s for ticket %s",
                interaction.user.id, ticket["id"],
            )
        else:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "You already have an active response alert for this ticket."
                ),
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
