"""
bot.py

Entry point. Startup order:
  1. Config already validated at import time (config.py exits on error).
  2. Validate/load JSON state files.
  3. Verify the bot can see the source channel and is an admin there.
  4. Build the Bot/Dispatcher, register handlers.
  5. Start the scheduler wake-loop as an independent asyncio task, isolated
     from polling errors (an exception in one never kills the other).
  6. Start polling.
  7. Handle SIGINT/SIGTERM for a clean shutdown that never corrupts JSON.
"""

from __future__ import annotations

import asyncio
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError

import storage
from config import CONFIG
from utils import log
from scheduler import VideoScheduler
import handlers


async def validate_source_channel(bot: Bot) -> None:
    try:
        chat = await bot.get_chat(CONFIG.source_channel_id)
    except Exception as e:
        log.error(f"Cannot access SOURCE_CHANNEL_ID ({CONFIG.source_channel_id}): {e}")
        log.error("Make sure the bot has been added as an admin to the source channel.")
        raise SystemExit(1)

    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(CONFIG.source_channel_id, me.id)
        if member.status not in ("administrator", "creator"):
            log.error(
                f"Bot is not an administrator in source channel '{chat.title}'. "
                "Please promote it to admin."
            )
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as e:
        log.error(f"Could not verify admin status in source channel: {e}")
        raise SystemExit(1)

    log.info(f"Source channel loaded: {chat.title} ({CONFIG.source_channel_id})")


async def validate_destination_channels(bot: Bot) -> None:
    channels = await storage.get_channels()
    ok_count = 0
    for channel_id in channels:
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(channel_id, me.id)
            if member.status in ("administrator", "creator"):
                ok_count += 1
            else:
                log.warning(f"Bot is not admin in destination channel {channel_id}")
        except Exception as e:
            log.warning(f"Could not verify destination channel {channel_id}: {e}")
    log.info(f"{ok_count}/{len(channels)} destination channels verified")


async def main() -> None:
    log.info("Bot starting...")

    bot = Bot(
        token=CONFIG.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(handlers.router)

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        log.error("BOT_TOKEN is invalid. Get a valid token from @BotFather.")
        raise SystemExit(1)

    log.info(f"Authenticated as @{me.username}")

    await validate_source_channel(bot)
    await validate_destination_channels(bot)

    videos_data = await storage.get_videos()
    log.info(f"{len(videos_data.get('videos', []))} video(s) currently loaded")

    channels = await storage.get_channels()
    log.info(f"{len(channels)} destination channel(s) loaded")

    scheduler = VideoScheduler(bot)
    handlers.bind_scheduler(scheduler)

    # Resolve any interrupted delivery / stale state left over from an
    # unexpected previous shutdown before we start taking new commands.
    schedule = await storage.get_schedule()
    if schedule.get("in_progress_index") is not None:
        log.info(
            f"Detected an interrupted delivery for video index "
            f"{schedule['in_progress_index']} from a previous run; it will "
            "resume automatically (already-delivered channels are skipped)."
        )

    scheduler.start_loop()
    log.info("Scheduler task started")

    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        log.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows / some restricted environments don't support this.
            pass

    polling_task = asyncio.create_task(dp.start_polling(bot), name="polling")

    log.info("Bot is now polling for updates")

    await stop_event.wait()

    log.info("Shutting down gracefully...")
    polling_task.cancel()
    await scheduler.stop_loop()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

    await bot.session.close()
    log.info("Shutdown complete. State has been preserved in data/*.json")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log.info("Interrupted by user")
