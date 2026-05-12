"""
Relay Bot — Ticket Panel View
Persistent "Open Ticket" button placed by /announce.
Phase 3: defers immediately, resolves cross-server routing.
"""

from __future__ import annotations

import discord
from bot.database import queries
from bot.services import ticket_service, message_style
from bot.views.category_select import CategorySelectView


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
        target_guild, source_guild_id = await ticket_service.resolve_target_guild(
            guild, bot,
        )

        # Check for categories in the TARGET guild (where ticket will be created)
        categories = await queries.get_categories(target_guild.id)
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
            result = await ticket_service.open_ticket(
                target_guild,
                interaction.user,
                bot=bot,
                source_guild=source_guild if source_guild_id else None,
            )
            if result is None or isinstance(result, str):
                block_msg = result or "You already have an open ticket."
                await interaction.followup.send(
                    embed=message_style.warning_embed(block_msg),
                    ephemeral=True,
                )
            else:
                ticket_id, _ = result
                await interaction.followup.send(
                    embed=message_style.success_embed(
                        f"Ticket `#{ticket_id}` created! Check your DMs."
                    ),
                    ephemeral=True,
                )


class TicketPanelView(discord.ui.View):
    """Persistent view containing the Open Ticket button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(OpenTicketButton())
