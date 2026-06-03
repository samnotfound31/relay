"""
Relay Bot — Entry Point
Initializes the bot, loads cogs, registers persistent views, and starts.
"""

import asyncio
import logging
import aiohttp
import discord
from discord.ext import commands

from bot.config import BOT_TOKEN, API_BASE_URL, HEARTBEAT_INTERVAL, RELAY_HEARTBEAT_SECRET
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


# ── Heartbeat ──────────────────────────────────────────
async def send_heartbeat() -> None:
    """POST bot status to the API server on a periodic interval."""
    await bot.wait_until_ready()
    api_url = API_BASE_URL.rstrip("/")
    has_secret = bool(RELAY_HEARTBEAT_SECRET)
    log.info("Heartbeat URL: %s/api/relay/heartbeat", api_url)
    log.info("Heartbeat auth configured: %s", has_secret)
    if not has_secret:
        log.warning("RELAY_HEARTBEAT_SECRET is not set — heartbeat will be rejected")
    while not bot.is_closed():
        try:
            payload = {
                "guild_count": len(bot.guilds),
                "shard_count": bot.shard_count or 1,
                "uptime": int(bot.uptime.total_seconds()) if hasattr(bot, "uptime") and bot.uptime else 0,
                "version": "1.0.0",
                "status": "online",
            }
            headers = {"Authorization": f"Bearer {RELAY_HEARTBEAT_SECRET}"} if has_secret else {}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_url}/api/relay/heartbeat",
                    json=payload,
                    headers=headers,
                    timeout=10,
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        log.warning("Heartbeat returned status %s headers=%s", resp.status, list(headers.keys()))
                    else:
                        log.debug("Heartbeat OK — %s", resp.status)
        except Exception as exc:
            log.debug("Heartbeat send failed (will retry): %s", exc)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


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

        # Start heartbeat background task
        bot.loop.create_task(send_heartbeat())
        log.info("Heartbeat task started (every %ss)", HEARTBEAT_INTERVAL)

        # Start the bot
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Relay shutting down…")
    finally:
        asyncio.run(close_connection())
