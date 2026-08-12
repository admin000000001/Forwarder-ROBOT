"""
bot.py

Render + Termux compatible Telegram forwarding bot.
"""

from __future__ import annotations

import asyncio
import logging
import os

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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s "
        "%(message)s"
    ),
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(
    "forwarder"
)


# ============================================================
# HEALTH SERVER
# ============================================================

async def root_handler(
    request: web.Request,
) -> web.Response:

    return web.Response(
        text="Forwarder-ROBOT is running.",
        content_type="text/plain",
    )


async def health_handler(
    request: web.Request,
) -> web.Response:

    return web.json_response(
        {
            "status": "ok",
            "service": "Forwarder-ROBOT",
        }
    )


async def start_health_server():

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    app = web.Application()

    app.router.add_get(
        "/",
        root_handler,
    )

    app.router.add_get(
        "/health",
        health_handler,
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port,
    )

    await site.start()

    logger.info(
        "HTTP health server running on port %s",
        port,
    )

    return runner


# ============================================================
# VERIFY CHANNEL
# ============================================================

async def verify_configured_channels(
    bot: Bot,
) -> None:

    # ========================================================
    # SOURCES
    # ========================================================

    sources = await storage.get_sources()

    enabled_sources = 0

    for source in sources:

        try:

            chat_id = int(
                source["chat_id"]
            )

        except Exception:

            source["enabled"] = False

            continue

        try:

            chat = await bot.get_chat(
                chat_id
            )

            source["enabled"] = True

            source["title"] = (
                chat.title
                or source.get(
                    "title",
                    str(chat_id),
                )
            )

            source["username"] = (
                chat.username
            )

            enabled_sources += 1

            logger.info(
                "Source verified: %s (%s)",
                source["title"],
                chat_id,
            )

        except Exception as exc:

            # IMPORTANT:
            # Do NOT delete source.
            # Keep configuration even if temporary
            # Telegram failure occurs.
            source["enabled"] = False

            logger.warning(
                "Source verification failed %s: %s",
                chat_id,
                exc,
            )

    await storage.save_sources(
        sources
    )

    # ========================================================
    # DESTINATIONS
    # ========================================================

    channels = await storage.get_channels()

    enabled_channels = 0

    for channel in channels:

        try:

            chat_id = int(
                channel["chat_id"]
            )

        except Exception:

            channel["enabled"] = False

            continue

        try:

            chat = await bot.get_chat(
                chat_id
            )

            channel["enabled"] = True

            channel["title"] = (
                chat.title
                or channel.get(
                    "title",
                    str(chat_id),
                )
            )

            channel["username"] = (
                chat.username
            )

            enabled_channels += 1

            logger.info(
                "Destination verified: %s (%s)",
                channel["title"],
                chat_id,
            )

        except Exception as exc:

            channel["enabled"] = False

            logger.warning(
                "Destination verification failed %s: %s",
                chat_id,
                exc,
            )

    await storage.save_channels(
        channels
    )

    logger.info(
        "Channels: sources=%s/%s destinations=%s/%s",
        enabled_sources,
        len(sources),
        enabled_channels,
        len(channels),
    )


# ============================================================
# TELEGRAM AUTH
# ============================================================

async def authenticate(
    bot: Bot,
) -> bool:

    try:

        me = await bot.get_me()

        logger.info(
            "Authenticated as @%s (id=%s)",
            me.username or "unknown",
            me.id,
        )

        return True

    except TelegramUnauthorizedError:

        logger.error(
            "BOT_TOKEN is invalid or revoked."
        )

        return False

    except (
        TelegramNetworkError,
        asyncio.TimeoutError,
        TimeoutError,
    ) as exc:

        logger.error(
            "Telegram network error: %s",
            exc,
        )

        return False

    except Exception as exc:

        logger.exception(
            "Authentication failed: %s",
            exc,
        )

        return False


# ============================================================
# RUN ONE SESSION
# ============================================================

async def run_bot(
    bot: Bot,
    dp: Dispatcher,
    scheduler: Scheduler,
) -> None:

    authenticated = await authenticate(
        bot
    )

    if not authenticated:

        raise RuntimeError(
            "Telegram authentication failed."
        )

    # Verify configured channels.
    await verify_configured_channels(
        bot
    )

    # --------------------------------------------------------
    # Register handlers ONLY ONCE
    # --------------------------------------------------------

    if not getattr(
        dp,
        "_forwarder_registered",
        False,
    ):

        register_handlers(
            dp,
            bot,
            scheduler,
        )

        dp._forwarder_registered = True

        logger.info(
            "Handlers registered."
        )

    # --------------------------------------------------------
    # Resume scheduler
    # --------------------------------------------------------

    try:

        await scheduler.resume_if_needed()

    except Exception as exc:

        logger.exception(
            "Scheduler resume failed: %s",
            exc,
        )

    # --------------------------------------------------------
    # Polling
    # --------------------------------------------------------

    logger.info(
        "Telegram polling started."
    )

    await dp.start_polling(
        bot,
        polling_timeout=30,
        handle_as_tasks=True,
        close_bot_session=False,
    )


# ============================================================
# MAIN
# ============================================================

async def main() -> None:

    health_runner = (
        await start_health_server()
    )

    bot = Bot(
        token=CONFIG.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = Dispatcher()

    scheduler = Scheduler(
        bot
    )

    retry_delay = 10

    try:

        while True:

            try:

                await run_bot(
                    bot,
                    dp,
                    scheduler,
                )

                logger.warning(
                    "Polling stopped."
                )

                await asyncio.sleep(
                    retry_delay
                )

            except TelegramUnauthorizedError:

                logger.error(
                    "BOT_TOKEN invalid/revoked. "
                    "Stopping permanently."
                )

                break

            except (
                TelegramNetworkError,
                asyncio.TimeoutError,
                TimeoutError,
                ConnectionError,
            ) as exc:

                logger.error(
                    "Network failure: %s",
                    exc,
                )

                await asyncio.sleep(
                    retry_delay
                )

            except asyncio.CancelledError:

                raise

            except Exception as exc:

                logger.exception(
                    "Bot crashed: %s",
                    exc,
                )

                logger.info(
                    "Restarting in %s seconds...",
                    retry_delay,
                )

                await asyncio.sleep(
                    retry_delay
                )

    finally:

        logger.info(
            "Shutting down..."
        )

        try:

            if scheduler.is_running():

                await scheduler.stop()

        except Exception as exc:

            logger.warning(
                "Scheduler shutdown error: %s",
                exc,
            )

        try:

            await bot.session.close()

        except Exception as exc:

            logger.warning(
                "Bot session close error: %s",
                exc,
            )

        try:

            await health_runner.cleanup()

        except Exception as exc:

            logger.warning(
                "Health server cleanup error: %s",
                exc,
            )

        logger.info(
            "Shutdown complete."
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "\nBot stopped by user."
        )

    except Exception as exc:

        logging.getLogger(
            "forwarder"
        ).exception(
            "Fatal error: %s",
            exc,
    )
