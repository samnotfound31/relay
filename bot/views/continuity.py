"""
Relay Bot — Investigative Continuity Views
Reusable permission-checked views for /info and /history.
"""

from __future__ import annotations

import logging

import discord
from discord import ui

from bot.database import queries
from bot.services import message_style, note_service, permission_service

log = logging.getLogger(__name__)

PAGE_SIZE = 5
NO_ACCESS = "⚠️ You do not have permission to access investigative history."
EXPIRED_VIEW = "⚠️ This continuity panel expired. Please rerun `/info`."


async def _can_access(interaction: discord.Interaction, capability: str) -> bool:
    if not interaction.guild_id or not isinstance(interaction.user, discord.Member):
        return False
    return await permission_service.can_access_investigative_history(
        interaction.user, interaction.guild_id, capability,
    )


def _fmt_ts(value: str | None) -> str:
    if not value:
        return "—"
    return value[:16].replace("T", " ")


def _page(items: list[dict], index: int) -> list[dict]:
    start = index * PAGE_SIZE
    return items[start:start + PAGE_SIZE]


class ContinuityView(ui.View):
    def __init__(self, user_id: int, source_guild_id: int) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.source_guild_id = source_guild_id
        self.add_item(_ContinuityTypeSelect(user_id, source_guild_id))


class _ContinuityTypeSelect(ui.Select):
    def __init__(self, user_id: int, source_guild_id: int) -> None:
        self.user_id = user_id
        self.source_guild_id = source_guild_id
        super().__init__(
            placeholder="Select continuity type...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Notes", value="notes", description="View staff notes for this user"),
                discord.SelectOption(label="Transcripts", value="transcripts", description="Find transcript artifacts"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            selected = self.values[0]
            if selected == "notes":
                await _send_notes_history(interaction, self.user_id, self.source_guild_id)
                return
            await _send_transcript_history(interaction, self.user_id, self.source_guild_id)
        except discord.InteractionResponded:
            log.warning("Dropdown interaction already responded")
            await interaction.followup.send(
                embed=message_style.warning_embed(EXPIRED_VIEW),
                ephemeral=True,
            )
        except discord.NotFound:
            log.warning("Dropdown interaction: message or channel not found")
            await interaction.followup.send(
                embed=message_style.warning_embed(EXPIRED_VIEW),
                ephemeral=True,
            )
        except Exception as e:
            log.warning(f"Dropdown callback failed: {e}")
            try:
                await interaction.followup.send(
                    embed=message_style.warning_embed(EXPIRED_VIEW),
                    ephemeral=True,
                )
            except Exception:
                pass


async def _send_notes_history(
    interaction: discord.Interaction,
    user_id: int,
    source_guild_id: int,
) -> None:
    if not await _can_access(interaction, "notes"):
        try:
            await interaction.response.send_message(NO_ACCESS, ephemeral=True)
        except Exception as e:
            log.warning(f"Failed to send no-access message for notes: {e}")
        return
    notes = await note_service.get_notes(user_id, source_guild_id)
    view = NotesHistoryView(user_id, source_guild_id, notes)
    try:
        if len(notes) > PAGE_SIZE:
            await interaction.response.send_message(embed=view.build_embed(), view=view)
        else:
            await interaction.response.send_message(embed=view.build_embed())
    except discord.InteractionResponded:
        log.warning("Notes history: interaction already responded")
        await interaction.followup.send(embed=view.build_embed())
    except discord.NotFound:
        log.warning("Notes history: message or channel not found")
        await interaction.followup.send(embed=message_style.warning_embed(EXPIRED_VIEW))
    except Exception as e:
        log.warning(f"Failed to send notes history: {e}")


async def _send_transcript_history(
    interaction: discord.Interaction,
    user_id: int,
    source_guild_id: int,
) -> None:
    if not await _can_access(interaction, "transcripts"):
        try:
            await interaction.response.send_message(NO_ACCESS, ephemeral=True)
        except Exception as e:
            log.warning(f"Failed to send no-access message for transcripts: {e}")
        return
    transcripts = await queries.get_transcripts_for_user(user_id, source_guild_id)
    view = TranscriptHistoryView(user_id, source_guild_id, transcripts)
    try:
        if transcripts:
            await interaction.response.send_message(embed=view.build_embed(), view=view)
        else:
            await interaction.response.send_message(embed=view.build_embed())
    except discord.InteractionResponded:
        log.warning("Transcript history: interaction already responded")
        await interaction.followup.send(embed=view.build_embed())
    except discord.NotFound:
        log.warning("Transcript history: message or channel not found")
        await interaction.followup.send(embed=message_style.warning_embed(EXPIRED_VIEW))
    except Exception as e:
        log.warning(f"Failed to send transcript history: {e}")


class NotesHistoryView(ui.View):
    def __init__(self, user_id: int, source_guild_id: int, notes: list[dict]) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.source_guild_id = source_guild_id
        self.notes = notes
        self.page = 0

    def build_embed(self) -> discord.Embed:
        if not self.notes:
            return message_style.warning_embed("No notes for this user in this community.")
        lines = []
        for note in _page(self.notes, self.page):
            ts = _fmt_ts(note.get("created_at"))
            content = note.get("content", "")[:600]
            author_id = note.get("author_id")
            lines.append(
                f"**{note.get('author_name', 'Staff')}**  <@{author_id}>  `(ID {author_id})`  •  {ts}\n"
                f"{content}"
            )
        total_pages = max(1, (len(self.notes) + PAGE_SIZE - 1) // PAGE_SIZE)
        return message_style.relay_embed(
            title=f"📝 Notes History ({len(self.notes)})",
            description="\n\n".join(lines),
            footer=f"Community {self.source_guild_id} • Page {self.page + 1}/{total_pages}",
        )

    @ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await _can_access(interaction, "notes"):
            try:
                await interaction.response.send_message(NO_ACCESS, ephemeral=True)
            except Exception as e:
                log.warning(f"Failed to send no-access message for notes prev: {e}")
            return
        self.page = max(0, self.page - 1)
        try:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except discord.InteractionResponded:
            log.warning("Notes prev: interaction already responded")
            await interaction.followup.send(embed=self.build_embed())
        except discord.NotFound:
            log.warning("Notes prev: message or channel not found")
            await interaction.followup.send(embed=message_style.warning_embed(EXPIRED_VIEW))
        except Exception as e:
            log.warning(f"Failed to edit notes history (prev): {e}")

    @ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await _can_access(interaction, "notes"):
            try:
                await interaction.response.send_message(NO_ACCESS, ephemeral=True)
            except Exception as e:
                log.warning(f"Failed to send no-access message for notes next: {e}")
            return
        max_page = max(0, (len(self.notes) - 1) // PAGE_SIZE)
        self.page = min(max_page, self.page + 1)
        try:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except discord.InteractionResponded:
            log.warning("Notes next: interaction already responded")
            await interaction.followup.send(embed=self.build_embed())
        except discord.NotFound:
            log.warning("Notes next: message or channel not found")
            await interaction.followup.send(embed=message_style.warning_embed(EXPIRED_VIEW))
        except Exception as e:
            log.warning(f"Failed to edit notes history (next): {e}")


class TranscriptHistoryView(ui.View):
    def __init__(self, user_id: int, source_guild_id: int, transcripts: list[dict]) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.source_guild_id = source_guild_id
        self.transcripts = transcripts
        self.page = 0
        self._sync_select()

    def _sync_select(self) -> None:
        for item in list(self.children):
            if isinstance(item, _TranscriptSelect):
                self.remove_item(item)
        linked = [
            tr for tr in _page(self.transcripts, self.page)
            if tr.get("log_channel_id") and tr.get("transcript_message_id") and tr.get("guild_id")
        ]
        if linked:
            self.add_item(_TranscriptSelect(linked))

    def build_embed(self) -> discord.Embed:
        if not self.transcripts:
            return message_style.warning_embed("No transcripts for this user in this community.")
        lines = []
        for tr in _page(self.transcripts, self.page):
            available = "linked" if tr.get("log_channel_id") and tr.get("transcript_message_id") else "legacy/missing"
            closed_by = tr.get("closed_by") or "unknown"
            lines.append(
                f"**Ticket #{tr.get('ticket_id')}**  •  closed {_fmt_ts(tr.get('created_at'))}\n"
                f"Assigned/Closed by: `{closed_by}`  •  Log: **{available}**"
            )
        total_pages = max(1, (len(self.transcripts) + PAGE_SIZE - 1) // PAGE_SIZE)
        return message_style.relay_embed(
            title=f"📄 Transcript History ({len(self.transcripts)})",
            description="\n\n".join(lines),
            footer=f"Community {self.source_guild_id} • Page {self.page + 1}/{total_pages}",
        )

    @ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await _can_access(interaction, "transcripts"):
            try:
                await interaction.response.send_message(NO_ACCESS, ephemeral=True)
            except Exception as e:
                log.warning(f"Failed to send no-access message for transcripts prev: {e}")
            return
        self.page = max(0, self.page - 1)
        self._sync_select()
        try:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except discord.InteractionResponded:
            log.warning("Transcript prev: interaction already responded")
            await interaction.followup.send(embed=self.build_embed())
        except discord.NotFound:
            log.warning("Transcript prev: message or channel not found")
            await interaction.followup.send(embed=message_style.warning_embed(EXPIRED_VIEW))
        except Exception as e:
            log.warning(f"Failed to edit transcript history (prev): {e}")

    @ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await _can_access(interaction, "transcripts"):
            try:
                await interaction.response.send_message(NO_ACCESS, ephemeral=True)
            except Exception as e:
                log.warning(f"Failed to send no-access message for transcripts next: {e}")
            return
        max_page = max(0, (len(self.transcripts) - 1) // PAGE_SIZE)
        self.page = min(max_page, self.page + 1)
        self._sync_select()
        try:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        except discord.InteractionResponded:
            log.warning("Transcript next: interaction already responded")
            await interaction.followup.send(embed=self.build_embed())
        except discord.NotFound:
            log.warning("Transcript next: message or channel not found")
            await interaction.followup.send(embed=message_style.warning_embed(EXPIRED_VIEW))
        except Exception as e:
            log.warning(f"Failed to edit transcript history (next): {e}")


class _TranscriptSelect(ui.Select):
    def __init__(self, transcripts: list[dict]) -> None:
        self.transcripts_by_id = {str(tr["id"]): tr for tr in transcripts}
        options = []
        for tr in transcripts[:25]:
            ticket_number = tr.get("community_ticket_number") or tr.get("ticket_id")
            options.append(
                discord.SelectOption(
                    label=f"Ticket #{ticket_number}",
                    value=str(tr["id"]),
                    description=f"Closed {_fmt_ts(tr.get('created_at'))}",
                )
            )
        super().__init__(
            placeholder="Select transcript...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _can_access(interaction, "transcripts"):
            try:
                await interaction.response.send_message(NO_ACCESS, ephemeral=True)
            except Exception as e:
                log.warning(f"Failed to send no-access message for transcript select: {e}")
            return
        tr = self.transcripts_by_id.get(self.values[0])
        if tr:
            jump_url = (
                f"https://discord.com/channels/{tr['guild_id']}/"
                f"{tr['log_channel_id']}/{tr['transcript_message_id']}"
            )
            try:
                await interaction.response.send_message(
                    content=f"📄 Transcript log for ticket `{tr.get('ticket_id')}`: {jump_url}",
                )
            except discord.InteractionResponded:
                log.warning("Transcript select: interaction already responded")
                await interaction.followup.send(
                    content=f"📄 Transcript log for ticket `{tr.get('ticket_id')}`: {jump_url}",
                )
            except discord.NotFound:
                log.warning("Transcript select: message or channel not found")
                await interaction.followup.send(
                    embed=message_style.warning_embed(EXPIRED_VIEW),
                )
            except Exception as e:
                log.warning(f"Failed to send transcript link: {e}")
            return
        try:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "This transcript log link is no longer available."
                ),
            )
        except discord.InteractionResponded:
            log.warning("Transcript select (not found): interaction already responded")
            await interaction.followup.send(
                embed=message_style.warning_embed(
                    "This transcript log link is no longer available."
                ),
            )
        except Exception as e:
            log.warning(f"Failed to send transcript not found message: {e}")
