"""
Relay Bot — Setup Cog
Commands: /announce, /category add, /category remove, /staffroles,
          /linksupport, /unlinksupport, /staffperms
Phase 3: cross-server guild linking.
Phase 6: granular staff permissions.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.config import RELAY_COLOR
from bot.database import queries
from bot.services import guild_mode, message_style
from bot.services.permission_service import build_category_overwrites, PREFLIGHT_FAILURE_MESSAGE
from bot.views.ticket_panel import TicketPanelView
from bot.views.staff_perms import RELAY_CAPABILITIES, StaffPermsView, _CapabilityToggle

log = logging.getLogger(__name__)


class Setup(commands.Cog):
    """Server setup commands for Relay."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /announce ─────────────────────────────────────

    @app_commands.command(
        name="announce",
        description="Post the Relay ticket panel in a channel.",
    )
    @app_commands.describe(
        channel="Channel to post the panel in",
        banner="Custom banner image URL (optional)",
        colour="Embed color in hex format (e.g., #5865F2 or 5865F2, optional)",
    )
    @app_commands.default_permissions(administrator=True)
    async def announce(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        banner: str | None = None,
        colour: str | None = None,
    ) -> None:
        log.info(
            "[SETUP_ANNOUNCE] User %s (%s) posting panel in channel %s of guild %s",
            interaction.user.id, interaction.user.name, channel.id, interaction.guild_id
        )
        
        guild = interaction.guild
        if guild is None:
            log.warning("[SETUP_ANNOUNCE] Guild is None")
            return

        await interaction.response.defer(ephemeral=True)
        if await guild_mode.require_not_support_deferred(interaction):
            return

        # Parse and validate color if provided
        embed_color = None
        if colour:
            try:
                # Remove # prefix if present
                hex_str = colour.lstrip('#')
                # Validate hex format (6 characters)
                if len(hex_str) != 6 or not all(c in '0123456789ABCDEFabcdef' for c in hex_str):
                    raise ValueError("Invalid hex format")
                # Convert to integer
                embed_color = int(hex_str, 16)
            except ValueError:
                log.warning(
                    "[SETUP_ANNOUNCE] Invalid color format: %s",
                    colour
                )
                await interaction.followup.send(
                    embed=message_style.warning_embed(
                        "Invalid color format.\n"
                        "Use a valid hex color such as: #5865F2 or 5865F2"
                    ),
                    ephemeral=True,
                )
                return

        # Fetch categories so the panel displays them dynamically
        categories = await queries.get_categories(guild.id)
        log.debug(
            "[SETUP_ANNOUNCE] Fetched %d categories for panel",
            len(categories) if categories else 0
        )

        embed = message_style.ticket_panel_embed(
            guild.name,
            banner_url=banner,
            categories=categories or None,
            color=embed_color,
        )
        view = TicketPanelView()
        
        try:
            msg = await channel.send(embed=embed, view=view)
            log.info(
                "[SETUP_ANNOUNCE] Panel posted in channel %s (message %s)",
                channel.id, msg.id
            )
        except discord.Forbidden as e:
            log.error(
                "[PERMISSION_ERROR] Cannot send panel to channel %s: %s",
                channel.id, e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.error_embed(PREFLIGHT_FAILURE_MESSAGE),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            log.error(
                "[SETUP_ANNOUNCE] HTTP error posting panel: %s",
                e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Failed to post panel due to a Discord API error."
                ),
                ephemeral=True,
            )
            return
        except Exception as e:
            log.error(
                "[SETUP_ANNOUNCE] Unexpected error posting panel: %s",
                e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "An unexpected error occurred while posting the panel."
                ),
                ephemeral=True,
            )
            return

        await queries.upsert_guild_settings(
            guild.id,
            announce_channel_id=channel.id,
            announce_message_id=msg.id,
            banner_url=banner,
        )
        log.info(
            "[SETUP_ANNOUNCE] Guild settings updated for panel in channel %s",
            channel.id
        )

        await interaction.followup.send(
            embed=message_style.success_embed(
                f"Ticket panel posted in {channel.mention}."
            ),
            ephemeral=True,
        )

    # ── /category ─────────────────────────────────────

    category_group = app_commands.Group(
        name="category",
        description="Manage ticket categories.",
        default_permissions=discord.Permissions(administrator=True),
    )

    @category_group.command(
        name="add",
        description="Add a ticket category (also creates a Discord category).",
    )
    @app_commands.describe(
        name="Category name",
        description="Short description of this category",
        emoji="Emoji for this category",
    )
    async def category_add(
        self,
        interaction: discord.Interaction,
        name: str,
        description: str,
        emoji: str = "📂",
    ) -> None:
        log.info(
            "[SETUP_CATEGORY_ADD] User %s (%s) adding category %s in guild %s",
            interaction.user.id, interaction.user.name, name, interaction.guild_id
        )
        
        guild = interaction.guild
        if guild is None:
            log.warning("[SETUP_CATEGORY_ADD] Guild is None")
            return

        await interaction.response.defer(ephemeral=True)
        if await guild_mode.require_not_source_deferred(interaction) or await guild_mode.require_not_support_deferred(interaction):
            log.warning(
                "[SETUP_CATEGORY_ADD] Guild mode check failed for guild %s",
                guild.id
            )
            return

        # Note: permission_audit module not yet implemented, skipping audit
        # Category creation will proceed and Discord will return permission errors if needed
        log.debug("[SETUP_CATEGORY_ADD] Skipping permission audit (module not implemented)")

        # Create Discord channel category with proper permissions
        try:
            overwrites = await build_category_overwrites(guild)
        except Exception as e:
            log.error(
                "[SETUP_CATEGORY_ADD] Failed to build category overwrites: %s",
                e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.error_embed(PREFLIGHT_FAILURE_MESSAGE),
                ephemeral=True,
            )
            return

        display_name = f"{emoji} {name}"
        try:
            discord_category = await guild.create_category(
                display_name, overwrites=overwrites,
            )
            log.info(
                "[SETUP_CATEGORY_ADD] Created Discord category %s (%s)",
                display_name, discord_category.id
            )
        except discord.Forbidden as e:
            log.error(
                "[PERMISSION_ERROR] Cannot create category in guild %s: %s",
                guild.id, e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.error_embed(PREFLIGHT_FAILURE_MESSAGE),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            log.error(
                "[SETUP_CATEGORY_ADD] HTTP error creating category: %s",
                e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Failed to create category due to a Discord API error."
                ),
                ephemeral=True,
            )
            return
        except Exception as e:
            log.error(
                "[SETUP_CATEGORY_ADD] Unexpected error creating category: %s",
                e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "An unexpected error occurred while creating the category."
                ),
                ephemeral=True,
            )
            return

        # Store in DB with discord_category_id
        success = await queries.add_category(
            guild.id, name, description, emoji,
            discord_category_id=discord_category.id,
        )
        if success:
            log.info(
                "[SETUP_CATEGORY_ADD] Category %s stored in DB successfully",
                name
            )
            await interaction.followup.send(
                embed=message_style.success_embed(
                    f"Category **{emoji} {name}** created.\n"
                    f"Discord category: {display_name}"
                ),
                ephemeral=True,
            )
        else:
            # DB insert failed (duplicate) — clean up the Discord category
            log.warning(
                "[SETUP_CATEGORY_ADD] Category %s already exists in DB, cleaning up Discord category",
                name
            )
            try:
                await discord_category.delete()
            except Exception as e:
                log.warning(
                    "[SETUP_CATEGORY_ADD] Failed to clean up Discord category %s: %s",
                    discord_category.id, e
                )
            await interaction.followup.send(
                embed=message_style.error_embed(
                    f"Category **{name}** already exists."
                ),
                ephemeral=True,
            )

    @category_group.command(
        name="remove",
        description="Remove a ticket category.",
    )
    @app_commands.describe(name="Category name to remove")
    async def category_remove(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        log.info(
            "[SETUP_CATEGORY_REMOVE] User %s (%s) removing category %s in guild %s",
            interaction.user.id, interaction.user.name, name, interaction.guild_id
        )
        
        guild = interaction.guild
        if guild is None:
            log.warning("[SETUP_CATEGORY_REMOVE] Guild is None")
            return

        if await guild_mode.require_not_source(interaction) or await guild_mode.require_not_support(interaction):
            log.warning(
                "[SETUP_CATEGORY_REMOVE] Guild mode check failed for guild %s",
                guild.id
            )
            return

        # Get the category data before removing (to clean up Discord category)
        cat_data = await queries.get_category_by_name(guild.id, name)

        removed = await queries.remove_category(guild.id, name)
        if removed:
            log.info(
                "[SETUP_CATEGORY_REMOVE] Category %s removed from DB",
                name
            )
            # Optionally remove the Discord category if empty
            if cat_data and cat_data.get("discord_category_id"):
                dc_cat = guild.get_channel(cat_data["discord_category_id"])
                if dc_cat and not dc_cat.channels:  # type: ignore
                    try:
                        await dc_cat.delete(reason=f"Category '{name}' removed")
                        log.info(
                            "[SETUP_CATEGORY_REMOVE] Deleted Discord category %s",
                            dc_cat.id
                        )
                    except discord.Forbidden as e:
                        log.warning(
                            "[PERMISSION_ERROR] Cannot delete Discord category %s: %s",
                            dc_cat.id, e, exc_info=True
                        )
                        await interaction.response.send_message(
                            embed=message_style.error_embed(PREFLIGHT_FAILURE_MESSAGE),
                            ephemeral=True,
                        )
                        return
                    except Exception as e:
                        log.warning(
                            "[SETUP_CATEGORY_REMOVE] Failed to delete Discord category %s: %s",
                            dc_cat.id, e
                        )

            await interaction.response.send_message(
                embed=message_style.success_embed(
                    f"Category **{name}** removed."
                ),
                ephemeral=True,
            )
        else:
            log.warning(
                "[SETUP_CATEGORY_REMOVE] Category %s not found in DB",
                name
            )
            await interaction.response.send_message(
                embed=message_style.error_embed(
                    f"Category **{name}** not found."
                ),
                ephemeral=True,
            )

    # ── /staffroles ───────────────────────────────────

    @app_commands.command(
        name="staffroles",
        description="Add or remove a support role that can see tickets.",
    )
    @app_commands.describe(
        action="Add or remove a role",
        role="The role to configure",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Remove", value="remove"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def staffroles(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        role: discord.Role,
    ) -> None:
        log.info(
            "[SETUP_STAFFROLES] User %s (%s) %s role %s in guild %s",
            interaction.user.id, interaction.user.name, action.value, role.name, interaction.guild_id
        )
        
        guild = interaction.guild
        if guild is None:
            log.warning("[SETUP_STAFFROLES] Guild is None")
            return

        await interaction.response.defer(ephemeral=True)
        if await guild_mode.require_not_source_deferred(interaction) or await guild_mode.require_not_support_deferred(interaction):
            log.warning(
                "[SETUP_STAFFROLES] Guild mode check failed for guild %s",
                guild.id
            )
            return

        if action.value == "add":
            success = await queries.add_support_role(guild.id, role.id)
            if not success:
                log.info(
                    "[SETUP_STAFFROLES] Role %s already a support role",
                    role.id
                )
                await interaction.followup.send(
                    embed=message_style.warning_embed(
                        f"{role.mention} is already a support role."
                    ),
                    ephemeral=True,
                )
                return
            log.info(
                "[SETUP_STAFFROLES] Added role %s as support role",
                role.id
            )
            result_msg = f"{role.mention} added as a support role."
        else:
            removed = await queries.remove_support_role(guild.id, role.id)
            if not removed:
                log.warning(
                    "[SETUP_STAFFROLES] Role %s is not a support role",
                    role.id
                )
                await interaction.followup.send(
                    embed=message_style.error_embed(
                        f"{role.mention} is not a support role."
                    ),
                    ephemeral=True,
                )
                return
            log.info(
                "[SETUP_STAFFROLES] Removed role %s from support roles",
                role.id
            )
            result_msg = f"{role.mention} removed from support roles."

        # ── Propagate to ALL existing Relay Discord categories ──
        categories = await queries.get_categories(guild.id)
        try:
            new_overwrites = await build_category_overwrites(guild)
        except Exception as e:
            log.error(
                "[SETUP_STAFFROLES] Failed to build category overwrites: %s",
                e, exc_info=True
            )
            await interaction.followup.send(
                embed=message_style.warning_embed(
                    result_msg + "\n\nWarning: Failed to update category permissions."
                ),
                ephemeral=True,
            )
            return
        
        updated = 0

        for cat in categories:
            dc_id = cat.get("discord_category_id")
            if not dc_id:
                continue
            dc_cat = guild.get_channel(dc_id)
            if dc_cat is None:
                log.warning(
                    "[SETUP_STAFFROLES] Cannot find Discord category %s",
                    dc_id
                )
                continue
            try:
                await dc_cat.edit(overwrites=new_overwrites)
                updated += 1
                log.debug(
                    "[SETUP_STAFFROLES] Updated permissions for category %s",
                    dc_id
                )
            except discord.Forbidden as e:
                log.error(
                    "[PERMISSION_ERROR] Cannot update category %s permissions: %s",
                    dc_id, e, exc_info=True
                )
            except discord.HTTPException as e:
                log.error(
                    "[SETUP_STAFFROLES] HTTP error updating category %s: %s",
                    dc_id, e
                )
            except Exception as e:
                log.error(
                    "[SETUP_STAFFROLES] Unexpected error updating category %s: %s",
                    dc_id, e, exc_info=True
                )

        # Also update the fallback category if it exists
        settings = await queries.get_guild_settings(guild.id)
        if settings and settings.get("ticket_category_id"):
            fallback = guild.get_channel(settings["ticket_category_id"])
            if fallback:
                try:
                    await fallback.edit(overwrites=new_overwrites)
                    updated += 1
                    log.debug(
                        "[SETUP_STAFFROLES] Updated fallback category %s",
                        settings["ticket_category_id"]
                    )
                except discord.Forbidden as e:
                    log.error(
                        "[PERMISSION_ERROR] Cannot update fallback category permissions: %s",
                        e, exc_info=True
                    )
                except Exception as e:
                    log.error(
                        "[SETUP_STAFFROLES] Error updating fallback category: %s",
                        e, exc_info=True
                    )

        suffix = f"\nUpdated {updated} existing category permissions." if updated else ""
        log.info(
            "[SETUP_STAFFROLES] Completed %s for role %s, updated %d categories",
            action.value, role.id, updated
        )
        await interaction.followup.send(
            embed=message_style.success_embed(result_msg + suffix),
            ephemeral=True,
        )

    # ── /linksupport ─────────────────────────────────

    @app_commands.command(
        name="linksupport",
        description="Link this guild to a support guild for cross-server tickets.",
    )
    @app_commands.describe(
        support_guild_id="The ID of the support guild to route tickets to",
    )
    @app_commands.default_permissions(administrator=True)
    async def linksupport(
        self,
        interaction: discord.Interaction,
        support_guild_id: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True)

        mode = await guild_mode.resolve_guild_mode(guild.id)
        if mode != guild_mode.GuildMode.LOCAL:
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Only local guilds can be linked as Relay source guilds."
                ),
                ephemeral=True,
            )
            return

        # Parse guild ID
        try:
            target_id = int(support_guild_id.strip())
        except ValueError:
            await interaction.followup.send(
                embed=message_style.error_embed("Invalid guild ID."),
                ephemeral=True,
            )
            return

        # ── Security Checks ──

        # 1. Prevent self-linking
        if target_id == guild.id:
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Cannot link a guild to itself."
                ),
                ephemeral=True,
            )
            return

        # 2. Bot must be in the target guild
        support_guild = self.bot.get_guild(target_id)
        if support_guild is None:
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Relay is not in that guild. Add the bot to the support guild first."
                ),
                ephemeral=True,
            )
            return

        target_mode = await guild_mode.resolve_guild_mode(target_id)
        if target_mode == guild_mode.GuildMode.SOURCE:
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Cannot use a Relay source guild as a support guild."
                ),
                ephemeral=True,
            )
            return

        # 3. User must be admin in target guild too
        target_member = support_guild.get_member(interaction.user.id)
        if target_member is None or not target_member.guild_permissions.administrator:
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "You must be an Administrator in both guilds to create a link."
                ),
                ephemeral=True,
            )
            return

        # 4. Prevent reverse loops (target → this guild already exists)
        reverse_link = await queries.get_support_guild_id(target_id)
        if reverse_link == guild.id:
            await interaction.followup.send(
                embed=message_style.error_embed(
                    "Cannot create a circular link. "
                    f"**{support_guild.name}** already routes to this guild."
                ),
                ephemeral=True,
            )
            return

        # 5. Check if already linked
        existing = await queries.get_guild_link(guild.id)
        if existing:
            old_guild = self.bot.get_guild(existing["support_guild_id"])
            old_name = old_guild.name if old_guild else str(existing["support_guild_id"])
            await interaction.followup.send(
                embed=message_style.warning_embed(
                    f"This guild is already linked to **{old_name}**.\n"
                    "Use `/unlinksupport` first to remove the existing link."
                ),
                ephemeral=True,
            )
            return

        # ── Create Link ──
        success = await queries.create_guild_link(
            source_guild_id=guild.id,
            support_guild_id=target_id,
            linked_by=interaction.user.id,
        )

        if success:
            await interaction.followup.send(
                embed=message_style.success_embed(
                    f"🌐 Linked **{guild.name}** → **{support_guild.name}**\n"
                    f"Tickets from this guild will now route to the support guild."
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=message_style.error_embed("Failed to create link."),
                ephemeral=True,
            )

    # ── /unlinksupport ───────────────────────────────

    @app_commands.command(
        name="unlinksupport",
        description="Remove the support guild link from this guild.",
    )
    @app_commands.default_permissions(administrator=True)
    async def unlinksupport(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return

        existing = await queries.get_guild_link(guild.id)
        if not existing:
            await interaction.response.send_message(
                embed=message_style.warning_embed(
                    "This guild has no support link configured."
                ),
                ephemeral=True,
            )
            return

        removed = await queries.remove_guild_link(guild.id)
        if removed:
            old_guild = self.bot.get_guild(existing["support_guild_id"])
            old_name = old_guild.name if old_guild else str(existing["support_guild_id"])
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    f"Link to **{old_name}** removed.\n"
                    "Tickets will now be created locally."
                ),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=message_style.error_embed("Failed to remove link."),
                ephemeral=True,
            )


    # ── /logchannel ───────────────────────────────────

    @app_commands.command(
        name="logchannel",
        description="Set or remove the transcript log channel.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(channel="Channel for transcript logs (omit to remove)")
    async def logchannel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return

        if await guild_mode.require_not_source(interaction) or await guild_mode.require_not_support(interaction):
            return

        if channel:
            await queries.set_transcript_log_channel(guild.id, channel.id)
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    f"Transcript log channel set to {channel.mention}."
                ),
                ephemeral=True,
            )
        else:
            await queries.remove_transcript_log_channel(guild.id)
            await interaction.response.send_message(
                embed=message_style.success_embed(
                    "Transcript log channel removed. Relay will auto-create one when needed."
                ),
                ephemeral=True,
            )

    # ── /staffperms ────────────────────────────────────

    @app_commands.command(
        name="staffperms",
        description="Manage granular staff role permissions.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="Staff role to edit (optional - shows overview)")
    async def staffperms(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=message_style.error_embed("This command requires administrator permissions."),
                ephemeral=True,
            )
            return
        guild = interaction.guild
        if guild is None:
            return

        await interaction.response.defer(ephemeral=True)
        if await guild_mode.require_not_support_deferred(interaction):
            return

        # Get all staff roles
        staff_role_ids = await queries.get_support_roles(guild.id)
        if not staff_role_ids:
            await interaction.followup.send(
                embed=message_style.warning_embed(
                    "No staff roles configured. Use `/staffroles` first."
                ),
                ephemeral=True,
            )
            return

        if role is None:
            # Show overview (grouped by permission profile)
            await self._show_permission_overview(interaction, guild, staff_role_ids)
        else:
            # Show permission editor for specific role
            if role.id not in staff_role_ids:
                await interaction.followup.send(
                    embed=message_style.warning_embed(
                        f"**{role.name}** is not a staff role.\n"
                        "Use `/staffroles` to add it first."
                    ),
                    ephemeral=True,
                )
                return
            await self._show_permission_editor(interaction, guild, role)

    async def _show_permission_overview(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        staff_role_ids: list[int],
    ) -> None:
        """Show paginated overview of permission profiles grouped by identical denials."""
        profiles = await queries.get_all_role_permission_profiles(guild.id)

        if not profiles:
            await interaction.followup.send(
                embed=message_style.relay_embed(
                    title="Staff Permissions Overview",
                    description="All staff roles currently have full access to all commands.",
                    color=RELAY_COLOR,
                ),
                ephemeral=True,
            )
            return

        # Build overview embed
        desc = "Roles grouped by identical permission profiles:\n\n"
        for i, (denied_key, role_ids) in enumerate(profiles.items(), 1):
            role_names = []
            for role_id in role_ids:
                r = guild.get_role(role_id)
                if r:
                    role_names.append(r.name)
                else:
                    role_names.append(f"<@{role_id}> (deleted)")

            denied_caps = denied_key.split(",") if denied_key else []
            if denied_caps:
                caps_list = "\n".join(f"• `{cap}`" for cap in sorted(denied_caps))
                desc += f"**Group {i}**\n**Roles:** {', '.join(role_names)}\n**Denied Commands:**\n{caps_list}\n\n"
            else:
                desc += f"**Group {i}**\n**Roles:** {', '.join(role_names)}\n**Denied Commands:** None (full access)\n\n"

        embed = message_style.relay_embed(
            title="Staff Permissions Overview",
            description=desc,
            color=RELAY_COLOR,
        )
        embed.set_footer(text="Use /staffperms <role> to edit a specific role")

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _show_permission_editor(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        role: discord.Role,
    ) -> None:
        """Show interactive permission editor for a specific role."""
        view = StaffPermsView(role.id, guild.id, guild)
        await view.load_denied_capabilities()

        # Build capability toggle buttons
        for i, (cap, desc) in enumerate(sorted(RELAY_CAPABILITIES.items())):
            btn = _CapabilityToggle(cap, view)
            btn.row = i // 4  # 4 buttons per row
            view.add_item(btn)

        # Add done button
        done_btn = _DoneButton(view)
        done_btn.row = 4
        view.add_item(done_btn)

        denied_count = len(view.denied_capabilities)
        denied_list = sorted(view.denied_capabilities) if view.denied_capabilities else ["None"]

        embed = message_style.relay_embed(
            title=f"Edit Permissions: {role.name}",
            description=(
                f"This controls which Relay operational commands **{role.name}** may access.\n\n"
                f"**Currently Denied ({denied_count}):**\n" + "\n".join(f"• `{cap}`" for cap in denied_list)
            ),
            color=RELAY_COLOR,
        )
        embed.set_footer(text="Toggle buttons to deny/allow commands • Click Done when finished")

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class _DoneButton(ui.Button):
    """Button to finish editing permissions."""

    def __init__(self, view: StaffPermsView):
        self.view_ref = view
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Done",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=message_style.success_embed(
                f"Permissions updated for **{self.view_ref.guild.get_role(self.view_ref.role_id).name}**."
            ),
            view=None,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Setup(bot))
