"""
telegram_utils.py

Telegram helper functions for Forwarder-ROBOT.

Supports:
- HTML escaping
- Telegram chat verification
- Single message copying
- Media-group / album copying
- Retry handling
- Network error handling
- Telegram rate-limit handling

Compatible with aiogram 3.x.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html import escape as html_escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

logger = logging.getLogger("forwarder")


# ============================================================
# HTML ESCAPE
# ============================================================

def esc(value: Any) -> str:
    """
    Escape dynamic values before putting them inside
    Telegram HTML messages.

    Example:
        esc("<test>")

    becomes:
        &lt;test&gt;
    """

    return html_escape(
        str(value),
        quote=False,
    )


# ============================================================
# CHAT VERIFICATION
# ============================================================

@dataclass
class ChatAccessResult:
    ok: bool
    chat_id: int
    title: str | None = None
    username: str | None = None
    error: str | None = None


async def verify_chat_access(
    bot: Bot,
    chat_id: int | str,
) -> ChatAccessResult:
    """
    Verify that the bot can access a Telegram chat/channel.

    The bot should normally be an administrator in source
    and destination channels.
    """

    try:
        chat = await bot.get_chat(
            chat_id=int(chat_id)
        )

        title = (
            getattr(chat, "title", None)
            or getattr(chat, "first_name", None)
            or getattr(chat, "username", None)
        )

        username = getattr(
            chat,
            "username",
            None,
        )

        return ChatAccessResult(
            ok=True,
            chat_id=int(chat.id),
            title=title,
            username=username,
        )

    except TelegramForbiddenError as exc:

        return ChatAccessResult(
            ok=False,
            chat_id=int(chat_id),
            error=f"Forbidden: {exc}",
        )

    except TelegramBadRequest as exc:

        return ChatAccessResult(
            ok=False,
            chat_id=int(chat_id),
            error=str(exc),
        )

    except TelegramNetworkError as exc:

        return ChatAccessResult(
            ok=False,
            chat_id=int(chat_id),
            error=f"Network error: {exc}",
        )

    except Exception as exc:

        return ChatAccessResult(
            ok=False,
            chat_id=int(chat_id),
            error=str(exc),
        )


# ============================================================
# RETRY HELPER
# ============================================================

async def _retry_sleep(
    attempt: int,
    retry_after: int | float | None = None,
) -> None:

    if retry_after is not None:

        delay = float(retry_after) + 1.0

    else:

        # 2, 4, 8, 16...
        delay = min(
            2 ** attempt,
            30,
        )

    logger.warning(
        "[WARNING] Telegram request retrying in %.1fs",
        delay,
    )

    await asyncio.sleep(delay)


# ============================================================
# COPY SINGLE MESSAGE
# ============================================================

async def copy_message_with_retry(
    bot: Bot,
    destination_chat_id: int,
    source_chat_id: int,
    message_id: int,
    *,
    max_retries: int = 5,
) -> tuple[bool, str | None]:
    """
    Copy one Telegram message.

    Preserves Telegram's original message content including:
    - text
    - photo
    - video
    - document
    - audio
    - animation
    - caption
    - formatting
    - spoilers
    - etc.

    Returns:
        (True, None) on success
        (False, error) on failure
    """

    for attempt in range(max_retries + 1):

        try:

            await bot.copy_message(
                chat_id=int(destination_chat_id),
                from_chat_id=int(source_chat_id),
                message_id=int(message_id),
            )

            return True, None

        except TelegramRetryAfter as exc:

            if attempt >= max_retries:

                return (
                    False,
                    f"Flood limit: retry after {exc.retry_after}s",
                )

            await _retry_sleep(
                attempt,
                exc.retry_after,
            )

        except (
            TelegramNetworkError,
            TelegramServerError,
        ) as exc:

            if attempt >= max_retries:

                return (
                    False,
                    f"Network/server error: {exc}",
                )

            await _retry_sleep(attempt)

        except TelegramForbiddenError as exc:

            return (
                False,
                f"Forbidden: {exc}",
            )

        except TelegramBadRequest as exc:

            return (
                False,
                f"Telegram BadRequest: {exc}",
            )

        except Exception as exc:

            if attempt >= max_retries:

                return (
                    False,
                    str(exc),
                )

            await _retry_sleep(attempt)

    return False, "Unknown copy error"


# ============================================================
# COPY MEDIA GROUP / ALBUM
# ============================================================

async def copy_media_group_with_retry(
    bot: Bot,
    destination_chat_id: int,
    source_chat_id: int,
    message_ids: list[int],
    *,
    max_retries: int = 5,
) -> tuple[bool, str | None]:
    """
    Copy an album/media group.

    message_ids should contain all messages belonging to
    the same Telegram media group.

    Example:
        [100, 101, 102]

    Telegram will preserve the media group structure.
    """

    if not message_ids:

        return False, "No message IDs supplied."

    # Telegram allows copyMessages with a list of message IDs.
    # Keep IDs unique and preserve their original order.

    clean_ids: list[int] = []

    seen: set[int] = set()

    for message_id in message_ids:

        try:
            mid = int(message_id)
        except (TypeError, ValueError):
            continue

        if mid in seen:
            continue

        seen.add(mid)
        clean_ids.append(mid)

    if not clean_ids:

        return False, "No valid message IDs supplied."

    for attempt in range(max_retries + 1):

        try:

            await bot.copy_messages(
                chat_id=int(destination_chat_id),
                from_chat_id=int(source_chat_id),
                message_ids=clean_ids,
            )

            return True, None

        except TelegramRetryAfter as exc:

            if attempt >= max_retries:

                return (
                    False,
                    f"Flood limit: retry after {exc.retry_after}s",
                )

            await _retry_sleep(
                attempt,
                exc.retry_after,
            )

        except (
            TelegramNetworkError,
            TelegramServerError,
        ) as exc:

            if attempt >= max_retries:

                return (
                    False,
                    f"Network/server error: {exc}",
                )

            await _retry_sleep(attempt)

        except TelegramForbiddenError as exc:

            return (
                False,
                f"Forbidden: {exc}",
            )

        except TelegramBadRequest as exc:

            return (
                False,
                f"Telegram BadRequest: {exc}",
            )

        except Exception as exc:

            if attempt >= max_retries:

                return (
                    False,
                    str(exc),
                )

            await _retry_sleep(attempt)

    return False, "Unknown media-group copy error"


# ============================================================
# GENERIC COPY HELPER
# ============================================================

async def copy_post_with_retry(
    bot: Bot,
    destination_chat_id: int,
    post: dict[str, Any],
    *,
    max_retries: int = 5,
) -> tuple[bool, str | None]:
    """
    Generic helper.

    Automatically detects whether the stored post is:
    - a single message
    - an album/media group
    """

    try:

        source_chat_id = int(
            post["source_chat_id"]
        )

        message_ids = [
            int(x)
            for x in post.get(
                "message_ids",
                [],
            )
        ]

    except (
        KeyError,
        TypeError,
        ValueError,
    ) as exc:

        return False, f"Invalid post data: {exc}"

    if not message_ids:

        return False, "Post contains no message IDs."

    is_album = (
        post.get("type") == "album"
        and len(message_ids) > 1
    )

    if is_album:

        return await copy_media_group_with_retry(
            bot,
            destination_chat_id,
            source_chat_id,
            message_ids,
            max_retries=max_retries,
        )

    return await copy_message_with_retry(
        bot,
        destination_chat_id,
        source_chat_id,
        message_ids[0],
        max_retries=max_retries,
    )


# ============================================================
# OPTIONAL: BOT ADMIN CHECK
# ============================================================

async def check_bot_admin(
    bot: Bot,
    chat_id: int,
) -> tuple[bool, str | None]:

    try:

        me = await bot.get_me()

        member = await bot.get_chat_member(
            chat_id=int(chat_id),
            user_id=int(me.id),
        )

        status = str(member.status)

        if status in {
            "administrator",
            "creator",
        }:

            return True, None

        return (
            False,
            f"Bot is not admin. Current status: {status}",
        )

    except TelegramBadRequest as exc:

        return False, str(exc)

    except TelegramForbiddenError as exc:

        return False, str(exc)

    except TelegramNetworkError as exc:

        return False, str(exc)

    except Exception as exc:

        return False, str(exc)
