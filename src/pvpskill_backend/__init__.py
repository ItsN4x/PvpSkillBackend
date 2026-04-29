"""PvpSkill backend + Discord bot.

Combines a FastAPI HTTP API (called by the PvpSkill Minecraft mod) with a
discord.py slash-command bot into a single process so they can share state
(SQLite database + mctiers.com HTTP client + in-memory verification codes).
"""

__version__ = "1.0.0"
