"""
Relay Bot — Help Cog
Provides the interactive /help handbook and contextual onboarding tips.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import queries
from bot.services import message_style
from bot.views.help import HelpView, _home_embed, _check_permission, HELP_CATEGORIES
from bot.config import RELAY_COLOR

log = logging.getLogger(__name__)


class Help(commands.Cog):
    """Interactive help handbook and contextual onboarding framework."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /help Command ─────────────────────────────────

    @app_commands.command(name="help", description="Open the Relay operational handbook")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Ephemeral interactive help handbook with permission-aware categories."""
        # Filter categories based on user permissions
        visible_categories = []
        for cat_id, name, desc, gate in HELP_CATEGORIES:
            if await _check_permission(interaction, gate):
                visible_categories.append((cat_id, name, desc, gate))
        
        embed = _home_embed(interaction, visible_categories)
        view = HelpView(interaction, visible_categories)
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )

    # ── Tip Type #1: Server Setup Guidance ─────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Send setup guidance when Relay joins a new server (once per guild)."""
        # Check if already delivered
        already = await queries.has_onboarding_state("guild_setup", guild.id, guild.id)
        if already:
            return

        # Find a suitable channel to send the setup message
        target_channel = guild.system_channel or next(
            (ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages),
            None,
        )
        if target_channel is None:
            log.warning("No writable channel found for setup tip in guild %s", guild.id)
            return

        embed = discord.Embed(
            title="Welcome to Relay",
            description=(
                "Thanks for adding **Relay** to your server.\n\n"
                "Here's how to get started:\n"
                "\u200b\n"
                "### Quick Setup\n"
                "**1.** `/addsupportrole` — Designate which roles can handle tickets\n"
                "**2.** `/setcategory` — Create support categories for ticket routing\n"
                "**3.** `/announce` — Deploy the support panel for users\n"
                "\u200b\n"
                "### Cross-Server Support\n"
                "If this is a **support server** receiving tickets from other communities:\n"
                "- Use `/linksupport` in the community server to connect it here\n"
                "- Configure categories to organize incoming tickets\n"
                "\u200b\n"
                "### Need Help?\n"
                "Run `/help` anytime for the full operational handbook.\n"
                "\u200b\n"
                "*This message is sent once per server.*"
            ),
            color=RELAY_COLOR,
        )
        embed.set_footer(text="Relay • Setup Guide")

        try:
            await target_channel.send(embed=embed)
            await queries.set_onboarding_state("guild_setup", guild.id, guild.id)
            log.info("Delivered setup guidance to guild %s", guild.id)
        except discord.Forbidden:
            log.warning("Cannot send setup tip in guild %s (forbidden)", guild.id)
        except Exception as e:
            log.warning("Failed to deliver setup guidance to guild %s: %s", guild.id, e)

    # ── Tip Type #3: New Staff First-Use Guidance ──────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Detect first-time staff dashboard usage and offer /help tip."""
        # Only trigger on component interactions from dashboard
        if interaction.type != discord.InteractionType.component:
            return
        if not interaction.data:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("relay:dash:"):
            return

        # Only trigger on claim (first meaningful operational action)
        if custom_id not in ("relay:dash:claim", "relay:dash:preclaim"):
            return

        # Must have a guild and user
        if not interaction.guild or not interaction.user:
            return

        user_id = interaction.user.id
        guild_id = interaction.guild.id

        # Check if already delivered
        already = await queries.has_onboarding_state("staff_first_use", user_id, guild_id)
        if already:
            return

        # Mark as delivered first to prevent races
        await queries.set_onboarding_state("staff_first_use", user_id, guild_id)

        # Send subtle first-use tip (ephemeral followup after a short delay)
        embed = discord.Embed(
            description=(
                "Welcome to your first ticket claim.\n\n"
                "If you're new to Relay's workflow tools, "
                "run `/help` for the operational handbook — "
                "it covers ticket lifecycle, dashboard controls, "
                "and best practices.\n\n"
                "*This tip appears once per server.*"
            ),
            color=RELAY_COLOR,
        )
        embed.set_footer(text="Relay • First-Use Tip")

        try:
            # Use followup since the claim interaction has its own response
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                # If somehow response isn't done, don't interfere
                pass
        except discord.NotFound:
            pass
        except Exception as e:
            log.debug("First-use tip delivery failed (non-critical): %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
