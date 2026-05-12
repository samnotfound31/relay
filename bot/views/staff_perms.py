"""
Relay Bot — Staff Permissions View
Interactive UI for managing granular staff role permissions.
Phase 6: granular command-level permissions for staff roles.
"""

from __future__ import annotations

import logging

import discord
from discord import ui

from bot.config import RELAY_COLOR
from bot.database import queries
from bot.services import message_style

log = logging.getLogger(__name__)

# ── Capabilities (Commands/Controls) ─────────────────────

RELAY_CAPABILITIES = {
    # Communication
    "reply": "Send visible replies to users",
    "anreply": "Send anonymous replies to users",
    "leave": "Disconnect relay sessions (user command)",
    
    # Workflow
    "claim": "Claim ticket ownership",
    "transfer": "Transfer ticket ownership",
    "context": "Update ticket investigation context",
    "status": "Change ticket workflow status",
    "priority": "Change ticket priority",
    "move": "Move tickets between categories",
    "close": "Close tickets",
    "remind": "Set response reminders",
    
    # Continuity
    "info": "View user investigative context",
    "history": "View user ticket history",
    "note_add": "Add staff notes to users",
    "note_remove": "Remove staff notes from users",
    
    # Dashboard/System
    "dashboard": "Access operational dashboard controls",
}


class StaffPermsView(ui.View):
    """Interactive permission editor for a specific role."""

    def __init__(self, role_id: int, guild_id: int, guild: discord.Guild):
        super().__init__(timeout=300)
        self.role_id = role_id
        self.guild_id = guild_id
        self.guild = guild
        self.denied_capabilities: set[str] = set()

    async def on_timeout(self) -> None:
        """Disable components when view expires."""
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore

    async def load_denied_capabilities(self) -> None:
        """Load current denied capabilities for this role."""
        self.denied_capabilities = set(await queries.get_role_denied_capabilities(self.guild_id, self.role_id))

    def refresh_buttons(self) -> None:
        """Update button states based on current denials."""
        for item in self.children:
            if isinstance(item, _CapabilityToggle):
                item.update_state(item.capability in self.denied_capabilities)


class _CapabilityToggle(ui.Button):
    """Toggle button for a specific capability."""

    def __init__(self, capability: str, view: StaffPermsView):
        self.capability = capability
        self.view_ref = view
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=f"✓ {capability}",
            row=0,
        )
        self.update_state(False)

    def update_state(self, is_denied: bool) -> None:
        """Update button style based on denial state."""
        if is_denied:
            self.style = discord.ButtonStyle.danger
            self.label = f"✗ {self.capability}"
        else:
            self.style = discord.ButtonStyle.success
            self.label = f"✓ {self.capability}"

    async def callback(self, interaction: discord.Interaction) -> None:
        # Toggle denial state
        if self.capability in self.view_ref.denied_capabilities:
            # Allow capability
            await queries.allow_capability_for_role(
                self.view_ref.guild_id,
                self.view_ref.role_id,
                self.capability,
            )
            self.view_ref.denied_capabilities.remove(self.capability)
            self.update_state(False)
        else:
            # Deny capability
            await queries.deny_capability_for_role(
                self.view_ref.guild_id,
                self.view_ref.role_id,
                self.capability,
            )
            self.view_ref.denied_capabilities.add(self.capability)
            self.update_state(True)

        await interaction.response.edit_message(view=self.view_ref)
