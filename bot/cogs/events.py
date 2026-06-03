"""
Relay Bot — Events Cog
Handles DM reception and relays user messages to staff channels.
Phase 3: cross-server routing — resolves support guilds for linked sources.
"""

from __future__ import annotations

import logging

import discord
from discord import Object
from discord.ext import commands, tasks

from bot.config import (
    INACTIVITY_THRESHOLD_SECONDS,
    INACTIVITY_CHECK_INTERVAL,
    RENAME_RESYNC_INTERVAL,
    AUTOCLOSE_CHECK_INTERVAL,
)
from bot.database import queries
from bot.services import guild_mode, relay_service, ticket_service, message_style
from bot.services import workflow_service

log = logging.getLogger(__name__)


class Events(commands.Cog):
    """Listens for DMs, routes relay messages, and runs inactivity checks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.inactivity_loop.start()
        self.rename_resync_loop.start()
        self.autoclose_loop.start()

    def cog_unload(self) -> None:
        self.inactivity_loop.cancel()
        self.rename_resync_loop.cancel()
        self.autoclose_loop.cancel()

    # ── DM Listener ──────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore bots (prevents loops)
        if message.author.bot:
            return

        # Only handle DMs
        if not isinstance(message.channel, discord.DMChannel):
            return

        # Check for existing open ticket
        ticket = await ticket_service.get_active_ticket_for_user(
            self.bot, message.author.id,
        )

        if ticket:
            # Touch activity timestamp
            await queries.touch_ticket_activity(ticket["channel_id"])
            await queries.mark_user_reply(
                ticket["channel_id"],
                message.author.id,
                message.author.display_name,
            )

            # Cancel any pending autoclose due to user activity
            if ticket.get("scheduled_close_at"):
                cancelled = await queries.cancel_autoclose(ticket["channel_id"])
                if cancelled:
                    log.info(
                        "Autoclose cancelled for channel %s due to user activity",
                        ticket["channel_id"],
                    )
                    channel = self.bot.get_channel(ticket["channel_id"])
                    if channel and isinstance(channel, discord.TextChannel):
                        await channel.send(
                            embed=message_style.warning_embed(
                                "⚠️ Scheduled ticket closure cancelled due to user activity."
                            )
                        )

            # Relay DM to staff channel
            success = await relay_service.relay_user_to_staff(self.bot, message)
            if success:
                try:
                    await message.add_reaction("✉️")
                except discord.Forbidden:
                    pass

                # Trigger response reminders
                await self._trigger_reminders(ticket, message)
            else:
                await message.channel.send(
                    embed=message_style.warning_embed(
                        "Your ticket channel could not be found. It may have been deleted."
                    )
                )
        else:
            # No open ticket — ignore unsolicited DM
            # Relay only operates inside already-established operational context
            return

    async def _trigger_reminders(self, ticket: dict, message: discord.Message) -> None:
        """Trigger response reminders for a ticket when user replies."""
        reminders = await queries.get_reminders_for_ticket(ticket["id"])
        if not reminders:
            return

        channel = self.bot.get_channel(ticket["channel_id"])
        if not channel or not isinstance(channel, discord.TextChannel):
            # Clear stale reminders if channel missing
            await queries.delete_all_reminders_for_ticket(ticket["id"])
            return

        # Ping all subscribed staff
        staff_mentions = []
        for reminder in reminders:
            staff_id = reminder["staff_id"]
            staff_member = channel.guild.get_member(staff_id)
            if staff_member:
                staff_mentions.append(f"<@{staff_id}>")
            else:
                # Staff member left guild - skip but will clear all after
                log.warning(f"Staff member {staff_id} not found in guild {channel.guild.id}")

        if staff_mentions:
            mentions = " ".join(staff_mentions)
            await channel.send(
                content=mentions,
                embed=message_style.relay_embed(
                    description="🔔 Response received from ticket creator.",
                    color=message_style.RELAY_NEUTRAL,
                )
            )
            log.info(
                "Reminders triggered for ticket %s, notified %s staff",
                ticket["id"], len(staff_mentions),
            )

        # Auto-clear all reminders after firing
        await queries.delete_all_reminders_for_ticket(ticket["id"])
        log.info("Cleared all reminders for ticket %s after trigger", ticket["id"])


    # ── Inactivity Loop ──────────────────────────────

    @tasks.loop(seconds=INACTIVITY_CHECK_INTERVAL)
    async def inactivity_loop(self) -> None:
        """Periodically check for inactive tickets and rename them."""
        for guild in self.bot.guilds:
            try:
                inactive = await queries.get_inactive_tickets(
                    guild.id, INACTIVITY_THRESHOLD_SECONDS,
                )
                for ticket in inactive:
                    channel = guild.get_channel(ticket["channel_id"])
                    if channel:
                        await workflow_service.mark_inactive(
                            channel, ticket, self.bot,  # type: ignore
                        )
            except Exception:
                pass  # Don't crash the loop

    @inactivity_loop.before_loop
    async def before_inactivity_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ── Rename Resync Loop ────────────────────────────

    @tasks.loop(seconds=RENAME_RESYNC_INTERVAL)
    async def rename_resync_loop(self) -> None:
        """
        Lightweight deferred cosmetic rename resync.
        Only processes channels explicitly flagged after a rate-limited rename.
        Always recomputes the desired name from CURRENT DB state.
        """
        for guild in self.bot.guilds:
            try:
                pending = await queries.get_pending_rename_resyncs(guild.id)
                for ticket in pending:
                    await workflow_service.perform_rename_resync(self.bot, ticket)
            except Exception:
                pass  # Don't crash the loop

    @rename_resync_loop.before_loop
    async def before_rename_resync_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ── Autoclose Loop ────────────────────────────────

    @tasks.loop(seconds=AUTOCLOSE_CHECK_INTERVAL)
    async def autoclose_loop(self) -> None:
        """Check for scheduled autocloses and execute them."""
        try:
            due = await queries.get_due_autocloses()
        except Exception as e:
            log.warning("Autoclose loop: failed to fetch due autocloses: %s", e)
            return

        for ticket in due:
            try:
                channel = self.bot.get_channel(ticket["channel_id"])
                if channel and isinstance(channel, discord.TextChannel):
                    # Ensure closed_by is a real Member for transcript generation
                    closed_by = channel.guild.me
                    if closed_by is None:
                        try:
                            closed_by = await channel.guild.fetch_member(self.bot.user.id)
                        except Exception:
                            # Fallback to Object if fetch fails (shouldn't happen)
                            closed_by = Object(id=self.bot.user.id)

                    # Pass closure message explicitly for autoclose
                    closure_msg = ticket.get("autoclose_closure_message")
                    success, _ = await workflow_service.close_ticket(
                        channel, closed_by, self.bot, closure_msg,  # type: ignore
                    )
                    if success:
                        log.info(
                            "Autoclose executed for channel %s (ticket %s)",
                            ticket["channel_id"], ticket["id"],
                        )
                    else:
                        # Ticket already closed or no longer open — clear stale flag
                        await queries.clear_autoclose_flag(ticket["id"])
                        log.info(
                            "Autoclose stale flag cleared for ticket %s (no open row)",
                            ticket["id"],
                        )
                else:
                    # Channel missing — clear the flag to prevent infinite retries
                    await queries.clear_autoclose_flag(ticket["id"])
                    log.info(
                        "Autoclose flag cleared for ticket %s (channel %s missing)",
                        ticket["id"], ticket["channel_id"],
                    )
            except Exception as e:
                log.warning(
                    "Autoclose execution failed for ticket %s: %s",
                    ticket.get("id"), e,
                )
                # Clear stale flag on any exception to prevent infinite retries
                try:
                    await queries.clear_autoclose_flag(ticket["id"])
                    log.info(
                        "Autoclose flag cleared for ticket %s after exception",
                        ticket["id"],
                    )
                except Exception:
                    pass
                continue

    @autoclose_loop.before_loop
    async def before_autoclose_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
