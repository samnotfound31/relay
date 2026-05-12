"""
Relay Bot — Entry Point
Initializes the bot, loads cogs, registers persistent views, and starts.
"""

import asyncio
import logging
import discord
from discord.ext import commands

from bot.config import BOT_TOKEN
from bot.database.connection import close_connection
from bot.database.schema import initialize_schema
from bot.views.dashboard import DashboardView, PreClaimView
from bot.views.ticket_panel import TicketPanelView


# ── Logging ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("relay")


# ── Bot Setup ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# Store bot reference globally so services can access it
bot._relay_bot = bot  # type: ignore


# ── Cog Loading ───────────────────────────────────────
COGS = [
    "bot.cogs.setup",
    "bot.cogs.tickets",
    "bot.cogs.events",
    "bot.cogs.help",
]


@bot.event
async def on_ready() -> None:
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info(f"  Relay is online as {bot.user}")
    log.info(f"  Guilds: {len(bot.guilds)}")
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Set persistent activity for discoverability
    activity = discord.Activity(
        type=discord.ActivityType.playing,
        name="Relay || /help for assistance",
    )
    await bot.change_presence(activity=activity)
    log.info("  Activity set: Playing Relay || /help for assistance")

    # Register persistent views so buttons survive restarts
    bot.add_view(TicketPanelView())
    bot.add_view(DashboardView())
    bot.add_view(PreClaimView())

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        log.info(f"  Synced {len(synced)} slash commands")
    except Exception as e:
        log.error(f"  Failed to sync commands: {e}")

    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


async def main() -> None:
    """Async entry point."""
    async with bot:
        # Initialize database
        await initialize_schema()
        log.info("Database initialized")

        # Load cogs
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                log.info(f"Loaded cog: {cog}")
            except Exception as e:
                log.error(f"Failed to load {cog}: {e}")

        # Start the bot
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Relay shutting down…")
    finally:
        asyncio.run(close_connection())
