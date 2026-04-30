# PvpSkill Backend

Backend + Discord bot for the [PvpSkill](https://github.com/ItsN4x/PvpSkill) Minecraft Fabric mod.

- **FastAPI** HTTP API (consumed by the mod): verification codes, Discord-account links, cached [mctiers.com v2](https://mctiers.com/docs/v2) proxy, overall-rank lookup.
- **discord.py** slash-command bot: `/verify`, `/tier`, `/profile`, `/rank`, `/top`, `/title`, `/region`, `/compare`, `/whois`, `/unlink`, `/stats`, `/help`.
- Both run in a single process so they share the SQLite database and mctiers client cache.

## Verify flow

1. In-game: `/pvpskill verify` → mod POSTs `{mc_uuid, mc_name}` to `/verify/start` → backend returns an 8-char code (e.g. `DB67A9EF`).
2. The mod tells the player which Discord channel to paste the code in (the verify channel configured in `Settings.discord_verify_channel_id`).
3. In Discord: `/verify DB67A9EF` → bot calls `verify.redeem` → `db.link()` inserts the `(mc_uuid, discord_id)` pair.
4. Future mod requests can call `GET /link/by-uuid/{mc_uuid}` to see if the account is linked.

## Running locally

```bash
pip install -e .[dev]
export DISCORD_BOT_TOKEN="…"            # from https://discord.com/developers/applications
python -m pvpskill_backend
```

The API listens on `http://0.0.0.0:8080` by default. Visit `/docs` for the interactive OpenAPI UI.

## Deploying to Fly.io

```bash
fly launch --copy-config --no-deploy        # once, accepts the existing fly.toml
fly secrets set DISCORD_BOT_TOKEN=…
fly volumes create pvpskill_data --region fra --size 1
fly deploy
```

The `fly.toml` ships a 512 MB VM with a 1 GB volume mounted at `/data` for the SQLite database.

## Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | *(required for bot)* | Bot token from the Discord developer portal. |
| `DISCORD_GUILD_ID` | `1496516138958590004` | Guild slash commands are synced to. |
| `DISCORD_VERIFY_CHANNEL_ID` | `1496516140808142852` | `/verify` is enforced to this channel. |
| `DISCORD_INVITE_URL` | `https://discord.gg/8uNSjtpj5m` | Returned to the mod in `/verify/start`. |
| `MCTIERS_BASE_URL` | `https://mctiers.com/api/v2` | Upstream tier-list API. |
| `MCTIERS_CACHE_TTL_SECONDS` | `120` | In-memory TTL for upstream responses. |
| `VERIFY_CODE_TTL_SECONDS` | `600` | How long a pending code is valid. |
| `DATA_DIR` | `data` | Parent dir for SQLite + scratch. |

## Endpoint summary

| Method | Path | Consumer |
| --- | --- | --- |
| POST | `/verify/start` | Mod — returns code + channel to paste it in. |
| GET | `/link/by-uuid/{mc_uuid}` | Mod — checks if account is linked. |
| POST | `/link/unlink` | Mod — breaks a link. |
| GET | `/mctiers/by-name/{name}` | Mod + bot — profile by username (fixes the v1 404 bug). |
| GET | `/mctiers/by-uuid/{uuid}` | Mod + bot — profile by UUID. |
| GET | `/mctiers/top/{gamemode}` | Mod — leaderboard. |
| GET | `/mctiers/rank/{uuid}` | Mod — overall leaderboard position. |
| GET | `/stats` | Anyone — total linked accounts. |
| GET | `/healthz` | Fly.io healthcheck. |
