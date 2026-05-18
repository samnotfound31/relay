"""
Relay Bot — Ticket Panel View
Persistent "Open Ticket" button placed by /announce.
Phase 3: defers immediately, resolves cross-server routing.
"""

from __future__ import annotations

import logging

import discord
from bot.database import queries
from bot.services import ticket_service, message_style
from bot.views.category_select import CategorySelectView

log = logging.getLogger(__name__)


class OpenTicketButton(discord.ui.Button):
    """The 🎫 Open Ticket button."""

    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Open Ticket",
            emoji="🎫",
            custom_id="relay:open_ticket",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        log.info("Open Ticket button clicked by user %s in guild %s", interaction.user.id, interaction.guild_id)
        
        try:
            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message(
                    embed=message_style.error_embed("This button only works in a server."),
                    ephemeral=True,
                )
                return

            # Defer IMMEDIATELY — cross-server routing can be slow
            await interaction.response.defer(ephemeral=True)

            bot = interaction.client
            source_guild = guild

            # Resolve where the ticket should go
            try:
                target_guild, source_guild_id = await ticket_service.resolve_target_guild(
                    guild, bot,
                )
                log.info("Target guild resolved to %s for user %s", target_guild.id, interaction.user.id)
            except Exception as e:
                log.error("Failed to resolve target guild for user %s: %s", interaction.user.id, e, exc_info=True)
                await interaction.followup.send(
                    embed=message_style.error_embed("Failed to resolve target server for ticket creation."),
                    ephemeral=True,
                )
                return

            # Check for categories in the TARGET guild (where ticket will be created)
            try:
                categories = await queries.get_categories(target_guild.id)
            except Exception as e:
                log.error("Failed to fetch categories for guild %s: %s", target_guild.id, e, exc_info=True)
                await interaction.followup.send(
                    embed=message_style.error_embed("Failed to fetch ticket categories. Please try again."),
                    ephemeral=True,
                )
                return
                
            if categories:
                view = CategorySelectView(target_guild.id, categories, source_guild.id)
                await interaction.followup.send(
                    embed=message_style.relay_embed(
                        title="Select a Category",
                        description="Choose the category that best describes your issue.",
                    ),
                    view=view,
                    ephemeral=True,
                )
            else:
                # Direct ticket creation
                log.info("No categories configured, proceeding with direct ticket creation for user %s", interaction.user.id)
                result = await ticket_service.open_ticket(
                    target_guild,
                    interaction.user,
                    bot=bot,
                    source_guild=source_guild if source_guild_id else None,
                )
                if result is None or isinstance(result, str):
                    block_msg = result or "You already have an open ticket."
                    
                    # Check if this is a permission error
                    if isinstance(result, str) and result.startswith("FORBIDDEN:"):
                        log.warning("Permission error for user %s in guild %s: %s", interaction.user.id, target_guild.id, result)
                        await interaction.followup.send(
                            embed=message_style.error_embed(
                                "Relay is missing required permissions to create ticket channels. Please ensure the bot has Manage Channels permission."
                            ),
                            ephemeral=True,
                        )
                    else:
                        await interaction.followup.send(
                            embed=message_style.warning_embed(block_msg),
                            ephemeral=True,
                        )
                else:
                    ticket_id, _ = result
                    log.info("Ticket %s created successfully for user %s", ticket_id, interaction.user.id)
                    await interaction.followup.send(
                        embed=message_style.success_embed(
                            f"Ticket `#{ticket_id}` created! Check your DMs."
                        ),
                        ephemeral=True,
                    )
        except discord.Forbidden as e:
            log.error("Forbidden error in OpenTicketButton callback for user %s: %s", interaction.user.id, e, exc_info=True)
            # Try to send an error response if we haven't already responded
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        embed=message_style.error_embed(
                            "Relay is missing required permissions to create ticket channels. Please ensure the bot has Manage Channels permission."
                        ),
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        embed=message_style.error_embed(
                            "Relay is missing required permissions to create ticket channels. Please ensure the bot has Manage Channels permission."
                        ),
                        ephemeral=True,
                    )
            except Exception:
                log.error("Failed to send error message to user %s after Forbidden exception", interaction.user.id)
        except discord.HTTPException as e:
            log.error("HTTP error in OpenTicketButton callback for user %s: %s", interaction.user.id, e, exc_info=True)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        embed=message_style.error_embed("A Discord API error occurred. Please try again."),
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        embed=message_style.error_embed("A Discord API error occurred. Please try again."),
                        ephemeral=True,
                    )
            except Exception:
                log.error("Failed to send error message to user %s after HTTPException", interaction.user.id)
        except Exception as e:
            log.error("Unexpected error in OpenTicketButton callback for user %s: %s", interaction.user.id, e, exc_info=True)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        embed=message_style.error_embed("An unexpected error occurred. Please try again."),
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        embed=message_style.error_embed("An unexpected error occurred. Please try again."),
                        ephemeral=True,
                    )
            except Exception:
                log.error("Failed to send error message to user %s after unexpected exception", interaction.user.id)


class TicketPanelView(discord.ui.View):
    """Persistent view containing the Open Ticket button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(OpenTicketButton())
