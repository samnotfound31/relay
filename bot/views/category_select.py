"""
Relay Bot — Category Select View
Dropdown for users to pick a ticket category.
Phase 3: supports cross-server routing via source_guild_id.
"""

from __future__ import annotations

import logging

import discord
from bot.database import queries
from bot.services import ticket_service, message_style, permission_audit

log = logging.getLogger(__name__)


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
        log.info("Category select callback triggered by user %s in guild %s", interaction.user.id, self.guild_id)
        
        try:
            # Defer IMMEDIATELY to prevent interaction timeout
            await interaction.response.defer(ephemeral=True)

            category_name = self.values[0]
            log.info("User %s selected category %s in guild %s", interaction.user.id, category_name, self.guild_id)
            
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
                log.info("Cross-server routing: source_guild_id=%s, target_guild_id=%s", self.source_guild_id, self.guild_id)

            result = await ticket_service.open_ticket(
                guild,
                interaction.user,
                category_name,
                bot=bot,
                source_guild=source_guild,
            )
            if result is None or isinstance(result, str):
                block_msg = result or "You already have an open ticket."
                embed = (
                    message_style.error_embed(block_msg)
                    if block_msg.startswith("Relay is missing the following required permissions:")
                    else message_style.warning_embed(block_msg)
                )
                await interaction.followup.send(
                    embed=embed,
                    ephemeral=True,
                )
            else:
                ticket_id, _ = result
                log.info("Ticket %s created successfully for user %s in category %s", ticket_id, interaction.user.id, category_name)
                await interaction.followup.send(
                    embed=message_style.success_embed(
                        f"Ticket `#{ticket_id}` created! Check your DMs."
                    ),
                    ephemeral=True,
                )
        except discord.Forbidden as e:
            log.error("Forbidden error in CategorySelect callback for user %s: %s", interaction.user.id, e, exc_info=True)
            audit = permission_audit.audit_permissions(
                guild,
                "ticket_workflow",
                context="category_select_forbidden",
            ) if "guild" in locals() and guild else None
            embed = (
                permission_audit.missing_permissions_embed(audit)
                if audit
                else message_style.error_embed("Relay is missing required permissions.")
            )
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        embed=embed,
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        embed=embed,
                        ephemeral=True,
                    )
            except Exception:
                log.error("Failed to send error message to user %s after Forbidden exception", interaction.user.id)
        except discord.HTTPException as e:
            log.error("HTTP error in CategorySelect callback for user %s: %s", interaction.user.id, e, exc_info=True)
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
            log.error("Unexpected error in CategorySelect callback for user %s: %s", interaction.user.id, e, exc_info=True)
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
