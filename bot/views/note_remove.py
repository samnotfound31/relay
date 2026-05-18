"""
Relay Bot — Note Removal View
Dropdown UI for /note remove with permission-aware options.
"""

from __future__ import annotations

import logging

import discord
from discord import ui

from bot.services import note_service, permission_service
from bot.services.permission_service import PREFLIGHT_FAILURE_MESSAGE

log = logging.getLogger(__name__)

MAX_NOTE_LABEL = 60


class NoteRemoveView(ui.View):
    """Dropdown for removing staff notes. Admins see extra delete-all option."""

    def __init__(
        self,
        notes: list[dict],
        actor: discord.Member,
        is_admin: bool,
        user_id: int,
        source_guild_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.actor = actor
        self.is_admin = is_admin
        self.user_id = user_id
        self.source_guild_id = source_guild_id
        self.add_item(_NoteRemoveSelect(notes, is_admin))


class _NoteRemoveSelect(ui.Select):
    def __init__(self, notes: list[dict], is_admin: bool) -> None:
        options = []
        for note in notes:
            note_id = note["id"]
            preview = note["content"][:MAX_NOTE_LABEL]
            label = f"#{note_id}: {preview}"
            if len(label) > 100:
                label = label[:97] + "..."
            desc = f"by {note['author_name']} • {note['created_at'][:16]}"
            options.append(
                discord.SelectOption(label=label, description=desc, value=str(note_id))
            )

        if is_admin:
            options.append(
                discord.SelectOption(
                    label="🗑 Delete ALL notes",
                    description="Remove every note for this user in this community.",
                    value="__DELETE_ALL__",
                )
            )

        super().__init__(
            placeholder="Select a note to remove…",
            options=options[:25],  # Discord limit
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        log.info(
            "[NOTE_REMOVE] User %s (%s) removing note in guild %s",
            interaction.user.id, interaction.user.name, interaction.guild_id
        )
        
        try:
            value = self.values[0]
            view: NoteRemoveView = self.view  # type: ignore

            if value == "__DELETE_ALL__":
                log.info(
                    "[NOTE_REMOVE_DELETE_ALL] User %s deleting all notes for user %s in guild %s",
                    view.actor.id, view.user_id, view.source_guild_id
                )
                count, msg = await note_service.remove_all_notes(
                    view.user_id, view.source_guild_id, view.actor.id, view.is_admin,
                )
                log.info(
                    "[NOTE_REMOVE_DELETE_ALL_SUCCESS] Deleted %d notes for user %s",
                    count, view.user_id
                )
                await interaction.response.edit_message(
                    content=None,
                    embed=discord.Embed(
                        description=msg,
                        color=0x57F287 if count > 0 else 0xFEE75C,
                    ),
                    view=None,
                )
                return

            note_id = int(value)
            log.info(
                "[NOTE_REMOVE_SINGLE] User %s removing note %s for user %s",
                view.actor.id, note_id, view.user_id
            )
            success, msg = await note_service.remove_note(
                note_id, view.actor.id, view.is_admin,
            )
            color = 0x57F287 if success else 0xED4245
            if success:
                log.info(
                    "[NOTE_REMOVE_SUCCESS] Note %s removed successfully",
                    note_id
                )
            else:
                log.warning(
                    "[NOTE_REMOVE_FAILED] Failed to remove note %s: %s",
                    note_id, msg
                )
            await interaction.response.edit_message(
                content=None,
                embed=discord.Embed(description=msg, color=color),
                view=None,
            )
        except discord.Forbidden as e:
            log.error(
                "[PERMISSION_ERROR] Forbidden in NoteRemove callback for user %s: %s",
                interaction.user.id, e, exc_info=True
            )
            embed = discord.Embed(description=PREFLIGHT_FAILURE_MESSAGE, color=0xED4245)
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
                log.error("Failed to send error message after Forbidden exception")
        except Exception as e:
            log.error(
                "[NOTE_REMOVE_ERROR] Unexpected error in NoteRemove callback for user %s: %s",
                interaction.user.id, e, exc_info=True
            )
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            description="An unexpected error occurred. Please try again.",
                            color=0xED4245,
                        ),
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        embed=discord.Embed(
                            description="An unexpected error occurred. Please try again.",
                            color=0xED4245,
                        ),
                        ephemeral=True,
                    )
            except Exception:
                log.error("Failed to send error message after unexpected exception")
