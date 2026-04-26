from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn

from .api import create_app
from .bot import make_bot
from .config import settings
from .db import Database
from .mctiers import MctiersClient
from .verify import VerifyStore


log = logging.getLogger("pvpskill.main")


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    settings.ensure_dirs()

    db = Database(settings.db_path)
    await db.connect()

    mctiers = MctiersClient(
        base_url=settings.mctiers_base_url,
        cache_ttl_seconds=settings.mctiers_cache_ttl_seconds,
    )

    verify_store = VerifyStore(
        ttl_seconds=settings.verify_code_ttl_seconds,
        code_length=settings.verify_code_length,
    )

    bot_task: asyncio.Task | None = None
    bot = None
    if settings.discord_bot_token:
        bot = make_bot(db=db, mctiers=mctiers, verify=verify_store)
        bot_task = asyncio.create_task(bot.start(settings.discord_bot_token), name="discord-bot")
    else:
        log.warning("DISCORD_BOT_TOKEN not set — Discord bot disabled (API only)")

    app = create_app(
        db=db,
        mctiers=mctiers,
        verify=verify_store,
        discord_ready=lambda: bool(bot and bot.is_ready()),
    )

    config = uvicorn.Config(
        app, host=settings.host, port=settings.port, log_level="info", access_log=False
    )
    server = uvicorn.Server(config)
    api_task = asyncio.create_task(server.serve(), name="uvicorn")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    tasks: list[asyncio.Task] = [api_task]
    if bot_task is not None:
        tasks.append(bot_task)

    stop_task = asyncio.create_task(stop_event.wait(), name="stop-wait")

    try:
        done, _ = await asyncio.wait(
            [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            exc = t.exception()
            if exc is not None:
                log.error("task %s failed: %s", t.get_name(), exc)
    finally:
        stop_task.cancel()
        server.should_exit = True
        if bot is not None and not bot.is_closed():
            await bot.close()
        for t in tasks:
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await mctiers.aclose()
        await db.close()
