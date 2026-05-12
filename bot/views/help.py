"""
Relay Bot — Interactive Help Handbook View
Provides a premium, categorized, interactive /help experience.
"""

from __future__ import annotations

import logging

import discord
from discord import ui

from bot.services import message_style
from bot.config import RELAY_COLOR

log = logging.getLogger(__name__)

EXPIRED_MSG = "Help session expired. Please rerun `/help`."

# ── Help Content with Permission Gates ───────────────

# Permission gates: 'admin', 'staff', 'investigative', 'user'
# 'admin' = server administrator
# 'staff' = has any staff role (or admin)
# 'investigative' = has investigative continuity access
# 'user' = no special permissions (default)

HELP_CATEGORIES = [
    # User-facing categories (visible to everyone)
    ("user_tickets", "Opening Tickets", "How to open and use Relay tickets", "user"),
    ("user_relay", "Relay Sessions", "Understanding DM-based support sessions", "user"),
    ("user_leave", "Leaving Support Safely", "When and how to disconnect from sessions", "user"),
    
    # Staff categories (visible to staff roles)
    ("staff_workflow", "Ticket Workflow", "Claim, status, priority, and escalation", "staff"),
    ("staff_dashboard", "Dashboard Operations", "Using the operational dashboard controls", "staff"),
    ("staff_communication", "Staff Communication", "Reply commands and staff emoji system", "staff"),
    ("staff_ownership", "Ownership & Handoffs", "Claiming, transferring, and returning to queue", "staff"),
    ("staff_tips", "Operational Best Practices", "Workflow philosophy and common mistakes", "staff"),
    
    # Investigative continuity (gated by investigative access)
    ("investigative_continuity", "Investigative Continuity", "Context, history, notes, and reminders", "investigative"),
    ("investigative_history", "User History Retrieval", "Accessing ticket patterns and outcomes", "investigative"),
    
    # Admin-only categories
    ("admin_setup", "Server Setup", "Configure Relay for your community", "admin"),
    ("admin_linking", "Support Linking", "Cross-server ticket routing configuration", "admin"),
    ("admin_permissions", "Staff Permissions", "Granular role-based command access", "admin"),
    ("admin_categories", "Categories & Infrastructure", "Ticket category management", "admin"),
    ("admin_transcripts", "Transcripts & Logging", "Automatic transcript generation", "admin"),
]


# ── Permission Check Helpers ───────────────────────────

async def _check_permission(
    interaction: discord.Interaction,
    gate: str,
) -> bool:
    """Check if user has the required permission gate."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    
    if gate == "user":
        return True  # Everyone can see user content
    
    if gate == "admin":
        return interaction.user.guild_permissions.administrator
    
    if gate == "staff":
        from bot.services.permission_service import is_staff
        try:
            return await is_staff(interaction.user, interaction.guild.id)
        except Exception:
            return interaction.user.guild_permissions.administrator
    
    if gate == "investigative":
        from bot.services.permission_service import can_access_investigative_history
        try:
            return await can_access_investigative_history(
                interaction.user, interaction.guild.id, "history"
            )
        except Exception:
            return False
    
    return False


def _home_embed(interaction: discord.Interaction, visible_categories: list[tuple]) -> discord.Embed:
    """Build the main help handbook landing page with permission-aware categories."""
    if not visible_categories:
        desc = (
            "Welcome to the **Relay Operational Handbook**.\n\n"
            "Relay is a cross-server moderation operations platform that bridges "
            "users in community servers with staff in a dedicated support server "
            "through persistent DM-based ticket workflows.\n\n"
            "No help categories are available for your current role."
        )
    else:
        desc = (
            "Welcome to the **Relay Operational Handbook**.\n\n"
            "Relay is a cross-server moderation operations platform that bridges "
            "users in community servers with staff in a dedicated support server "
            "through persistent DM-based ticket workflows.\n"
            "\u200b\n"
            "### How to Use This Handbook\n"
            "Select a category from the dropdown below to learn about "
            "Relay's architecture, commands, and operational workflows.\n"
            "\u200b\n"
            "### Available Categories\n"
        )
        for _, name, short_desc, _gate in visible_categories:
            desc += f"**{name}** — {short_desc}\n"

    embed = discord.Embed(
        title="Relay Operational Handbook",
        description=desc,
        color=RELAY_COLOR,
    )
    embed.set_footer(text="Use the dropdown below to navigate • Relay v5")
    return embed


def _category_embed(category_id: str) -> discord.Embed:
    """Return the detailed embed for a given category."""
    builders = {
        # User-facing
        "user_tickets": _build_user_tickets,
        "user_relay": _build_user_relay,
        "user_leave": _build_user_leave,
        # Staff
        "staff_workflow": _build_staff_workflow,
        "staff_dashboard": _build_staff_dashboard,
        "staff_communication": _build_staff_communication,
        "staff_ownership": _build_staff_ownership,
        "staff_tips": _build_staff_tips,
        # Investigative
        "investigative_continuity": _build_investigative_continuity,
        "investigative_history": _build_investigative_history,
        # Admin
        "admin_setup": _build_admin_setup,
        "admin_linking": _build_admin_linking,
        "admin_permissions": _build_admin_permissions,
        "admin_categories": _build_admin_categories,
        "admin_transcripts": _build_admin_transcripts,
    }
    builder = builders.get(category_id)
    if builder is None:
        return message_style.warning_embed("Unknown category.")
    return builder()


# ── Builder Functions: User-Facing ─────────────────────

def _build_user_tickets() -> discord.Embed:
    desc = (
        "### How to Open Tickets\n"
        "Find the Relay ticket panel in your community server (usually in "
        "a support or rules channel). Click the button to open a DM-based "
        "support ticket.\n"
        "\u200b\n"
        "### What Happens Next\n"
        "1. Relay opens a DM with you to confirm your ticket\n"
        "2. A ticket channel is created in the support server\n"
        "3. Staff are notified and can claim your ticket\n"
        "4. Once claimed, you can communicate directly with your handler\n"
        "\u200b\n"
        "### Your Ticket Channel\n"
        "All messages you send in the DM appear in the ticket channel. "
        "Staff messages appear in your DM. This keeps the conversation "
        "private and organized.\n"
        "\u200b\n"
        "### Ticket Resolution\n"
        "When your issue is resolved, staff will close the ticket. The channel "
        "is deleted, and a transcript is saved for staff records. You can "
        "always open a new ticket if you need help again."
    )
    embed = discord.Embed(title="Opening Tickets", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • User Guide")
    return embed


def _build_user_relay() -> discord.Embed:
    desc = (
        "### What is a Relay Session?\n"
        "When you open a ticket, Relay establishes a persistent DM session with you. "
        "This session bridges your messages to the ticket channel in the support server.\n"
        "\u200b\n"
        "### How Messaging Works\n"
        "- Your DM messages → appear in the ticket channel\n"
        "- Staff messages in ticket channel → appear in your DM\n"
        "- This is real-time and persistent\n"
        "\u200b\n"
        "### Session Privacy\n"
        "Your conversation is visible only to staff handling your ticket. "
        "Other users cannot see your ticket channel. Your DM with Relay "
        "is private to you.\n"
        "\u200b\n"
        "### Session Continuity\n"
        "The session remains active until your ticket is closed or you "
        "explicitly disconnect. Staff can see your message history "
        "within the session for context."
    )
    embed = discord.Embed(title="Relay Sessions", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • User Guide")
    return embed


def _build_user_leave() -> discord.Embed:
    desc = (
        "### Disconnecting from Support\n"
        "You can disconnect from your Relay session at any time using the "
        "**Leave** button in your DM with Relay. This is a destructive action.\n"
        "\u200b\n"
        "### What Happens When You Leave\n"
        "- You will stop receiving messages from staff\n"
        "- Staff will stop receiving your messages\n"
        "- The ticket remains open, but you cannot communicate\n"
        "\u200b\n"
        "### When to Use Leave\n"
        "Use Leave only if you are intentionally abandoning support, "
        "such as when your issue is resolved and you don't want further contact.\n"
        "\u200b\n"
        "### Warning\n"
        "If you disconnect accidentally, contact staff in the community server. "
        "They may be able to restore your session. Do not use Leave as a way "
        "to pause communication — your ticket will still be open and staff may "
        "attempt to reach you."
    )
    embed = discord.Embed(title="Leaving Support Safely", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • User Guide")
    return embed


# ── Builder Functions: Staff ───────────────────────────

def _build_staff_workflow() -> discord.Embed:
    desc = (
        "### Status Workflow\n"
        "**Open** — Unclaimed, waiting in queue. Setting status to Open "
        "releases ownership and returns ticket to queue.\n\n"
        "**Investigating** — Actively being worked. Set this when you're "
        "actively investigating or resolving the issue.\n\n"
        "**Waiting User** — You're waiting for the user to respond. Use the "
        "Remind button to get pinged when they reply.\n\n"
        "**Escalated** — Requires higher-level intervention. Add a context note "
        "explaining what needs attention.\n\n"
        "**Closed** — Ticket resolved and archived.\n"
        "\u200b\n"
        "### Priority Levels\n"
        "**Low** — Routine inquiries, non-urgent issues.\n"
        "**Medium** — Standard priority for most tickets.\n"
        "**High** — Urgent issues requiring immediate attention.\n"
        "\u200b\n"
        "### Escalation Philosophy\n"
        "Escalate early when a ticket is outside your scope or expertise. "
        "Don't sit on tickets you cannot resolve — pass them to someone "
        "who can."
    )
    embed = discord.Embed(title="Ticket Workflow", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Staff Operations")
    return embed


def _build_staff_dashboard() -> discord.Embed:
    desc = (
        "### Dashboard Lifecycle\n"
        "Every ticket channel has an operational dashboard — a persistent message "
        "with buttons for quick workflow actions.\n"
        "\u200b\n"
        "### Two-Phase Lifecycle\n"
        "**Pre-claim (Queue State):** Only the Claim button is visible. "
        "The dashboard is lightweight and minimal.\n\n"
        "**Post-claim (Operational State):** Full operational controls appear. "
        "The dashboard is locked to the handler — only the owner (or admins) "
        "can use dashboard controls.\n"
        "\u200b\n"
        "### Dashboard Layout\n"
        "**Row 1:** Claim button (shows claimed state with handler name)\n"
        "**Row 2:** Change Status / Change Priority\n"
        "**Row 3:** Remind / Context / Info / History / Add Note\n"
        "**Row 4:** Move / Close\n"
        "\u200b\n"
        "### Ownership Lock\n"
        "Once claimed, the dashboard is locked to you. Other staff cannot "
        "accidentally change status or priority. Admins can override this lock.\n"
        "\u200b\n"
        "### Returning to Queue\n"
        "Setting status to Open releases your claim and returns the ticket to "
        "the queue. The dashboard collapses back to the lightweight Claim-only state."
    )
    embed = discord.Embed(title="Dashboard Operations", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Staff Operations")
    return embed


def _build_staff_communication() -> discord.Embed:
    desc = (
        "### Reply Commands\n"
        "**`/reply`** — Send a visible reply to the user. Your message appears "
        "in their DM with your staff emoji prefix.\n\n"
        "**`/anreply`** — Send an anonymous reply. The message appears without "
        "identifying which staff member sent it.\n"
        "\u200b\n"
        "### Staff Emojis\n"
        "**`/emoji`** — Set your personal staff emoji. This emoji prefixes all "
        "your relayed messages, helping users distinguish between staff members.\n\n"
        "**`/emojilist`** — Post or update the staff emoji registry in a channel. "
        "Use this to show users which emoji belongs to which staff member.\n"
        "\u200b\n"
        "### Communication Philosophy\n"
        "Be clear, professional, and empathetic. Your messages appear in "
        "the user's DM — they see everything you type. Use context notes "
        "to communicate important information to other staff without "
        "messaging the user directly."
    )
    embed = discord.Embed(title="Staff Communication", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Staff Operations")
    return embed


def _build_staff_ownership() -> discord.Embed:
    desc = (
        "### Claiming Tickets\n"
        "**`/claim`** — Take ownership of an unclaimed ticket. This locks the "
        "dashboard to you. Only you (or admins) can use dashboard controls.\n"
        "\u200b\n"
        "### Transferring Ownership\n"
        "**`/transfer`** — Transfer ticket ownership to another staff member. "
        "Useful for handoffs or when you're going offline. The new handler "
        "gains full dashboard control.\n"
        "\u200b\n"
        "### Returning to Queue\n"
        "Setting status to **Open** releases your claim and returns the ticket "
        "to the queue for another handler to pick up. Use this when you can no "
        "longer handle the ticket.\n"
        "\u200b\n"
        "### Ownership Best Practices\n"
        "- Claim before responding — ownership prevents conflicts\n"
        "- Transfer before going offline — don't leave tickets orphaned\n"
        "- Return to queue when overloaded — let others handle what you can't\n"
        "- Don't claim tickets you don't intend to handle immediately"
    )
    embed = discord.Embed(title="Ownership & Handoffs", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Staff Operations")
    return embed


def _build_staff_tips() -> discord.Embed:
    desc = (
        "### Workflow Philosophy\n"
        "Relay is designed for **intentional, persistent moderation operations**. "
        "Every action should be deliberate — claim tickets you intend to handle, "
        "set statuses that reflect reality, and close when truly resolved.\n"
        "\u200b\n"
        "### Best Practices\n"
        "**1.** Claim before responding — ownership prevents conflicts\n"
        "**2.** Use context notes — future handlers will thank you\n"
        "**3.** Set Waiting User when waiting — signals ticket state clearly\n"
        "**4.** Check history first — returning users have patterns\n"
        "**5.** Escalate early — don't sit on tickets outside your scope\n"
        "**6.** Use Remind — track tickets waiting for user response\n"
        "**7.** Return to queue when overloaded — release tickets for others\n"
        "\u200b\n"
        "### Common Mistakes\n"
        "- Forgetting to claim before responding\n"
        "- Leaving tickets in Investigating when actually waiting for user\n"
        "- Not adding context for escalated tickets\n"
        "- Closing without checking if user is satisfied\n"
        "- Not checking history for repeat users\n"
        "\u200b\n"
        "### Operational Mindset\n"
        "Relay is not just a ticket bot — it's a persistence-first moderation "
        "operations platform. Every action you take builds institutional memory."
    )
    embed = discord.Embed(title="Operational Best Practices", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Staff Operations")
    return embed


# ── Builder Functions: Investigative ─────────────────────

def _build_investigative_continuity() -> discord.Embed:
    desc = (
        "### Context Notes\n"
        "**`/context`** — Set a brief investigation summary on the ticket. "
        "This helps other staff understand the current state at a glance, "
        "especially during escalations or handoffs. Think of it as a sticky note.\n"
        "\u200b\n"
        "### User Intelligence\n"
        "**`/info`** — Show investigative context for the ticket user. "
        "Displays total tickets opened, current ticket status, staff notes, "
        "and creation timeline.\n"
        "\u200b\n"
        "### Staff Notes\n"
        "**`/note add`** — Attach a persistent note to the user's profile "
        "(scoped to your community). Notes survive across tickets and help "
        "build institutional memory.\n\n"
        "**`/note remove`** — Remove a staff note from the user's profile.\n"
        "\u200b\n"
        "### Reminders\n"
        "**`/remind`** — Get notified when the ticket creator replies. "
        "Use this when waiting for user response to avoid constantly watching.\n"
        "\u200b\n"
        "### Privacy Scope\n"
        "Continuity data (history, notes) is scoped per community. A user's "
        "history in Community A is not visible to staff in Community B."
    )
    embed = discord.Embed(title="Investigative Continuity", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Investigative Tools")
    return embed


def _build_investigative_history() -> discord.Embed:
    desc = (
        "### User History Retrieval\n"
        "**`/history`** — Open focused continuity retrieval for the ticket user. "
        "Browse previous tickets with timestamps and outcomes. This helps "
        "identify patterns and provide informed support.\n"
        "\u200b\n"
        "### What History Shows\n"
        "- Previous ticket timestamps and durations\n"
        "- Ticket outcomes (resolved, escalated, etc.)\n"
        "- Staff who handled previous tickets\n"
        "- Categories and priority levels\n"
        "\u200b\n"
        "### Using History Effectively\n"
        "- Check if this is a returning user with known patterns\n"
        "- Identify chronic issues that need escalation\n"
        "- Understand user history before responding\n"
        "- Build institutional memory across tickets\n"
        "\u200b\n"
        "### Transcript Access\n"
        "Use the **History** button on the dashboard to access transcripts "
        "for specific previous tickets. This helps understand full context "
        "of past interactions."
    )
    embed = discord.Embed(title="User History Retrieval", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Investigative Tools")
    return embed


# ── Builder Functions: Admin ─────────────────────────────

def _build_admin_setup() -> discord.Embed:
    desc = (
        "### Setup Commands\n"
        "**`/announce`** — Deploy the ticket panel in a public channel. "
        "Users click the panel button to open a DM-based ticket.\n\n"
        "**`/staffroles`** — Designate which roles can see and handle tickets. "
        "These roles receive access to all ticket channels.\n\n"
        "**`/logchannel`** — Set the channel where closed ticket transcripts are posted.\n"
        "\u200b\n"
        "### Setup Order\n"
        "1. `/staffroles` — Configure who can handle tickets\n"
        "2. `/category add` — Create ticket categories\n"
        "3. `/logchannel` — Set transcript destination (optional)\n"
        "4. `/announce` — Deploy the ticket panel\n"
        "\u200b\n"
        "### Operational Notes\n"
        "Relay automatically creates Discord category channels with proper "
        "permissions when you add ticket categories. Staff roles receive "
        "automatic access to all ticket channels."
    )
    embed = discord.Embed(title="Server Setup", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Admin Configuration")
    return embed


def _build_admin_linking() -> discord.Embed:
    desc = (
        "### Cross-Server Linking\n"
        "**`/linksupport`** — Link a community server to a support server. "
        "Users in the community can open tickets that route to the support server.\n\n"
        "**`/unlinksupport`** — Remove the support link. Tickets must be closed first.\n"
        "\u200b\n"
        "### Linking Requirements\n"
        "- You must be admin in both servers\n"
        "- Relay must be in both servers\n"
        "- The community server must not already be linked\n"
        "- You cannot create circular links (A→B→A)\n"
        "\u200b\n"
        "### Guild Modes\n"
        "**LOCAL** — Standalone server, no cross-server routing\n"
        "**SOURCE** — Community server that routes tickets to a support server\n"
        "**SUPPORT** — Support server that receives tickets from community servers\n"
        "\u200b\n"
        "### Operational Notes\n"
        "SOURCE guilds are restricted — only `/announce` and `/unlinksupport` "
        "are available. All operational commands run in LOCAL or SUPPORT guilds."
    )
    embed = discord.Embed(title="Support Linking", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Admin Configuration")
    return embed


def _build_admin_permissions() -> discord.Embed:
    desc = (
        "### Granular Staff Permissions\n"
        "**`/staffperms`** — Manage granular role-based command access. "
        "Control which staff roles can use specific operational commands.\n"
        "\u200b\n"
        "### Capability Categories\n"
        "**Communication:** reply, anreply, leave\n"
        "**Workflow:** claim, transfer, context, status, priority, move, close, remind\n"
        "**Continuity:** info, history, note_add, note_remove\n"
        "**Dashboard:** dashboard\n"
        "\u200b\n"
        "### Permission Model\n"
        "- Server administrators bypass all restrictions\n"
        "- Staff roles must be configured via `/staffroles` first\n"
        "- Permissions are denylist-based (default allow)\n"
        "- If any of a user's staff roles denies a capability, access is denied\n"
        "\u200b\n"
        "### Operational Notes\n"
        "Use `/staffperms` without arguments to see an overview of all role "
        "permission profiles. Use with a specific role to edit granular access."
    )
    embed = discord.Embed(title="Staff Permissions", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Admin Configuration")
    return embed


def _build_admin_categories() -> discord.Embed:
    desc = (
        "### Category Management\n"
        "**`/category add`** — Create ticket categories with emojis. "
        "Relay automatically creates a corresponding Discord category channel "
        "with proper permissions.\n\n"
        "**`/category remove`** — Remove a ticket category and its Discord channel. "
        "If the category is empty, the Discord channel is deleted automatically.\n"
        "\u200b\n"
        "### Category Permissions\n"
        "Relay automatically configures permissions on Discord category channels:\n"
        "- @everyone: view_channel=false\n"
        "- Staff roles: view_channel=true, send_messages=true, read_history=true\n"
        "- Bot: full permissions for ticket operations\n"
        "\u200b\n"
        "### Operational Notes\n"
        "When you add or remove staff roles via `/staffroles`, Relay automatically "
        "updates permissions on all existing ticket categories. You don't need "
        "to manually manage Discord permissions."
    )
    embed = discord.Embed(title="Categories & Infrastructure", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Admin Configuration")
    return embed


def _build_admin_transcripts() -> discord.Embed:
    desc = (
        "### Automatic Transcripts\n"
        "When a ticket is closed, Relay automatically generates a full conversation "
        "transcript and posts it to your configured log channel. This captures all "
        "messages, status changes, and workflow events.\n"
        "\u200b\n"
        "### Configuring Transcripts\n"
        "**`/logchannel`** — Set or remove the transcript log channel. "
        "Only staff with appropriate permissions can access transcripts.\n"
        "\u200b\n"
        "### What's Included\n"
        "- All user and staff messages (with timestamps)\n"
        "- Status changes and workflow events\n"
        "- Handler assignments and transfers\n"
        "- Closure reason and duration\n"
        "- Investigation context notes\n"
        "\u200b\n"
        "### Transcript Access\n"
        "Transcripts are internal staff records. They are posted to the log channel "
        "and can be downloaded for archival. Staff can view transcripts for a specific "
        "user using the **History** button on the dashboard.\n"
        "\u200b\n"
        "### Privacy\n"
        "Transcripts are not shared with users unless your server explicitly provides "
        "access. They are staff-only records for operational continuity."
    )
    embed = discord.Embed(title="Transcripts & Logging", description=desc, color=RELAY_COLOR)
    embed.set_footer(text="Relay Handbook • Admin Configuration")
    return embed


# ── Help View ────────────────────────────────────────

class HelpCategorySelect(ui.Select):
    """Dropdown to navigate between permission-aware help categories."""

    def __init__(self, visible_categories: list[tuple]) -> None:
        options = [
            discord.SelectOption(
                label=name,
                value=cat_id,
                description=desc[:100],
            )
            for cat_id, name, desc, _gate in visible_categories
        ]
        super().__init__(
            placeholder="Select a category…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        category_id = self.values[0]
        embed = _category_embed(category_id)
        try:
            await interaction.response.edit_message(embed=embed, view=self.view)
        except discord.NotFound:
            await interaction.response.send_message(
                embed=message_style.warning_embed(EXPIRED_MSG),
                ephemeral=True,
            )
        except Exception as e:
            log.warning("Help category callback failed: %s", e)


class HelpHomeButton(ui.Button):
    """Button to return to the help home page."""

    def __init__(self, interaction: discord.Interaction, visible_categories: list[tuple]) -> None:
        self._interaction = interaction
        self._visible_categories = visible_categories
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Home",
            emoji="🏠",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = _home_embed(self._interaction, self._visible_categories)
        try:
            await interaction.response.edit_message(embed=embed, view=self.view)
        except discord.NotFound:
            await interaction.response.send_message(
                embed=message_style.warning_embed(EXPIRED_MSG),
                ephemeral=True,
            )
        except Exception as e:
            log.warning("Help home callback failed: %s", e)


class HelpView(ui.View):
    """Interactive help handbook view with permission-aware categories."""

    def __init__(self, interaction: discord.Interaction, visible_categories: list[tuple]) -> None:
        super().__init__(timeout=300)  # 5 minute timeout for help sessions
        self._interaction = interaction
        self._visible_categories = visible_categories
        self.add_item(HelpCategorySelect(visible_categories))
        self.add_item(HelpHomeButton(interaction, visible_categories))

    async def on_timeout(self) -> None:
        """Disable components when the help session expires."""
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: ui.Item,
    ) -> None:
        log.warning("Help view interaction error: %s", error)
        try:
            await interaction.response.send_message(
                embed=message_style.warning_embed(EXPIRED_MSG),
                ephemeral=True,
            )
        except Exception:
            pass
