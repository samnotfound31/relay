"""
Relay Bot — Category Select View
Dropdown for users to pick a ticket category.
Phase 3: supports cross-server routing via source_guild_id.
"""

from __future__ import annotations

import discord
from bot.database import queries
from bot.services import ticket_service, message_style


class CategorySelect(discord.ui.Select):
    """Dropdown populated with ticket categories."""

    def __init__(
        self,
        guild_id: int,
        categories: list[dict],
        source_guild_id: int | None = None,
    ) -> None:
        self.guild_id = guild_id
        self.source_guild_id = source_guild_id
        options = [
            discord.SelectOption(
                label=cat["name"],
                description=cat["description"][:100] if cat["description"] else None,
                emoji=cat["emoji"] or "📂",
                value=cat["name"],
            )
            for cat in categories[:25]
        ]
        super().__init__(
            placeholder="Select a category…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"relay:category_select:{guild_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        # Defer IMMEDIATELY to prevent interaction timeout
        await interaction.response.defer(ephemeral=True)

        category_name = self.values[0]
        bot = interaction.client
        guild = bot.get_guild(self.guild_id)  # type: ignore
        if guild is None:
            await interaction.followup.send(
                embed=message_style.error_embed("Could not find the server."),
                ephemeral=True,
            )
            return

        # Resolve source guild for cross-server identity
        source_guild = None
        if self.source_guild_id and self.source_guild_id != self.guild_id:
            source_guild = bot.get_guild(self.source_guild_id)

        result = await ticket_service.open_ticket(
            guild,
            interaction.user,
            category_name,
            bot=bot,
            source_guild=source_guild,
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


class CategorySelectView(discord.ui.View):
    """Wraps the CategorySelect dropdown."""

    def __init__(
        self,
        guild_id: int,
        categories: list[dict],
        source_guild_id: int | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.add_item(CategorySelect(guild_id, categories, source_guild_id))
