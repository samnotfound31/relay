"""
Relay Bot — Database Connection
Manages the async SQLite connection lifecycle.
"""

import os
import aiosqlite
from bot.config import DATABASE_PATH


_connection: aiosqlite.Connection | None = None


async def get_connection() -> aiosqlite.Connection:
    """Return the singleton database connection, creating it if needed."""
    global _connection
    if _connection is None:
        database_dir = os.path.dirname(DATABASE_PATH)
        if database_dir:
            os.makedirs(database_dir, exist_ok=True)
        _connection = await aiosqlite.connect(DATABASE_PATH)
        _connection.row_factory = aiosqlite.Row
        await _connection.execute("PRAGMA journal_mode=WAL")
        await _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


async def close_connection() -> None:
    """Gracefully close the database connection."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
