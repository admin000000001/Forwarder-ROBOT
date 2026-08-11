"""
bot.py
Render-compatible Telegram Bot

Features:
- Aiogram polling
- Render $PORT health server
- /health endpoint
- Automatic startup
- Automatic retry if Telegram/network temporarily fails
- Source channel verification
- Destination channel verification
- Scheduler resume
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramUnauthorizedError,
)

import storage
from config import CONFIG
from handlers import register_handlers
from scheduler import Scheduler
from telegram_utils import verify_chat_access


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("forwarder")


# ============================================================
# RENDER HEALTH SERVER
# ============================================================

async def health_handler(request: web.Request) -> web.Response:
    """
    Render/UptimeRobot health endpoint.
    """

    return web.json_response(
        {
            "status": "ok",
            "service": "telegram-bot",
            "message": "Bot is running",
        }
    )


async def root_handler(request: web.Request) -> web.Response:
    """
    Simple homepage.
    """

    return web.Response(
        text="Telegram Bot is running.",
        content_type="text/plain",
    )


async def start_health_server() -> web.AppRunner:
    """
    Start HTTP server on Render's $PORT.
    """

    port = int(os.getenv("PORT", "10000"))

    app = web.Application()

    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port,
    )

    await site.start()

    logger.info(
        "[RENDER] Health server started on 0.0.0.0:%s",
        port,
    )

    return runner


# ============================================================
# STARTUP CHECKS
# ============================================================

async def startup_checks(bot: Bot) -> bool:
    """
    Verify bot token and configured Telegram channels.

    Returns:
        True if Telegram authentication succeeded.
    """

    logger.info("[INFO] Bot starting...")

    # --------------------------------------------------------
    # Telegram authentication
    # --------------------------------------------------------

    try:
        me = await bot.get_me()

    except TelegramUnauthorizedError:
        logger.error(
            "[CONFIG ERROR] BOT_TOKEN is invalid or revoked."
        )
        return False

    except (TelegramNetworkError, TimeoutError, asyncio.TimeoutError) as exc:
        logger.error(
            "[NETWORK ERROR] Telegram connection failed: %s",
            exc,
        )
        return False

    except Exception as exc:
        logger.error(
            "[ERROR] Unable to authenticate with Telegram: %s",
            exc,
        )
        return False

    logger.info(
        "[SUCCESS] Authenticated as @%s",
        me.username or "unknown",
    )

    # ========================================================
    # SOURCE CHANNELS
    # ========================================================

    try:
        sources = await storage.get_sources()
    except Exception as exc:
        logger.error(
            "[ERROR] Cannot load source channels: %s",
            exc,
        )
        sources = []

    enabled_sources = 0

    for source in sources:

        chat_id = source.get("chat_id")

        if chat_id is None:
            logger.warning(
                "[WARNING] Source entry has no chat_id: %s",
                source,
            )
            source["enabled"] = False
            continue

        try:
            result = await verify_chat_access(
                bot,
                chat_id,
            )

            source["enabled"] = result.ok

            if result.ok:

                source["title"] = (
                    result.title
                    or source.get("title")
                    or str(chat_id)
                )

                source["username"] = result.username

                enabled_sources += 1

                logger.info(
                    "[SUCCESS] Source channel loaded: %s (%s)",
                    source["title"],
                    chat_id,
                )

            else:

                logger.warning(
                    "[WARNING] Source unavailable (%s): %s",
                    chat_id,
                    result.error,
                )

        except Exception as exc:

            source["enabled"] = False

            logger.warning(
                "[WARNING] Source check failed (%s): %s",
                chat_id,
                exc,
            )

    if sources:

        try:
            await storage.save_sources(sources)
        except Exception as exc:
            logger.error(
                "[ERROR] Could not save sources: %s",
                exc,
            )

    logger.info(
        "[INFO] Source channels enabled: %s/%s",
        enabled_sources,
        len(sources),
    )

    # ========================================================
    # DESTINATION CHANNELS
    # ========================================================

    try:
        channels = await storage.get_channels()
    except Exception as exc:
        logger.error(
            "[ERROR] Cannot load destination channels: %s",
            exc,
        )
        channels = []

    verified_count = 0

    for channel in channels:

        chat_id = channel.get("chat_id")

        if chat_id is None:

            logger.warning(
                "[WARNING] Destination entry has no chat_id: %s",
                channel,
            )

            channel["enabled"] = False
            continue

        try:

            result = await verify_chat_access(
                bot,
                chat_id,
            )

            channel["enabled"] = result.ok

            if result.ok:

                channel["title"] = (
                    result.title
                    or channel.get("title")
                    or str(chat_id)
                )

                channel["username"] = result.username

                verified_count += 1

                logger.info(
                    "[SUCCESS] Destination channel verified: %s (%s)",
                    channel["title"],
                    chat_id,
                )

            else:

                logger.warning(
                    "[WARNING] Destination unavailable (%s): %s",
                    chat_id,
                    result.error,
                )

        except Exception as exc:

            channel["enabled"] = False

            logger.warning(
                "[WARNING] Destination check failed (%s): %s",
                chat_id,
                exc,
            )

    if channels:

        try:
            await storage.save_channels(channels)
        except Exception as exc:
            logger.error(
                "[ERROR] Could not save destination channels: %s",
                exc,
            )

    logger.info(
        "[INFO] Destination channels verified: %s/%s",
        verified_count,
        len(channels),
    )

    # ========================================================
    # POSTS
    # ========================================================

    try:

        posts = await storage.get_posts()

        logger.info(
            "[INFO] Posts loaded: %s",
            len(posts),
        )

    except Exception as exc:

        logger.error(
            "[ERROR] Could not load posts: %s",
            exc,
        )

    return True


# ============================================================
# RUN BOT
# ============================================================

async def run_bot(
    bot: Bot,
    dp: Dispatcher,
    scheduler: Scheduler,
) -> None:

    # --------------------------------------------------------
    # Startup
    # --------------------------------------------------------

    authenticated = await startup_checks(bot)

    if not authenticated:

        raise RuntimeError(
            "Telegram authentication failed."
        )

    # --------------------------------------------------------
    # Register handlers
    # --------------------------------------------------------

    register_handlers(
        dp,
        bot,
        scheduler,
    )

    logger.info(
        "[INFO] Handlers registered"
    )

    # --------------------------------------------------------
    # Resume scheduler
    # --------------------------------------------------------

    try:

        await scheduler.resume_if_needed()

        logger.info(
            "[INFO] Scheduler state checked"
        )

    except Exception as exc:

        logger.error(
            "[ERROR] Scheduler resume failed: %s",
            exc,
        )

    # --------------------------------------------------------
    # Polling
    # --------------------------------------------------------

    logger.info(
        "[INFO] Telegram polling started"
    )

    await dp.start_polling(
        bot,

        # Long polling timeout
        polling_timeout=30,

        # Don't let one update block others
        handle_as_tasks=True,

        # Keep bot session under our control
        close_bot_session=False,
    )


# ============================================================
# MAIN
# ============================================================

async def main() -> None:

    # --------------------------------------------------------
    # Start Render HTTP health server FIRST
    # --------------------------------------------------------

    health_runner = await start_health_server()

    # --------------------------------------------------------
    # Create Telegram bot
    # --------------------------------------------------------

    bot = Bot(
        token=CONFIG.bot_token,

        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = Dispatcher()

    scheduler = Scheduler(bot)

    try:

        # ====================================================
        # AUTOMATIC BOT START / RETRY LOOP
        # ====================================================

        retry_delay = 10

        while True:

            try:

                await run_bot(
                    bot,
                    dp,
                    scheduler,
                )

                # If polling exits normally
                logger.warning(
                    "[WARNING] Telegram polling stopped."
                )

                await asyncio.sleep(retry_delay)

            except TelegramUnauthorizedError:

                logger.error(
                    "[FATAL] BOT_TOKEN is invalid/revoked."
                )

                break

            except (
                TelegramNetworkError,
                TimeoutError,
                asyncio.TimeoutError,
                ConnectionError,
            ) as exc:

                logger.error(
                    "[NETWORK] Telegram connection lost: %s",
                    exc,
                )

                logger.info(
                    "[RESTART] Retrying bot in %s seconds...",
                    retry_delay,
                )

                await asyncio.sleep(retry_delay)

            except asyncio.CancelledError:

                logger.info(
                    "[INFO] Main task cancelled."
                )

                raise

            except Exception as exc:

                logger.exception(
                    "[ERROR] Bot crashed: %s",
                    exc,
                )

                logger.info(
                    "[RESTART] Restarting bot in %s seconds...",
                    retry_delay,
                )

                await asyncio.sleep(retry_delay)

    finally:

        logger.info(
            "[INFO] Shutting down..."
        )

        # ----------------------------------------------------
        # Stop scheduler
        # ----------------------------------------------------

        try:

            if scheduler.is_running():

                await scheduler.stop()

        except Exception as exc:

            logger.warning(
                "[WARNING] Scheduler shutdown error: %s",
                exc,
            )

        # ----------------------------------------------------
        # Close Telegram session
        # ----------------------------------------------------

        try:

            await bot.session.close()

        except Exception as exc:

            logger.warning(
                "[WARNING] Telegram session close error: %s",
                exc,
            )

        # ----------------------------------------------------
        # Close HTTP server
        # ----------------------------------------------------

        try:

            await health_runner.cleanup()

        except Exception as exc:

            logger.warning(
                "[WARNING] Health server cleanup error: %s",
                exc,
            )

        logger.info(
            "[INFO] Shutdown complete."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print(
            "\n[INFO] Bot stopped by user."
        )

    except SystemExit:

        raise

    except Exception as exc:

        logger.exception(
            "[FATAL] Application stopped: %s",
            exc,
              )
