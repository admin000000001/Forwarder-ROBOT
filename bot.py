"""
bot.py
Render + Termux compatible Telegram Forwarder Bot

Features:
- Aiogram 3.x polling
- Render $PORT health server
- /health endpoint
- Automatic Telegram retry
- Channel post updates enabled
- Source channel verification
- Destination channel verification
- Route-aware scheduler
- Scheduler resume
- Safe shutdown
- Dispatcher created only once
- Handlers registered only once
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
# HEALTH SERVER
# ============================================================

async def health_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "service": "telegram-bot",
            "message": "Bot is running",
        }
    )


async def root_handler(request: web.Request) -> web.Response:
    return web.Response(
        text="Telegram Forwarder Bot is running.",
        content_type="text/plain",
    )


async def start_health_server() -> web.AppRunner:
    """
    Render requires the application to listen on $PORT.
    """

    raw_port = os.getenv("PORT", "10000")

    try:
        port = int(raw_port)
    except ValueError:
        port = 10000

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
        "[RENDER] Health server listening on 0.0.0.0:%s",
        port,
    )

    return runner


# ============================================================
# SAFE INTEGER
# ============================================================

def safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ============================================================
# STARTUP CHECKS
# ============================================================

async def startup_checks(bot: Bot) -> bool:
    """
    Check Telegram authentication and configured channels.

    IMPORTANT:
    A temporary verification failure must NOT permanently
    disable the source in storage.
    """

    logger.info("[INFO] Bot starting...")

    # ========================================================
    # BOT AUTHENTICATION
    # ========================================================

    try:
        me = await bot.get_me()

    except TelegramUnauthorizedError:
        logger.error(
            "[FATAL] BOT_TOKEN is invalid or revoked."
        )
        return False

    except (
        TelegramNetworkError,
        TimeoutError,
        asyncio.TimeoutError,
        ConnectionError,
    ) as exc:
        logger.error(
            "[NETWORK] Telegram authentication failed: %s",
            exc,
        )
        return False

    except Exception as exc:
        logger.exception(
            "[ERROR] Telegram authentication failed: %s",
            exc,
        )
        return False

    logger.info(
        "[SUCCESS] Authenticated as @%s | ID=%s",
        me.username or "unknown",
        me.id,
    )

    # ========================================================
    # SOURCES
    # ========================================================

    try:
        sources = await storage.get_sources()
    except Exception as exc:
        logger.exception(
            "[ERROR] Cannot load sources: %s",
            exc,
        )
        sources = []

    enabled_sources = 0

    for source in sources:

        chat_id = safe_int(
            source.get("chat_id")
        )

        if chat_id is None:
            logger.warning(
                "[WARNING] Invalid source entry: %s",
                source,
            )
            continue

        try:

            result = await verify_chat_access(
                bot,
                chat_id,
            )

            if result.ok:

                # Keep source enabled.
                source["enabled"] = True

                source["chat_id"] = chat_id

                if result.title:
                    source["title"] = result.title

                if result.username:
                    source["username"] = result.username

                enabled_sources += 1

                logger.info(
                    "[SOURCE OK] %s | %s",
                    result.title or "Unknown",
                    chat_id,
                )

            else:

                # IMPORTANT:
                # Do not permanently disable it.
                #
                # If Telegram temporarily fails, the source
                # must remain configured.

                logger.warning(
                    "[SOURCE CHECK WARNING] %s | %s",
                    chat_id,
                    result.error,
                )

                if "enabled" not in source:
                    source["enabled"] = True

        except Exception as exc:

            logger.warning(
                "[SOURCE CHECK ERROR] %s | %s",
                chat_id,
                exc,
            )

            if "enabled" not in source:
                source["enabled"] = True

    # Save metadata, but don't delete sources.
    try:
        await storage.save_sources(sources)
    except Exception as exc:
        logger.warning(
            "[WARNING] Could not save source metadata: %s",
            exc,
        )

    logger.info(
        "[INFO] Source channels configured: %s | verified: %s",
        len(sources),
        enabled_sources,
    )

    # ========================================================
    # DESTINATIONS
    # ========================================================

    try:
        channels = await storage.get_channels()
    except Exception as exc:
        logger.exception(
            "[ERROR] Cannot load destination channels: %s",
            exc,
        )
        channels = []

    verified_destinations = 0

    for channel in channels:

        chat_id = safe_int(
            channel.get("chat_id")
        )

        if chat_id is None:
            logger.warning(
                "[WARNING] Invalid destination entry: %s",
                channel,
            )
            continue

        try:

            result = await verify_chat_access(
                bot,
                chat_id,
            )

            if result.ok:

                channel["enabled"] = True
                channel["chat_id"] = chat_id

                if result.title:
                    channel["title"] = result.title

                if result.username:
                    channel["username"] = result.username

                verified_destinations += 1

                logger.info(
                    "[DESTINATION OK] %s | %s",
                    result.title or "Unknown",
                    chat_id,
                )

            else:

                logger.warning(
                    "[DESTINATION CHECK WARNING] %s | %s",
                    chat_id,
                    result.error,
                )

                if "enabled" not in channel:
                    channel["enabled"] = True

        except Exception as exc:

            logger.warning(
                "[DESTINATION CHECK ERROR] %s | %s",
                chat_id,
                exc,
            )

            if "enabled" not in channel:
                channel["enabled"] = True

    try:
        await storage.save_channels(channels)
    except Exception as exc:
        logger.warning(
            "[WARNING] Could not save destination metadata: %s",
            exc,
        )

    logger.info(
        "[INFO] Destination channels configured: %s | verified: %s",
        len(channels),
        verified_destinations,
    )

    # ========================================================
    # ROUTES
    # ========================================================

    try:

        if hasattr(storage, "get_routes"):

            routes = await storage.get_routes()

            logger.info(
                "[INFO] Routes configured: %s",
                len(routes),
            )

            for route in routes:

                source_id = safe_int(
                    route.get("source_id")
                )

                destinations = route.get(
                    "destinations",
                    [],
                )

                logger.info(
                    "[ROUTE] %s -> %s",
                    source_id,
                    destinations,
                )

    except Exception as exc:

        logger.warning(
            "[WARNING] Could not load routes: %s",
            exc,
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

        # Show source distribution.
        source_counter: dict[int, int] = {}

        for post in posts:

            source_id = safe_int(
                post.get("source_chat_id")
            )

            if source_id is not None:

                source_counter[source_id] = (
                    source_counter.get(source_id, 0) + 1
                )

        for source_id, count in source_counter.items():

            logger.info(
                "[POSTS] Source %s -> %s post(s)",
                source_id,
                count,
            )

    except Exception as exc:

        logger.exception(
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

    # ========================================================
    # STARTUP CHECK
    # ========================================================

    authenticated = await startup_checks(
        bot
    )

    if not authenticated:
        raise RuntimeError(
            "Telegram authentication failed."
        )

    # ========================================================
    # REGISTER HANDLERS
    # ========================================================

    #
    # IMPORTANT:
    #
    # handlers.py must contain:
    #
    # def register_handlers(dp, bot, scheduler):
    #
    # and it must protect against duplicate router attachment.
    #

    register_handlers(
        dp,
        bot,
        scheduler,
    )

    logger.info(
        "[SUCCESS] Handlers registered."
    )

    # ========================================================
    # SCHEDULER RESUME
    # ========================================================

    try:

        await scheduler.resume_if_needed()

        logger.info(
            "[INFO] Scheduler resume check complete."
        )

    except Exception as exc:

        logger.exception(
            "[ERROR] Scheduler resume failed: %s",
            exc,
        )

    # ========================================================
    # POLLING
    # ========================================================

    logger.info(
        "[INFO] Telegram polling started."
    )

    logger.info(
        "[INFO] Listening for channel posts..."
    )

    #
    # Explicit allowed_updates is important for the
    # source-channel listener.
    #

    allowed_updates = [
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "callback_query",
    ]

    await dp.start_polling(
        bot,

        polling_timeout=30,

        handle_as_tasks=True,

        close_bot_session=False,

        allowed_updates=allowed_updates,
    )


# ============================================================
# MAIN
# ============================================================

async def main() -> None:

    # ========================================================
    # HEALTH SERVER
    # ========================================================

    health_runner = await start_health_server()

    # ========================================================
    # BOT
    # ========================================================

    bot = Bot(
        token=CONFIG.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    # ========================================================
    # DISPATCHER
    # ========================================================

    #
    # Create Dispatcher ONLY ONCE.
    #
    # Do NOT recreate it inside retry loop.
    #

    dp = Dispatcher()

    # ========================================================
    # SCHEDULER
    # ========================================================

    scheduler = Scheduler(
        bot
    )

    # ========================================================
    # RETRY SETTINGS
    # ========================================================

    retry_delay = 10

    try:

        while True:

            try:

                logger.info(
                    "[INFO] Starting bot worker..."
                )

                await run_bot(
                    bot,
                    dp,
                    scheduler,
                )

                #
                # Polling exited without exception.
                #

                logger.warning(
                    "[WARNING] Telegram polling stopped."
                )

                await asyncio.sleep(
                    retry_delay
                )

            # =================================================
            # INVALID TOKEN
            # =================================================

            except TelegramUnauthorizedError:

                logger.error(
                    "[FATAL] Telegram token is invalid/revoked."
                )

                break

            # =================================================
            # NETWORK ERROR
            # =================================================

            except (
                TelegramNetworkError,
                TimeoutError,
                asyncio.TimeoutError,
                ConnectionError,
            ) as exc:

                logger.error(
                    "[NETWORK] Connection error: %s",
                    exc,
                )

                logger.info(
                    "[RESTART] Retrying in %s seconds...",
                    retry_delay,
                )

                await asyncio.sleep(
                    retry_delay
                )

            # =================================================
            # CANCELLED
            # =================================================

            except asyncio.CancelledError:

                logger.info(
                    "[INFO] Main task cancelled."
                )

                raise

            # =================================================
            # OTHER ERROR
            # =================================================

            except Exception as exc:

                logger.exception(
                    "[ERROR] Bot crashed: %s",
                    exc,
                )

                logger.info(
                    "[RESTART] Restarting in %s seconds...",
                    retry_delay,
                )

                #
                # IMPORTANT:
                # Don't create a second Dispatcher.
                # Don't create another Scheduler.
                #

                await asyncio.sleep(
                    retry_delay
                )

    finally:

        logger.info(
            "[INFO] Shutting down..."
        )

        # ====================================================
        # STOP SCHEDULER
        # ====================================================

        try:

            if scheduler.is_running():

                await scheduler.stop()

        except Exception as exc:

            logger.warning(
                "[WARNING] Scheduler shutdown error: %s",
                exc,
            )

        # ====================================================
        # STOP POLLING
        # ====================================================

        with suppress(Exception):

            await dp.stop_polling()

        # ====================================================
        # CLOSE BOT SESSION
        # ====================================================

        try:

            await bot.session.close()

        except Exception as exc:

            logger.warning(
                "[WARNING] Bot session close error: %s",
                exc,
            )

        # ====================================================
        # CLOSE HEALTH SERVER
        # ====================================================

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

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "\n[INFO] Bot stopped by user."
        )

    except asyncio.CancelledError:

        print(
            "\n[INFO] Bot cancelled."
        )

    except Exception as exc:

        logging.getLogger("forwarder").exception(
            "[FATAL] Application stopped: %s",
            exc,
                )
