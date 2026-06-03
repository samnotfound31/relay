# relay-bot

Discord bot service for Relay.

This repository contains only the bot runtime after the Relay repository split.

## Contents

- `bot/` - Discord bot source code
- `requirements.txt` - Python dependencies
- `Dockerfile` - Bot container image
- `docker-compose.yml` - Standalone bot deployment scaffold
- `.env.example` - Bot environment template

## Runtime Data

Relay bot runtime storage is configured through environment variables:

- `DATABASE_PATH` - SQLite database path
- `TRANSCRIPT_DIR` - generated transcript output directory
- `FONT_DIR` - transcript/PDF font directory

Defaults preserve the previous layout by using `./data` relative to the repository or `/app/data` in Docker when mounted.

## Local Start

```bash
pip install -r requirements.txt
python -m bot.main
```

## Docker

```bash
docker compose up --build
```

The default compose file mounts `./data` into `/app/data`. For shared deployment with `relay-website`, mount the same persistent database volume and set `DATABASE_PATH` consistently in both services.
