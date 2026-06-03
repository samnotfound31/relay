"""
Relay Bot — Database Schema
Creates all required tables and runs migrations.
"""

from bot.database.connection import get_connection


SCHEMA_SQL = """
-- Guild-level settings
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id            INTEGER PRIMARY KEY,
    ticket_category_id  INTEGER,          -- DEPRECATED: was single category, now per-category
    announce_channel_id INTEGER,
    announce_message_id INTEGER,
    banner_url          TEXT,
    emojilist_channel_id INTEGER,          -- Channel with emoji registry message
    emojilist_message_id INTEGER,          -- Message ID of emoji registry
    panel_title         TEXT,             -- Custom panel embed title
    panel_color         TEXT,             -- Custom panel embed color (hex)
    panel_description   TEXT,             -- Custom panel embed description
    panel_button_label  TEXT,             -- Custom panel button label
    panel_footer_text   TEXT,             -- Custom panel footer text
    created_at          TEXT DEFAULT (datetime('now'))
);

-- Support roles allowed to see / interact with tickets
CREATE TABLE IF NOT EXISTS support_roles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    UNIQUE(guild_id, role_id)
);

-- Ticket categories users can choose from
CREATE TABLE IF NOT EXISTS ticket_categories (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id            INTEGER NOT NULL,
    name                TEXT    NOT NULL,
    description         TEXT,
    emoji               TEXT,
    discord_category_id INTEGER,          -- Phase 2: Discord channel category ID
    UNIQUE(guild_id, name)
);

-- Active tickets
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       TEXT,
    guild_id        INTEGER NOT NULL,     -- Where the channel lives (support guild)
    user_id         INTEGER NOT NULL,
    user_name       TEXT,
    channel_id      INTEGER UNIQUE,
    staff_channel_id INTEGER,
    dm_channel_id   INTEGER,
    category_id     INTEGER,
    category_name   TEXT,
    claimed_by      INTEGER,
    claimed_by_name TEXT,
    claimed_at      TEXT,
    status          TEXT DEFAULT 'open',
    ticket_status   TEXT DEFAULT 'open',  -- Phase 2: workflow status
    priority        TEXT DEFAULT 'medium', -- Pre-Phase 3: urgency level
    source_guild_id INTEGER,              -- Phase 3: where the user came from
    community_ticket_number INTEGER,      -- Display number scoped to source/local guild
    relay_session_status TEXT DEFAULT 'active',
    relay_session_left_at TEXT,
    needs_rename_resync INTEGER DEFAULT 0,
    ticket_context_issue TEXT,
    close_reason    TEXT,
    closed_by       INTEGER,
    last_user_message_at TEXT,
    last_staff_message_at TEXT,
    updated_at      TEXT DEFAULT (datetime('now')),
    scheduled_close_at TEXT,                  -- Phase 4: autoclose timestamp
    autoclose_duration   TEXT,                -- Phase 4: e.g. "25m" for logging
    autoclose_closure_message TEXT,
    last_activity_at TEXT DEFAULT (datetime('now')),
    is_inactive     INTEGER DEFAULT 0,    -- Phase 2: inactivity flag
    created_at      TEXT DEFAULT (datetime('now')),
    closed_at       TEXT
);

CREATE TABLE IF NOT EXISTS ticket_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_db_id    INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    ticket_id       TEXT,
    event_type      TEXT NOT NULL,
    actor_id        INTEGER,
    actor_name      TEXT,
    details         TEXT,
    metadata_json   TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Phase 2: Staff personal emojis
CREATE TABLE IF NOT EXISTS staff_emojis (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    emoji     TEXT    NOT NULL,
    UNIQUE(guild_id, user_id),
    UNIQUE(guild_id, emoji)
);

-- Phase 3: Cross-server guild links (source → support)
CREATE TABLE IF NOT EXISTS guild_links (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_guild_id   INTEGER NOT NULL UNIQUE,  -- ONE link per source guild
    support_guild_id  INTEGER NOT NULL,
    linked_by         INTEGER NOT NULL,          -- User ID who created the link
    created_at        TEXT DEFAULT (datetime('now'))
);

-- Phase 4: Transcripts
CREATE TABLE IF NOT EXISTS transcripts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id         INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    source_guild_id   INTEGER,                  -- Community scope
    guild_id          INTEGER NOT NULL,         -- Support guild where ticket lived
    channel_id        INTEGER NOT NULL,
    file_path         TEXT NOT NULL,
    closed_by         INTEGER,
    log_channel_id    INTEGER,
    transcript_message_id INTEGER,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- Phase 4: Per-guild transcript log channel; allow_user_sharing is legacy/ignored
CREATE TABLE IF NOT EXISTS transcript_settings (
    guild_id              INTEGER PRIMARY KEY,
    log_channel_id        INTEGER,
    allow_user_sharing    INTEGER DEFAULT 0,     -- legacy ignored flag; raw transcripts are internal-only
    created_at            TEXT DEFAULT (datetime('now'))
);

-- Phase 4: Staff notes scoped by user + source_guild_id (community-local)
CREATE TABLE IF NOT EXISTS staff_notes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    source_guild_id   INTEGER NOT NULL,          -- Community scope
    author_id         INTEGER NOT NULL,
    author_name       TEXT NOT NULL,
    content           TEXT NOT NULL,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- Phase 4: Response reminders - staff awaiting user reply
CREATE TABLE IF NOT EXISTS ticket_reminders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id         INTEGER NOT NULL,
    staff_id          INTEGER NOT NULL,
    guild_id          INTEGER NOT NULL,         -- Support guild for notification routing
    created_at        TEXT DEFAULT (datetime('now')),
    UNIQUE(ticket_id, staff_id)
);

-- Phase 5: Onboarding persistence — track tip/guidance delivery
CREATE TABLE IF NOT EXISTS onboarding_state (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scope             TEXT NOT NULL,            -- 'guild_setup' | 'staff_first_use'
    entity_id         INTEGER NOT NULL,         -- guild_id or user_id depending on scope
    guild_id          INTEGER NOT NULL,         -- guild context
    delivered_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(scope, entity_id, guild_id)
);

-- Phase 6: Role permission overrides (denylist-style control)
CREATE TABLE IF NOT EXISTS role_permissions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    role_id           INTEGER NOT NULL,
    capability        TEXT NOT NULL,            -- Command/control identifier (e.g., 'reply', 'close', 'history')
    UNIQUE(guild_id, role_id, capability)
);
"""

# Migrations for existing databases
MIGRATIONS = [
    # Phase 2 migrations
    "ALTER TABLE ticket_categories ADD COLUMN discord_category_id INTEGER",
    "ALTER TABLE tickets ADD COLUMN ticket_status TEXT DEFAULT 'open'",
    "ALTER TABLE tickets ADD COLUMN last_activity_at TEXT DEFAULT (datetime('now'))",
    "ALTER TABLE tickets ADD COLUMN is_inactive INTEGER DEFAULT 0",
    "ALTER TABLE guild_settings ADD COLUMN emojilist_channel_id INTEGER",
    "ALTER TABLE guild_settings ADD COLUMN emojilist_message_id INTEGER",
    "ALTER TABLE tickets ADD COLUMN priority TEXT DEFAULT 'medium'",
    # Phase 3 migrations
    "ALTER TABLE tickets ADD COLUMN source_guild_id INTEGER",
    # Session/display migrations
    "ALTER TABLE tickets ADD COLUMN community_ticket_number INTEGER",
    "ALTER TABLE tickets ADD COLUMN relay_session_status TEXT DEFAULT 'active'",
    "ALTER TABLE tickets ADD COLUMN relay_session_left_at TEXT",
    "ALTER TABLE tickets ADD COLUMN needs_rename_resync INTEGER DEFAULT 0",
    "ALTER TABLE tickets ADD COLUMN ticket_context_issue TEXT",
    # Phase 4 migrations
    "ALTER TABLE tickets ADD COLUMN scheduled_close_at TEXT",
    "ALTER TABLE tickets ADD COLUMN autoclose_duration TEXT",
    "ALTER TABLE tickets ADD COLUMN autoclose_closure_message TEXT",
    "ALTER TABLE transcripts ADD COLUMN log_channel_id INTEGER",
    "ALTER TABLE transcripts ADD COLUMN transcript_message_id INTEGER",
    # Phase 4: Response reminders
    "CREATE TABLE IF NOT EXISTS ticket_reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, staff_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, created_at TEXT DEFAULT (datetime('now')), UNIQUE(ticket_id, staff_id))",
    # Phase 5: Onboarding persistence
    "CREATE TABLE IF NOT EXISTS onboarding_state (id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, entity_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, delivered_at TEXT DEFAULT (datetime('now')), UNIQUE(scope, entity_id, guild_id))",
    # Phase 6: Role permission overrides (denylist-style control)
    "CREATE TABLE IF NOT EXISTS role_permissions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL, capability TEXT NOT NULL, UNIQUE(guild_id, role_id, capability))",
    # Phase 7: Panel customization
    "ALTER TABLE guild_settings ADD COLUMN panel_title TEXT",
    "ALTER TABLE guild_settings ADD COLUMN panel_color TEXT",
    "ALTER TABLE guild_settings ADD COLUMN panel_description TEXT",
    "ALTER TABLE guild_settings ADD COLUMN panel_button_label TEXT",
    "ALTER TABLE guild_settings ADD COLUMN panel_footer_text TEXT",
    # Live ticket state + lifecycle logs
    "ALTER TABLE tickets ADD COLUMN ticket_id TEXT",
    "ALTER TABLE tickets ADD COLUMN user_name TEXT",
    "ALTER TABLE tickets ADD COLUMN staff_channel_id INTEGER",
    "ALTER TABLE tickets ADD COLUMN dm_channel_id INTEGER",
    "ALTER TABLE tickets ADD COLUMN category_id INTEGER",
    "ALTER TABLE tickets ADD COLUMN claimed_by_name TEXT",
    "ALTER TABLE tickets ADD COLUMN claimed_at TEXT",
    "ALTER TABLE tickets ADD COLUMN close_reason TEXT",
    "ALTER TABLE tickets ADD COLUMN closed_by INTEGER",
    "ALTER TABLE tickets ADD COLUMN last_user_message_at TEXT",
    "ALTER TABLE tickets ADD COLUMN last_staff_message_at TEXT",
    "ALTER TABLE tickets ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))",
    "CREATE TABLE IF NOT EXISTS ticket_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_db_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, ticket_id TEXT, event_type TEXT NOT NULL, actor_id INTEGER, actor_name TEXT, details TEXT, metadata_json TEXT, created_at TEXT DEFAULT (datetime('now')))",
]


async def initialize_schema() -> None:
    """Run the schema DDL and migrations — safe to call multiple times."""
    db = await get_connection()
    await db.executescript(SCHEMA_SQL)
    await db.commit()

    # Run migrations (ignore errors for already-applied columns)
    for migration in MIGRATIONS:
        try:
            await db.execute(migration)
            await db.commit()
        except Exception:
            pass  # Column/table already exists


BACKFILL_EVENT_OWNERSHIP_SQL = """
UPDATE ticket_events
SET guild_id = (
    SELECT tickets.source_guild_id
    FROM tickets
    WHERE tickets.id = ticket_events.ticket_db_id
)
WHERE EXISTS (
    SELECT 1
    FROM tickets
    WHERE tickets.id = ticket_events.ticket_db_id
      AND tickets.source_guild_id IS NOT NULL
      AND tickets.source_guild_id != ticket_events.guild_id
);
"""


async def backfill_event_ownership() -> int:
    """
    Migrate existing ticket_events rows so that guild_id reflects source guild ownership.
    For linked tickets, ticket_events.guild_id is set to tickets.source_guild_id.
    Returns number of rows updated.
    """
    db = await get_connection()
    cursor = await db.execute(BACKFILL_EVENT_OWNERSHIP_SQL)
    await db.commit()
    return cursor.rowcount
