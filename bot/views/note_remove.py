"""
Relay Bot — Note Removal View
Dropdown UI for /note remove with permission-aware options.
"""

from __future__ import annotations

import discord
from discord import ui

from bot.services import note_service, permission_service

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
        value = self.values[0]
        view: NoteRemoveView = self.view  # type: ignore

        if value == "__DELETE_ALL__":
            count, msg = await note_service.remove_all_notes(
                view.user_id, view.source_guild_id, view.actor.id, view.is_admin,
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
        success, msg = await note_service.remove_note(
            note_id, view.actor.id, view.is_admin,
        )
        color = 0x57F287 if success else 0xED4245
        await interaction.response.edit_message(
            content=None,
            embed=discord.Embed(description=msg, color=color),
            view=None,
        )
