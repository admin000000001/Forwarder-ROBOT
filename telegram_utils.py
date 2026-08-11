from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html import escape as _html_escape
from typing import Awaitable, Callable, TypeVar

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

logger = logging.getLogger("forwarder")

T = TypeVar("T")


# ============================================================
# HTML SAFETY
# ============================================================

def esc(value: object) -> str:
    """
    Safely escape dynamic text for Telegram HTML parse mode.

    Prevents errors such as:
        Bad Request: can't parse entities:
        Unsupported start tag "id"
    """
    if value is None:
        return ""

    return _html_escape(str(value), quote=False)


# ============================================================
# CHAT VERIFICATION
# ============================================================

@dataclass
class ChatAccessResult:
    ok: bool
    title: str | None = None
    username: str | None = None
    error: str | None = None


async def verify_chat_access(
    bot: Bot,
    chat_id: int,
) -> ChatAccessResult:
    """
    Check whether the bot can access a Telegram chat/channel.
    """

    try:
        chat = await bot.get_chat(chat_id)

        title = (
            getattr(chat, "title", None)
            or getattr(chat, "first_name", None)
            or getattr(chat, "username", None)
            or str(chat_id)
        )

        username = getattr(chat, "username", None)

        return ChatAccessResult(
            ok=True,
            title=title,
            username=username,
            error=None,
        )

    except TelegramBadRequest as exc:
        return ChatAccessResult(
            ok=False,
            error=str(exc),
        )

    except TelegramNetworkError as exc:
        return ChatAccessResult(
            ok=False,
            error=f"Network error: {exc}",
        )

    except Exception as exc:
        return ChatAccessResult(
            ok=False,
            error=str(exc),
        )


# ============================================================
# RETRY HELPER
# ============================================================

async def _run_with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    backoff_seconds: int = 5,
) -> T:

    last_error: Exception | None = None

    retries = max(1, int(retries))

    for attempt in range(1, retries + 1):

        try:
            return await operation()

        except TelegramRetryAfter as exc:
            last_error = exc

            retry_after = int(getattr(exc, "retry_after", 5))

            if attempt >= retries:
                break

            logger.warning(
                "[WARNING] Telegram rate limit. "
                "Retrying in %s seconds (attempt %s/%s)",
                retry_after,
                attempt,
                retries,
            )

            await asyncio.sleep(retry_after)

        except (
            TelegramNetworkError,
            TelegramServerError,
            TimeoutError,
            asyncio.TimeoutError,
        ) as exc:

            last_error = exc

            if attempt >= retries:
                break

            delay = backoff_seconds * attempt

            logger.warning(
                "[WARNING] Telegram request failed: %s. "
                "Retrying in %s seconds (attempt %s/%s)",
                exc,
                delay,
                attempt,
                retries,
            )

            await asyncio.sleep(delay)

        except TelegramBadRequest:
            # BadRequest normally means the request itself is invalid.
            # Retrying will not fix wrong chat/message IDs.
            raise

        except Exception as exc:
            last_error = exc

            if attempt >= retries:
                break

            delay = backoff_seconds * attempt

            logger.warning(
                "[WARNING] Unexpected Telegram error: %s. "
                "Retrying in %s seconds (attempt %s/%s)",
                exc,
                delay,
                attempt,
                retries,
            )

            await asyncio.sleep(delay)

    if last_error is not None:
        raise last_error

    raise RuntimeError("Telegram operation failed")


# ============================================================
# COPY SINGLE MESSAGE
# ============================================================

async def copy_message_with_retry(
    bot: Bot,
    destination_chat_id: int,
    source_chat_id: int,
    message_id: int,
    retries: int = 3,
) -> tuple[bool, str | None]:
    """
    Copy one Telegram message.

    Works for supported Telegram message types, including:
      - video
      - photo
      - document
      - audio
      - voice
      - animation
      - text
      - captioned media
      - stickers
      - other Bot API copyable messages

    Telegram's copyMessage keeps the original content/caption
    without creating a forward header.
    """

    async def operation() -> tuple[bool, str | None]:
        await bot.copy_message(
            chat_id=destination_chat_id,
            from_chat_id=source_chat_id,
            message_id=message_id,
        )

        return True, None

    try:
        return await _run_with_retry(
            operation,
            retries=retries,
            backoff_seconds=5,
        )

    except Exception as exc:
        logger.error(
            "[ERROR] copy_message failed "
            "(source=%s, message=%s, destination=%s): %s",
            source_chat_id,
            message_id,
            destination_chat_id,
            exc,
        )

        return False, str(exc)


# ============================================================
# COPY MEDIA GROUP / ALBUM
# ============================================================

async def copy_media_group_with_retry(
    bot: Bot,
    destination_chat_id: int,
    source_chat_id: int,
    message_ids: list[int],
    retries: int = 3,
) -> tuple[bool, str | None]:
    """
    Copy an album/media group.

    The Telegram Bot API does not expose a copyMediaGroup method.
    Therefore each message is copied individually in the original
    message-ID order.

    This preserves the individual media and captions.
    """

    if not message_ids:
        return False, "Media group contains no message IDs."

    async def operation() -> tuple[bool, str | None]:

        for message_id in message_ids:

            await bot.copy_message(
                chat_id=destination_chat_id,
                from_chat_id=source_chat_id,
                message_id=message_id,
            )

        return True, None

    try:
        return await _run_with_retry(
            operation,
            retries=retries,
            backoff_seconds=5,
        )

    except Exception as exc:
        logger.error(
            "[ERROR] copy_media_group failed "
            "(source=%s, messages=%s, destination=%s): %s",
            source_chat_id,
            message_ids,
            destination_chat_id,
            exc,
        )

        return False, str(exc)


# ============================================================
# OPTIONAL: COPY MULTIPLE MESSAGES
# ============================================================

async def copy_messages_with_retry(
    bot: Bot,
    destination_chat_id: int,
    source_chat_id: int,
    message_ids: list[int],
    retries: int = 3,
) -> tuple[bool, str | None]:
    """
    Copy multiple messages sequentially.

    Useful for posts that contain several related messages.
    """

    if not message_ids:
        return False, "No message IDs supplied."

    async def operation() -> tuple[bool, str | None]:

        for message_id in message_ids:

            await bot.copy_message(
                chat_id=destination_chat_id,
                from_chat_id=source_chat_id,
                message_id=message_id,
            )

        return True, None

    try:
        return await _run_with_retry(
            operation,
            retries=retries,
            backoff_seconds=5,
        )

    except Exception as exc:
        logger.error(
            "[ERROR] copy_messages failed "
            "(source=%s, messages=%s, destination=%s): %s",
            source_chat_id,
            message_ids,
            destination_chat_id,
            exc,
        )

        return False, str(exc)


# ============================================================
# TEST HELPERS
# ============================================================

async def test_chat_access(
    bot: Bot,
    chat_id: int,
) -> bool:
    """
    Simple boolean access test.
    """

    result = await verify_chat_access(bot, chat_id)
    return result.ok
