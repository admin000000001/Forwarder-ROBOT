"""
bot.py

Entry point. Startup sequence:
  1. Load .env                       (config.py, on import)
  2. Validate BOT_TOKEN               (config.py, on import)
  3. Validate OWNER_ID                (config.py, on import)
  4. Load JSON storage
  5. Initialize bot
  6. Call get_me()
  7. Verify configured source channels
  8. Verify destination channels
  9. Load posts
  10. Load scheduler state
  11. Start scheduler (if it was running before restart)
  12. Start polling
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

import storage
from config import CONFIG
from handlers import register_handlers
from scheduler import Scheduler
from telegram_utils import verify_chat_access

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("forwarder")


async def _startup_checks(bot: Bot) -> None:
    logger.info("[INFO] Bot starting...")

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        print("[CONFIG ERROR] Invalid BOT_TOKEN — Telegram rejected the credentials.")
        raise SystemExit(1)
    except (TelegramNetworkError, TimeoutError) as exc:
        print(f"[CONFIG ERROR] Network error while contacting Telegram: {exc}")
        raise SystemExit(1)

    logger.info("[SUCCESS] Authenticated as @%s", me.username)

    # Verify configured source channels (never terminates the bot on failure).
    sources = await storage.get_sources()
    for source in sources:
        result = await verify_chat_access(bot, source["chat_id"])
        source["enabled"] = result.ok
        if result.ok:
            source["title"] = result.title or source.get("title")
            source["username"] = result.username
            logger.info("[SUCCESS] Source channel loaded: %s", source["title"])
        else:
            logger.warning(
                "[WARNING] Source unavailable (%s): %s", source["chat_id"], result.error
            )
    if sources:
        await storage.save_sources(sources)

    # Verify configured destination channels.
    channels = await storage.get_channels()
    verified_count = 0
    for channel in channels:
        result = await verify_chat_access(bot, channel["chat_id"])
        channel["enabled"] = result.ok
        if result.ok:
            channel["title"] = result.title or channel.get("title")
            channel["username"] = result.username
            verified_count += 1
        else:
            logger.warning(
                "[WARNING] Destination unavailable (%s): %s",
                channel["chat_id"],
                result.error,
            )
    if channels:
        await storage.save_channels(channels)
    logger.info("[SUCCESS] Destination channels verified: %s", verified_count)

    posts = await storage.get_posts()
    logger.info("[INFO] Posts loaded: %s", len(posts))


async def main() -> None:
    bot = Bot(
        token=CONFIG.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    scheduler = Scheduler(bot)

    try:
        await _startup_checks(bot)

        register_handlers(dp, bot, scheduler)

        logger.info("[INFO] Scheduler state loaded")
        await scheduler.resume_if_needed()

        logger.info("[INFO] Starting polling")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
