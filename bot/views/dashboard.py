"""
Relay Bot — Operational Dashboard View
Persistent workflow controls attached to the staff ticket embed.

All controls delegate to existing services / queries — no logic duplication.
Authoritative DB state is fetched on every interaction; the view itself
stores no per-ticket mutable state and is fully restart-safe.

Layout:
  Row 0: Claim / Claimed (ownership)
  Row 1: Status dropdown
  Row 2: Priority dropdown
  Row 3: Remind, Context, Info, History, Add Note (operational)
  Row 4: Move, Close (critical)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

import discord
from discord import ui

from bot.config import PRIORITY_EMOJIS, STATUS_EMOJIS
from bot.database import queries
from bot.services import (
    emoji_service,
    message_style,
    note_service,
    permission_service,
    workflow_service,
)

log = logging.getLogger(__name__)

EXPIRED_MSG = "⚠️ Dashboard expired. Please refresh the panel."
NO_STAFF_MSG = "⚠️ You do not have permission to use this dashboard."
NO_TICKET_MSG = "⚠️ This dashboard is no longer attached to an open ticket."
NO_HISTORY_MSG = "⚠️ You do not have permission to access investigative history."


_STATUS_CHOICES: list[tuple[str, str]] = [
    ("open", "Open (return to queue)"),
    ("investigating", "Investigating"),
    ("waiting-user", "Waiting User"),
    ("escalated", "Escalated"),
    ("resolved", "Resolved"),
]

_PRIORITY_CHOICES: list[tuple[str, str]] = [
    ("low", "🟢 Low"),
    ("medium", "🟡 Medium"),
    ("high", "🔴 High"),
]


# ── Helpers ─────────────────────────────────────────────

async def _fresh_ticket(interaction: discord.Interaction) -> dict | None:
    """Return the authoritative ticket row for the interaction channel, or None."""
    channel_id = interaction.channel_id
    if channel_id is None:
        return None
    try:
        return await queries.get_ticket_by_channel(channel_id)
    except Exception as e:
        log.warning("Dashboard ticket fetch failed: %s", e)
        return None


async def _staff_guard(interaction: discord.Interaction) -> bool:
    """Check the actor is staff in the current guild. Responds with denial if not."""
    if not isinstance(interaction.user, discord.Member) or not interaction.guild_id:
        await _safe_reply(
            interaction,
            message_style.warning_embed(NO_STAFF_MSG),
            ephemeral=True,
        )
        return False
    try:
        ok = await permission_service.is_staff(interaction.user, interaction.guild_id)
    except Exception as e:
        log.warning("Dashboard staff check failed: %s", e)
        ok = False
    if not ok:
        await _safe_reply(
            interaction,
            message_style.warning_embed(NO_STAFF_MSG),
            ephemeral=True,
        )
    return ok


async def _owner_guard(interaction: discord.Interaction, ticket: dict) -> bool:
    """
    Check ownership or admin override for dashboard controls.

    Returns True if the user is:
    - The ticket owner (claimed_by matches user.id)
    - An administrator (can override ownership)
    - Ticket is unclaimed (no ownership restriction)

    Returns False and responds with denial otherwise.
    """
    claimed_by = ticket.get("claimed_by")
    if not claimed_by:
        # Unclaimed ticket — no ownership restriction
        return True

    if not isinstance(interaction.user, discord.Member):
        await _safe_reply(
            interaction,
            message_style.warning_embed(NO_STAFF_MSG),
            ephemeral=True,
        )
        return False

    # Check if user is the owner
    if interaction.user.id == claimed_by:
        return True

    # Check admin override
    try:
        is_admin = permission_service.is_admin(interaction.user)
    except Exception as e:
        log.warning("Dashboard admin check failed: %s", e)
        is_admin = False

    if is_admin:
        return True

    # Deny with ownership message
    owner_name = f"<@{claimed_by}>"
    # Try to resolve owner name from guild for better UX
    if interaction.guild:
        owner_member = interaction.guild.get_member(claimed_by)
        if owner_member:
            owner_name = f"{owner_member.mention} ({owner_member.display_name})"

    await _safe_reply(
        interaction,
        message_style.warning_embed(
            f"This ticket is currently being handled by {owner_name}.\n"
            "Only the assigned handler or administrators may use dashboard controls."
        ),
        ephemeral=True,
    )
    return False


async def _history_guard(interaction: discord.Interaction) -> bool:
    """Check investigative history access. Responds with denial if not."""
    if not isinstance(interaction.user, discord.Member) or not interaction.guild_id:
        await _safe_reply(
            interaction,
            message_style.warning_embed(NO_HISTORY_MSG),
            ephemeral=True,
        )
        return False
    try:
        ok = await permission_service.can_access_investigative_history(
            interaction.user, interaction.guild_id, "history",
        )
    except Exception as e:
        log.warning("Dashboard history check failed: %s", e)
        ok = False
    if not ok:
        await _safe_reply(
            interaction,
            message_style.warning_embed(NO_HISTORY_MSG),
            ephemeral=True,
        )
    return ok


# Transient network/transport errors that should be logged compactly
# rather than producing full traceback spam.
_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    aiohttp.ClientOSError,
    aiohttp.ServerDisconnectedError,
)


async def _safe_reply(
    interaction: discord.Interaction,
    embed: discord.Embed,
    *,
    ephemeral: bool = False,
) -> None:
    """Reply safely regardless of whether the interaction was already responded to."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
    except discord.NotFound:
        log.warning("Dashboard reply target not found (expired interaction)")
    except _TRANSIENT_ERRORS as e:
        log.warning("Dashboard reply interrupted (transient): %s", e)
    except Exception as e:
        log.warning("Dashboard reply failed: %s", e)


async def _safe_send_modal(
    interaction: discord.Interaction,
    modal: ui.Modal,
    label: str = "modal",
) -> None:
    """Dispatch a modal with transient-network resilience."""
    try:
        await interaction.response.send_modal(modal)
    except discord.InteractionResponded:
        log.warning("Dashboard %s: interaction already responded", label)
    except _TRANSIENT_ERRORS as e:
        log.warning("Dashboard %s dispatch interrupted (transient): %s", label, e)
    except Exception as e:
        log.warning("Dashboard %s dispatch failed: %s", label, e)


async def _find_dashboard_message(channel: discord.abc.Messageable) -> discord.Message | None:
    """
    Find the dashboard message in the channel by searching for messages
    with dashboard view components. Returns None if not found.
    """
    if not isinstance(channel, discord.TextChannel):
        return None
    try:
        async for msg in channel.history(limit=50):
            if msg.components and len(msg.components) > 0:
                for component in msg.components:
                    if hasattr(component, 'children'):
                        for child in component.children:
                            custom_id = getattr(child, 'custom_id', None)
                            if custom_id and custom_id.startswith('relay:dash:'):
                                return msg
    except Exception as e:
        log.warning("Failed to find dashboard message: %s", e)
    return None


async def _refresh_attached_view(interaction: discord.Interaction) -> None:
    """
    Rebuild the dashboard view to reflect fresh DB state and patch the
    originating dashboard message in-place. Stale or missing messages
    are tolerated silently — the operational result already happened
    and was reported through other replies.

    Handles two-phase lifecycle:
    - Pre-claim: PreClaimView (only Claim button), original embed unchanged
    - Post-claim: DashboardView (full operational controls) + ownership header embed

    If interaction is from a transient selector (ephemeral), finds the
    actual dashboard message in the channel and refreshes it.
    """
    msg = interaction.message
    channel = interaction.channel

    # If the message is ephemeral (from transient selector), find the actual dashboard message
    if msg and msg.flags.ephemeral:
        msg = await _find_dashboard_message(channel) if channel else None
        if msg is None:
            log.warning("Could not find dashboard message for refresh (ephemeral interaction)")
            return

    if msg is None:
        return
    ticket = await _fresh_ticket(interaction)

    claimed_by = ticket.get("claimed_by") if ticket else None
    if claimed_by:
        # Post-claim state: full operational dashboard + header
        view = DashboardView()
        view.refresh(ticket)
        guild = interaction.guild
        header_embed = view.build_header(ticket, guild)
        try:
            await msg.edit(view=view, embed=header_embed)
        except discord.NotFound:
            log.info("Dashboard message gone — skipping refresh")
        except discord.Forbidden:
            log.warning("Dashboard refresh forbidden on message %s", msg.id)
        except _TRANSIENT_ERRORS as e:
            log.warning("Dashboard refresh interrupted (transient): %s", e)
        except Exception as e:
            log.warning("Dashboard refresh failed: %s", e)
    else:
        # Pre-claim state: lightweight claim button only
        # Restore original ticket embed (remove header if present)
        view = PreClaimView()
        # Rebuild original ticket embed for pre-claim state
        if ticket:
            from bot.services.message_style import ticket_created_staff_embed
            from bot.services import ticket_service
            # Fetch user for embed
            bot = interaction.client
            user = None
            try:
                user = await bot.fetch_user(ticket["user_id"])
            except Exception:
                pass
            if user:
                embed = ticket_created_staff_embed(
                    ticket.get("community_ticket_number") or ticket["id"],
                    user,
                    ticket.get("category_name"),
                )
                try:
                    await msg.edit(view=view, embed=embed)
                except discord.NotFound:
                    log.info("Dashboard message gone — skipping refresh")
                except discord.Forbidden:
                    log.warning("Dashboard refresh forbidden on message %s", msg.id)
                except _TRANSIENT_ERRORS as e:
                    log.warning("Dashboard refresh interrupted (transient): %s", e)
                except Exception as e:
                    log.warning("Dashboard refresh failed: %s", e)
                return
        # Fallback: just update view if embed rebuild fails
        try:
            await msg.edit(view=view)
        except discord.NotFound:
            log.info("Dashboard message gone — skipping refresh")
        except discord.Forbidden:
            log.warning("Dashboard refresh forbidden on message %s", msg.id)
        except _TRANSIENT_ERRORS as e:
            log.warning("Dashboard refresh interrupted (transient): %s", e)
        except Exception as e:
            log.warning("Dashboard refresh failed: %s", e)


def _resolve_source_guild_id(ticket: dict, fallback: int | None) -> int:
    return ticket.get("source_guild_id") or fallback or ticket["guild_id"]


# ── Modals ──────────────────────────────────────────────

class _CloseModal(ui.Modal, title="Close Ticket"):
    duration = ui.TextInput(
        label="Delay (optional: 30s, 25m, 2h, 3d)",
        placeholder="Leave empty for immediate close",
        required=False,
        max_length=10,
    )
    closure_message = ui.TextInput(
        label="Final staff message (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Local import to avoid circular imports at module load
        from bot.cogs.tickets import _parse_duration

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await _safe_reply(
                interaction,
                message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return

        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await _safe_reply(
                interaction,
                message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return

        closure_msg = (self.closure_message.value or "").strip() or None
        duration_text = (self.duration.value or "").strip()

        # ── Scheduled close ──
        if duration_text:
            if ticket.get("scheduled_close_at"):
                existing_label = ticket.get("autoclose_duration") or "scheduled"
                await interaction.response.send_message(
                    embed=message_style.warning_embed(
                        "Ticket already has a scheduled closure.\n"
                        f"Scheduled for: **{existing_label}** "
                        f"(`{ticket['scheduled_close_at']} UTC`)."
                    ),
                    ephemeral=True,
                )
                return
            parsed = _parse_duration(duration_text)
            if parsed is None:
                await interaction.response.send_message(
                    embed=message_style.error_embed(
                        "Invalid duration. Use `30s`, `25m`, `2h`, or `3d`."
                    ),
                    ephemeral=True,
                )
                return
            seconds, duration_label = parsed
            close_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            close_at_db = close_at.strftime("%Y-%m-%d %H:%M:%S")
            await queries.schedule_autoclose(
                channel.id, close_at_db, duration_label, closure_msg,
            )
            log.info(
                "Dashboard scheduled autoclose for channel %s in %s (at %s UTC)",
                channel.id, duration_label, close_at_db,
            )
            note = "\nFinal staff message will be included." if closure_msg else ""
            await interaction.response.send_message(
                embed=message_style.relay_embed(
                    description=(
                        f"⏳ Ticket scheduled to close in **{duration_label}** "
                        f"unless the user replies.{note}"
                    ),
                    color=message_style.RELAY_NEUTRAL,
                ),
            )
            return

        # ── Immediate close ──
        await interaction.response.send_message(
            embed=message_style.relay_embed(
                description="🔒 Closing ticket…",
                color=message_style.RELAY_NEUTRAL,
            ),
        )
        success, _ = await workflow_service.close_ticket(
            channel, interaction.user, interaction.client, closure_msg,  # type: ignore[arg-type]
        )
        if not success:
            try:
                await interaction.followup.send(
                    embed=message_style.error_embed(
                        "No open ticket found in this channel."
                    ),
                    ephemeral=True,
                )
            except Exception:
                pass


class _MoveModal(ui.Modal, title="Move Ticket"):
    category = ui.TextInput(
        label="Target category name",
        placeholder="e.g. support, billing",
        required=True,
        max_length=80,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or interaction.guild is None:
            await _safe_reply(
                interaction,
                message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        success, msg = await workflow_service.move_ticket(
            channel, self.category.value.strip(), interaction.guild,
        )
        if success:
            await interaction.followup.send(embed=message_style.success_embed(msg))
        else:
            await interaction.followup.send(
                embed=message_style.error_embed(msg),
                ephemeral=True,
            )
        await _refresh_attached_view(interaction)


class _NoteModal(ui.Modal, title="Add Note"):
    content = ui.TextInput(
        label="Note content",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1500,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        source_guild_id = _resolve_source_guild_id(ticket, interaction.guild_id)
        try:
            note_id = await note_service.add_note(
                user_id=ticket["user_id"],
                source_guild_id=source_guild_id,
                author_id=interaction.user.id,
                author_name=interaction.user.display_name,
                content=self.content.value,
            )
        except Exception as e:
            log.warning("Dashboard add_note failed: %s", e)
            await interaction.response.send_message(
                embed=message_style.error_embed("Failed to add note."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=message_style.success_embed(f"Note #{note_id} added."),
            ephemeral=True,
        )


# ── Components ──────────────────────────────────────────

def _validate_emoji(emoji: str | None, component_name: str) -> str | None:
    """
    Validate emoji string and log component construction.
    Returns None if invalid, otherwise returns the emoji.
    """
    if emoji is None:
        log.debug("Dashboard component: %s, emoji=None, valid=True", component_name)
        return None
    try:
        # Check if emoji is a valid unicode string
        # U+FFFD is the replacement character for invalid unicode
        if "\ufffd" in emoji:
            log.warning(
                "Dashboard component: %s, emoji contains invalid unicode (U+FFFD), falling back to None",
                component_name,
            )
            return None
        # Check if emoji is a reasonable length (typical emojis are 1-4 chars)
        if len(emoji) > 10:
            log.warning(
                "Dashboard component: %s, emoji too long (%d chars), falling back to None",
                component_name,
                len(emoji),
            )
            return None
        log.debug("Dashboard component: %s, emoji=%s, valid=True", component_name, emoji)
        return emoji
    except Exception as e:
        log.warning(
            "Dashboard component: %s, emoji validation failed: %s, falling back to None",
            component_name,
            e,
        )
        return None


class _PreClaimButton(ui.Button):
    """Claim button for pre-claim (queue) state only."""

    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Claim Ticket",
            emoji=_validate_emoji("🤝", "PreClaimButton"),
            custom_id="relay:dash:preclaim",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "claim"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return

        # Re-check ownership against authoritative state (guards races)
        if ticket.get("claimed_by"):
            claimer = (
                interaction.guild.get_member(ticket["claimed_by"])
                if interaction.guild else None
            )
            name = claimer.display_name if claimer else f"User {ticket['claimed_by']}"
            await interaction.response.send_message(
                embed=message_style.warning_embed(f"Already claimed by **{name}**."),
                ephemeral=True,
            )
            await _refresh_attached_view(interaction)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        success, msg = await workflow_service.claim_ticket(
            channel, interaction.user, interaction.client,  # type: ignore[arg-type]
        )
        if success:
            await interaction.followup.send(embed=message_style.success_embed(msg))
            log.info("Dashboard claim succeeded for channel %s", channel.id)
        else:
            await interaction.followup.send(
                embed=message_style.warning_embed(msg),
                ephemeral=True,
            )
        await _refresh_attached_view(interaction)


class _ClaimButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Claim Ticket",
            emoji=_validate_emoji("🤝", "ClaimButton"),
            custom_id="relay:dash:claim",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "claim"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return

        # Re-check ownership against authoritative state (guards races)
        if ticket.get("claimed_by"):
            claimer = (
                interaction.guild.get_member(ticket["claimed_by"])
                if interaction.guild else None
            )
            name = claimer.display_name if claimer else f"User {ticket['claimed_by']}"
            await interaction.response.send_message(
                embed=message_style.warning_embed(f"Already claimed by **{name}**."),
                ephemeral=True,
            )
            await _refresh_attached_view(interaction)
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        success, msg = await workflow_service.claim_ticket(
            channel, interaction.user, interaction.client,  # type: ignore[arg-type]
        )
        if success:
            await interaction.followup.send(embed=message_style.success_embed(msg))
            log.info("Dashboard claim succeeded for channel %s", channel.id)
        else:
            await interaction.followup.send(
                embed=message_style.warning_embed(msg),
                ephemeral=True,
            )
        await _refresh_attached_view(interaction)


class _ChangeStatusButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Change Status",
            emoji=_validate_emoji("📊", "ChangeStatusButton"),
            custom_id="relay:dash:status",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "status"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return

        # Open transient status selector (no preselection)
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label in _STATUS_CHOICES
        ]
        select = ui.Select(
            placeholder="Select new status…",
            min_values=1,
            max_values=1,
            options=options,
        )

        async def status_callback(interaction: discord.Interaction) -> None:
            if not await _staff_guard(interaction):
                return
            ticket = await _fresh_ticket(interaction)
            if ticket is None:
                await interaction.response.send_message(
                    embed=message_style.warning_embed(EXPIRED_VIEW),
                    ephemeral=True,
                )
                return
            if not await _owner_guard(interaction, ticket):
                return

            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    embed=message_style.warning_embed(NO_TICKET_MSG),
                    ephemeral=True,
                )
                return

            await interaction.response.defer()
            new_status = select.values[0]
            success, msg = await workflow_service.set_ticket_status(
                channel, new_status, interaction.client,  # type: ignore[arg-type]
            )
            if success:
                await interaction.followup.send(embed=message_style.success_embed(msg))
                log.info(
                    "Dashboard status change %s on channel %s",
                    new_status, channel.id,
                )
            else:
                await interaction.followup.send(
                    embed=message_style.error_embed(msg),
                    ephemeral=True,
                )
            await _refresh_attached_view(interaction)

        select.callback = status_callback
        view = ui.View()
        view.add_item(select)
        try:
            await interaction.response.send_message(
                embed=message_style.relay_embed(
                    title="📋 Change Ticket Status",
                    description="Select a new status for this ticket.",
                ),
                view=view,
                ephemeral=True,
            )
        except discord.InteractionResponded:
            log.warning("Status selector: interaction already responded")
        except _TRANSIENT_ERRORS as e:
            log.warning("Status selector dispatch interrupted (transient): %s", e)


class _ChangePriorityButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Change Priority",
            emoji=_validate_emoji("🔺", "ChangePriorityButton"),
            custom_id="relay:dash:priority",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "priority"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return

        if interaction.channel_id is None:
            return

        # Open transient priority selector (no preselection)
        options = [
            discord.SelectOption(label=label, value=value)
            for value, label in _PRIORITY_CHOICES
        ]
        select = ui.Select(
            placeholder="Select new priority…",
            min_values=1,
            max_values=1,
            options=options,
        )

        async def priority_callback(interaction: discord.Interaction) -> None:
            if not await _staff_guard(interaction):
                return
            ticket = await _fresh_ticket(interaction)
            if ticket is None:
                await interaction.response.send_message(
                    embed=message_style.warning_embed(EXPIRED_VIEW),
                    ephemeral=True,
                )
                return
            if not await _owner_guard(interaction, ticket):
                return

            if interaction.channel_id is None:
                return
            level = select.values[0]
            await queries.update_ticket_priority(interaction.channel_id, level)
            emoji = PRIORITY_EMOJIS.get(level, "🟡")
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    f"{emoji} Priority set to **{level.title()}**."
                ),
            )
            log.info(
                "Dashboard priority change %s on channel %s",
                level, interaction.channel_id,
            )
            await _refresh_attached_view(interaction)

        select.callback = priority_callback
        view = ui.View()
        view.add_item(select)
        try:
            await interaction.response.send_message(
                embed=message_style.relay_embed(
                    title="🎯 Change Ticket Priority",
                    description="Select a new priority for this ticket.",
                ),
                view=view,
                ephemeral=True,
            )
        except discord.InteractionResponded:
            log.warning("Priority selector: interaction already responded")
        except _TRANSIENT_ERRORS as e:
            log.warning("Priority selector dispatch interrupted (transient): %s", e)


class _RemindButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Remind",
            emoji=_validate_emoji("🔔", "RemindButton"),
            custom_id="relay:dash:remind",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "remind"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return

        existing = await queries.get_reminder_for_staff(
            ticket["id"], interaction.user.id,
        )
        if existing:
            await queries.delete_reminder(ticket["id"], interaction.user.id)
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    "Your response alert for this ticket has been cancelled."
                ),
                ephemeral=True,
            )
            log.info(
                "Dashboard reminder cancelled by %s for ticket %s",
                interaction.user.id, ticket["id"],
            )
            return

        created = await queries.create_reminder(
            ticket["id"], interaction.user.id, interaction.guild_id,  # type: ignore[arg-type]
        )
        if created:
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    "You will be notified when the ticket creator replies."
                ),
                ephemeral=True,
            )
            log.info(
                "Dashboard reminder created by %s for ticket %s",
                interaction.user.id, ticket["id"],
            )
        else:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "You already have an active response alert for this ticket."
                ),
                ephemeral=True,
            )


class _ContextButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Context",
            emoji=_validate_emoji("📝", "ContextButton"),
            custom_id="relay:dash:context",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "context"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return

        bot = interaction.client
        owner_user = None
        try:
            owner_user = await bot.fetch_user(ticket["user_id"])
        except Exception:
            pass
        owner_name = (
            owner_user.display_name
            if owner_user
            else f"User {ticket['user_id']}"
        )
        assigned_staff = None
        owner_emoji = None
        claimed_by = ticket.get("claimed_by")
        if claimed_by and interaction.guild:
            member = interaction.guild.get_member(claimed_by)
            assigned_staff = (
                member.display_name if member else f"User {claimed_by}"
            )
            try:
                owner_emoji = await emoji_service.get_emoji_for_staff(
                    interaction.guild_id, claimed_by,
                )
            except Exception:
                owner_emoji = None

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
        await interaction.response.send_message(embed=embed)


class _InfoButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Info",
            emoji=_validate_emoji("ℹ️", "InfoButton"),
            custom_id="relay:dash:info",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "info"):
            return
        if not await _history_guard(interaction):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return

        from bot.views.continuity import ContinuityView

        bot = interaction.client
        source_guild_id = _resolve_source_guild_id(ticket, interaction.guild_id)
        source_guild = bot.get_guild(source_guild_id)
        try:
            owner = await bot.fetch_user(ticket["user_id"])
        except Exception:
            owner = None
        source_member = (
            source_guild.get_member(ticket["user_id"]) if source_guild else None
        )

        total_tickets = await queries.get_ticket_history_count_for_user(
            ticket["user_id"], source_guild_id,
        )
        note_count = await queries.get_staff_note_count(
            ticket["user_id"], source_guild_id,
        )
        transcript_count = await queries.get_transcript_count_for_user(
            ticket["user_id"], source_guild_id,
        )

        ticket_number = ticket.get("community_ticket_number") or ticket["id"]
        display_name = (
            source_member.display_name
            if source_member
            else (owner.display_name if owner else "Unknown")
        )
        username = owner.name if owner else "unknown"
        created_at = (
            owner.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if owner else "—"
        )
        joined_at = (
            source_member.joined_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            if source_member and source_member.joined_at else "—"
        )
        source_name = source_guild.name if source_guild else f"Guild {source_guild_id}"
        avatar_url = None
        if source_member:
            avatar_url = source_member.display_avatar.url
        elif owner:
            avatar_url = owner.display_avatar.url

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
        await interaction.response.send_message(
            embed=embed,
            view=ContinuityView(ticket["user_id"], source_guild_id),
        )


class _HistoryButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="History",
            emoji=_validate_emoji("🧭", "HistoryButton"),
            custom_id="relay:dash:history",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "history"):
            return
        if not await _history_guard(interaction):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return

        from bot.views.continuity import ContinuityView

        source_guild_id = _resolve_source_guild_id(ticket, interaction.guild_id)
        embed = message_style.relay_embed(
            title="🧭 Continuity Retrieval",
            description=(
                "Select a focused continuity view for this ticket user.\n\n"
                "**Notes** — moderation continuity lookup\n"
                "**Transcripts** — transcript artifact retrieval"
            ),
            footer=f"User {ticket['user_id']} • Community {source_guild_id}",
        )
        await interaction.response.send_message(
            embed=embed,
            view=ContinuityView(ticket["user_id"], source_guild_id),
        )


class _AddNoteButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Add Note",
            emoji=_validate_emoji("📌", "AddNoteButton"),
            custom_id="relay:dash:note",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "note_add"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return
        await _safe_send_modal(interaction, _NoteModal(), "note")


class _MoveButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Move",
            emoji=_validate_emoji("📂", "MoveButton"),
            custom_id="relay:dash:move",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "move"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return
        await _safe_send_modal(interaction, _MoveModal(), "move")


class _CloseButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Close",
            emoji=_validate_emoji("🔒", "CloseButton"),
            custom_id="relay:dash:close",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _staff_guard(interaction):
            return
        if not await _require_capability(interaction, "close"):
            return
        ticket = await _fresh_ticket(interaction)
        if ticket is None:
            await interaction.response.send_message(
                embed=message_style.warning_embed(NO_TICKET_MSG),
                ephemeral=True,
            )
            return
        if not await _owner_guard(interaction, ticket):
            return
        await _safe_send_modal(interaction, _CloseModal(), "close")


# ── Permission Guard ─────────────────────────────────

async def _require_capability(
    interaction: discord.Interaction,
    capability: str,
) -> bool:
    """Check if the member has the required capability. Returns True if authorized, False otherwise."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    
    from bot.services.permission_service import can_use_command
    if await can_use_command(interaction.user, interaction.guild.id, capability):
        return True
    
    await interaction.response.send_message(
        embed=message_style.warning_embed(
            "⚠️ Your Relay role does not allow access to this operation."
        ),
        ephemeral=True,
    )
    return False


# ── Dashboard Button Components ──────────────────────────────────────

class PreClaimView(ui.View):
    """
    Lightweight pre-claim view with only the Claim button.
    Shown for unclaimed tickets in the queue.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(_PreClaimButton())


class DashboardView(ui.View):
    """
    Persistent operational dashboard attached to the staff ticket embed.

    Two-phase lifecycle:
    - Pre-claim: only Claim button (via PreClaimView)
    - Post-claim: full operational controls with ownership header

    The view itself is stateless — `refresh(ticket)` reflects fresh DB
    state into component labels/options before the message is sent or
    edited. A single instance is registered at startup as a persistent
    view; all interactions are processed against authoritative DB state.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)
        # Row 0 — Workflow ownership
        self.claim_btn = _ClaimButton()
        self.add_item(self.claim_btn)
        # Row 1 — Control row (status/priority change)
        self.add_item(_ChangeStatusButton())
        self.add_item(_ChangePriorityButton())
        # Row 2 — Operational actions
        self.add_item(_RemindButton())
        self.add_item(_ContextButton())
        self.add_item(_InfoButton())
        self.add_item(_HistoryButton())
        self.add_item(_AddNoteButton())
        # Row 3 — Critical actions
        self.add_item(_MoveButton())
        self.add_item(_CloseButton())

    def refresh(self, ticket: dict | None) -> None:
        """Sync component labels/defaults to current ticket state."""
        claimed_by = ticket.get("claimed_by") if ticket else None
        if claimed_by:
            self.claim_btn.label = "Claimed"
            self.claim_btn.style = discord.ButtonStyle.secondary
            self.claim_btn.disabled = True
            self.claim_btn.emoji = "✅"
        else:
            self.claim_btn.label = "Claim Ticket"
            self.claim_btn.style = discord.ButtonStyle.primary
            self.claim_btn.disabled = False
            self.claim_btn.emoji = "🤝"

    def build_header(self, ticket: dict, guild: discord.Guild | None) -> discord.Embed:
        """Build ownership header for post-claim dashboard."""
        claimed_by = ticket.get("claimed_by")
        if not claimed_by:
            return None

        owner_member = guild.get_member(claimed_by) if guild else None
        owner_name = owner_member.display_name if owner_member else f"User {claimed_by}"
        owner_mention = owner_member.mention if owner_member else f"<@{claimed_by}>"

        ticket_status = ticket.get("ticket_status", "open")
        status_emoji = STATUS_EMOJIS.get(ticket_status, "")
        status_label = f"{status_emoji} **{ticket_status}**" if status_emoji else f"**{ticket_status}**"

        priority = ticket.get("priority", "medium")
        priority_emoji = PRIORITY_EMOJIS.get(priority, "🟡")
        priority_label = f"{priority_emoji} **{priority.title()}**"

        relay_session = (
            "Connected"
            if ticket.get("relay_session_status", "active") == "active"
            else "Disconnected"
        )

        description = (
            f"**ACTIVE HANDLER**\n"
            f"{owner_mention} ({owner_name})\n\n"
            f"**Status:** {status_label}\n"
            f"**Priority:** {priority_label}\n"
            f"**Relay:** {relay_session}"
        )

        return message_style.relay_embed(
            description=description,
            color=message_style.RELAY_NEUTRAL,
        )
