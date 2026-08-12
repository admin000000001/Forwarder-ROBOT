"""
handlers.py

Telegram command handlers + automatic source channel post capture.

Compatible with:
    - aiogram 3.x
    - storage.py
    - scheduler.py
    - bot.py

Routing:
    Source A -> Destination A1, A2
    Source B -> Destination B1, B2
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

import storage


logger = logging.getLogger("forwarder.handlers")

router = Router()


# ============================================================
# ADMIN
# ============================================================

def get_admin_ids() -> set[int]:
    """
    Read admin IDs from environment.

    Supported:
        ADMIN_IDS=123,456,789
        ADMIN_ID=123
    """

    result: set[int] = set()

    raw = os.getenv("ADMIN_IDS", "").strip()

    if raw:
        for value in raw.split(","):
            value = value.strip()

            if not value:
                continue

            try:
                result.add(int(value))
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid ADMIN_IDS value: %s",
                    value,
                )

    single = os.getenv("ADMIN_ID", "").strip()

    if single:
        try:
            result.add(int(single))
        except (TypeError, ValueError):
            logger.warning(
                "Invalid ADMIN_ID value: %s",
                single,
            )

    return result


def is_admin(message: Message) -> bool:
    """
    Check whether message sender is configured admin.
    """

    if not message.from_user:
        return False

    admin_ids = get_admin_ids()

    return message.from_user.id in admin_ids


async def admin_only(message: Message) -> bool:
    """
    Admin guard.
    """

    if is_admin(message):
        return True

    await message.answer(
        "⛔ <b>Unauthorized</b>\n\n"
        "You are not allowed to use this bot."
    )

    return False


# ============================================================
# SAFE INTEGER HELPERS
# ============================================================

def safe_int(value: Any) -> int | None:
    """
    Safely convert value to int.
    """

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def command_args(message: Message) -> list[str]:
    """
    Return command arguments only.
    """

    text = message.text or ""

    parts = text.strip().split()

    if len(parts) <= 1:
        return []

    return parts[1:]


def parse_single_id(message: Message) -> int | None:
    """
    Parse:
        /command CHAT_ID
    """

    args = command_args(message)

    if len(args) != 1:
        return None

    return safe_int(args[0])


# ============================================================
# START / HELP
# ============================================================

@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:

    if not await admin_only(message):
        return

    text = """
🤖 <b>Forwarder-ROBOT</b>

<b>📡 SOURCES</b>

/addsource CHAT_ID
/removesource CHAT_ID
/sources
/clearsources

<b>📤 DESTINATIONS</b>

/addchannel CHAT_ID
/removechannel CHAT_ID
/channels
/clearchannels

<b>🔀 ROUTES</b>

/addroute SOURCE_ID DEST_ID
/removeroute SOURCE_ID DEST_ID
/routes
/clearoutes

<b>📦 POSTS</b>

/scan
/clearposts

<b>⏱ SCHEDULER</b>

/startschedule
/stopschedule
/status
/next
/setinterval MINUTES
/setsourcemode round_robin
/setsourcemode sequential
/reset

<b>Example</b>

<code>/addsource -1001111111111</code>

<code>/addsource -1002222222222</code>

<code>/addchannel -1003333333333</code>

<code>/addchannel -1004444444444</code>

<code>/addroute -1001111111111 -1003333333333</code>

<code>/addroute -1002222222222 -1004444444444</code>

Then:

<code>/routes</code>

Result:

Source A
└─ Destination A

Source B
└─ Destination B
"""

    await message.answer(text)


# ============================================================
# SOURCES
# ============================================================

@router.message(Command("addsource"))
async def cmd_addsource(
    message: Message,
    bot: Bot,
) -> None:

    if not await admin_only(message):
        return

    chat_id = parse_single_id(message)

    if chat_id is None:

        await message.answer(
            "❌ <b>Invalid command</b>\n\n"
            "Usage:\n"
            "<code>/addsource -1001234567890</code>"
        )

        return

    # --------------------------------------------------------
    # Verify Telegram chat
    # --------------------------------------------------------

    try:

        chat = await bot.get_chat(chat_id)

    except Exception as exc:

        logger.exception(
            "Source get_chat failed for %s: %s",
            chat_id,
            exc,
        )

        await message.answer(
            "❌ <b>Source channel access failed</b>\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            "Check that:\n"
            "• Chat ID is correct\n"
            "• Bot is added to the source channel\n"
            "• Bot has permission to receive channel posts"
        )

        return

    # --------------------------------------------------------
    # Make sure it is a channel
    # --------------------------------------------------------

    if getattr(chat, "type", None) != "channel":

        await message.answer(
            "❌ This chat is not a Telegram channel.\n\n"
            f"Detected type: <code>{chat.type}</code>"
        )

        return

    # --------------------------------------------------------
    # Save source
    # --------------------------------------------------------

    try:

        ok, result = await storage.add_source(
            chat_id=chat_id,
            title=chat.title or str(chat_id),
            username=chat.username,
        )

    except Exception as exc:

        logger.exception(
            "storage.add_source failed: %s",
            exc,
        )

        await message.answer(
            "❌ Could not save source.\n\n"
            f"<code>{exc}</code>"
        )

        return

    if ok:

        await message.answer(
            "✅ <b>Source added successfully</b>\n\n"
            f"📡 <b>{chat.title or 'Untitled'}</b>\n"
            f"🆔 <code>{chat_id}</code>\n"
            f"🔗 @{chat.username}"
            if chat.username
            else
            "✅ <b>Source added successfully</b>\n\n"
            f"📡 <b>{chat.title or 'Untitled'}</b>\n"
            f"🆔 <code>{chat_id}</code>"
        )

        logger.info(
            "Source added: %s (%s)",
            chat.title,
            chat_id,
        )

    else:

        await message.answer(
            "ℹ️ <b>Source</b>\n\n"
            f"{result}\n\n"
            f"🆔 <code>{chat_id}</code>"
        )


@router.message(Command("removesource"))
async def cmd_removesource(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    chat_id = parse_single_id(message)

    if chat_id is None:

        await message.answer(
            "Usage:\n"
            "<code>/removesource -1001234567890</code>"
        )

        return

    try:

        removed = await storage.remove_source(chat_id)

    except Exception as exc:

        logger.exception(
            "remove_source failed: %s",
            exc,
        )

        await message.answer(
            f"❌ Error: <code>{exc}</code>"
        )

        return

    if removed:

        await message.answer(
            "✅ Source removed."
        )

    else:

        await message.answer(
            "⚠️ Source not found."
        )


@router.message(Command("sources"))
async def cmd_sources(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:
        sources = await storage.get_sources()
    except Exception as exc:
        await message.answer(
            f"❌ Could not load sources:\n<code>{exc}</code>"
        )
        return

    if not sources:

        await message.answer(
            "📡 <b>Sources</b>\n\n"
            "No sources configured."
        )

        return

    lines = [
        "📡 <b>Configured Sources</b>",
        "",
    ]

    for index, source in enumerate(sources, 1):

        chat_id = source.get("chat_id")
        title = source.get("title") or "Unknown"
        username = source.get("username")

        enabled = source.get("enabled", True)

        status = "🟢" if enabled else "🔴"

        lines.append(
            f"{index}. {status} <b>{title}</b>"
        )

        lines.append(
            f"   🆔 <code>{chat_id}</code>"
        )

        if username:
            lines.append(
                f"   🔗 @{username}"
            )

        lines.append("")

    await message.answer(
        "\n".join(lines)
    )


@router.message(Command("clearsources"))
async def cmd_clearsources(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:
        await storage.clear_sources()
    except Exception as exc:
        await message.answer(
            f"❌ Error:\n<code>{exc}</code>"
        )
        return

    await message.answer(
        "✅ <b>All sources cleared.</b>\n\n"
        "Associated routes were also requested to be cleared."
    )


# ============================================================
# DESTINATIONS
# ============================================================

@router.message(Command("addchannel"))
async def cmd_addchannel(
    message: Message,
    bot: Bot,
) -> None:

    if not await admin_only(message):
        return

    chat_id = parse_single_id(message)

    if chat_id is None:

        await message.answer(
            "❌ <b>Invalid command</b>\n\n"
            "Usage:\n"
            "<code>/addchannel -1001234567890</code>"
        )

        return

    try:

        chat = await bot.get_chat(chat_id)

    except Exception as exc:

        logger.exception(
            "Destination get_chat failed for %s: %s",
            chat_id,
            exc,
        )

        await message.answer(
            "❌ <b>Destination access failed</b>\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            "Make sure the bot is an administrator "
            "of the destination channel."
        )

        return

    if getattr(chat, "type", None) != "channel":

        await message.answer(
            "❌ This chat is not a Telegram channel.\n\n"
            f"Detected type: <code>{chat.type}</code>"
        )

        return

    try:

        ok, result = await storage.add_channel(
            chat_id=chat_id,
            title=chat.title or str(chat_id),
            username=chat.username,
        )

    except Exception as exc:

        logger.exception(
            "storage.add_channel failed: %s",
            exc,
        )

        await message.answer(
            "❌ Could not save destination.\n\n"
            f"<code>{exc}</code>"
        )

        return

    if ok:

        text = (
            "✅ <b>Destination added successfully</b>\n\n"
            f"📡 <b>{chat.title or 'Untitled'}</b>\n"
            f"🆔 <code>{chat_id}</code>"
        )

        if chat.username:
            text += f"\n🔗 @{chat.username}"

        await message.answer(text)

        logger.info(
            "Destination added: %s (%s)",
            chat.title,
            chat_id,
        )

    else:

        await message.answer(
            f"ℹ️ {result}"
        )


@router.message(Command("removechannel"))
async def cmd_removechannel(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    chat_id = parse_single_id(message)

    if chat_id is None:

        await message.answer(
            "Usage:\n"
            "<code>/removechannel -1001234567890</code>"
        )

        return

    try:
        removed = await storage.remove_channel(chat_id)
    except Exception as exc:
        await message.answer(
            f"❌ Error:\n<code>{exc}</code>"
        )
        return

    await message.answer(
        "✅ Destination removed."
        if removed
        else "⚠️ Destination not found."
    )


@router.message(Command("channels"))
async def cmd_channels(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:
        channels = await storage.get_channels()
    except Exception as exc:
        await message.answer(
            f"❌ Error:\n<code>{exc}</code>"
        )
        return

    if not channels:

        await message.answer(
            "📤 <b>Destinations</b>\n\n"
            "No destinations configured."
        )

        return

    lines = [
        "📤 <b>Configured Destinations</b>",
        "",
    ]

    for index, channel in enumerate(channels, 1):

        chat_id = channel.get("chat_id")
        title = channel.get("title") or "Unknown"
        username = channel.get("username")

        enabled = channel.get("enabled", True)

        status = "🟢" if enabled else "🔴"

        lines.append(
            f"{index}. {status} <b>{title}</b>"
        )

        lines.append(
            f"   🆔 <code>{chat_id}</code>"
        )

        if username:
            lines.append(
                f"   🔗 @{username}"
            )

        lines.append("")

    await message.answer(
        "\n".join(lines)
    )


@router.message(Command("clearchannels"))
async def cmd_clearchannels(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:
        await storage.clear_channels()
    except Exception as exc:
        await message.answer(
            f"❌ Error:\n<code>{exc}</code>"
        )
        return

    await message.answer(
        "✅ <b>All destinations cleared.</b>"
    )


# ============================================================
# ROUTES
# ============================================================

@router.message(Command("addroute"))
async def cmd_addroute(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    args = command_args(message)

    if len(args) != 2:

        await message.answer(
            "❌ <b>Invalid command</b>\n\n"
            "Usage:\n"
            "<code>/addroute SOURCE_ID DEST_ID</code>\n\n"
            "Example:\n"
            "<code>/addroute -1001111111111 -1002222222222</code>"
        )

        return

    source_id = safe_int(args[0])
    destination_id = safe_int(args[1])

    if source_id is None or destination_id is None:

        await message.answer(
            "❌ Invalid source or destination ID."
        )

        return

    # --------------------------------------------------------
    # Verify source exists
    # --------------------------------------------------------

    try:
        sources = await storage.get_sources()
    except Exception as exc:
        await message.answer(
            f"❌ Could not load sources:\n<code>{exc}</code>"
        )
        return

    source_exists = False

    for source in sources:

        sid = safe_int(
            source.get("chat_id")
        )

        if sid == source_id:
            source_exists = True
            break

    if not source_exists:

        await message.answer(
            "❌ <b>Source not configured.</b>\n\n"
            "First add it:\n"
            f"<code>/addsource {source_id}</code>"
        )

        return

    # --------------------------------------------------------
    # Verify destination exists
    # --------------------------------------------------------

    try:
        channels = await storage.get_channels()
    except Exception as exc:
        await message.answer(
            f"❌ Could not load destinations:\n<code>{exc}</code>"
        )
        return

    destination_exists = False

    for channel in channels:

        cid = safe_int(
            channel.get("chat_id")
        )

        if cid == destination_id:
            destination_exists = True
            break

    if not destination_exists:

        await message.answer(
            "❌ <b>Destination not configured.</b>\n\n"
            "First add it:\n"
            f"<code>/addchannel {destination_id}</code>"
        )

        return

    # --------------------------------------------------------
    # Save route
    # --------------------------------------------------------

    try:

        ok, result = await storage.add_route(
            source_id,
            destination_id,
        )

    except Exception as exc:

        logger.exception(
            "add_route failed: %s",
            exc,
        )

        await message.answer(
            "❌ Could not create route.\n\n"
            f"<code>{exc}</code>"
        )

        return

    if ok:

        await message.answer(
            "✅ <b>Route added</b>\n\n"
            f"📡 Source\n"
            f"<code>{source_id}</code>\n\n"
            f"└── 📤 Destination\n"
            f"<code>{destination_id}</code>"
        )

    else:

        await message.answer(
            f"ℹ️ {result}"
        )


@router.message(Command("removeroute"))
async def cmd_removeroute(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    args = command_args(message)

    if len(args) != 2:

        await message.answer(
            "Usage:\n"
            "<code>/removeroute SOURCE_ID DEST_ID</code>"
        )

        return

    source_id = safe_int(args[0])
    destination_id = safe_int(args[1])

    if source_id is None or destination_id is None:

        await message.answer(
            "❌ Invalid chat ID."
        )

        return

    try:

        removed = await storage.remove_route(
            source_id,
            destination_id,
        )

    except Exception as exc:

        await message.answer(
            f"❌ Error:\n<code>{exc}</code>"
        )

        return

    await message.answer(
        "✅ Route removed."
        if removed
        else "⚠️ Route not found."
    )


@router.message(Command("routes"))
async def cmd_routes(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:
        routes = await storage.get_routes()
    except Exception as exc:
        await message.answer(
            f"❌ Could not load routes:\n<code>{exc}</code>"
        )
        return

    if not routes:

        await message.answer(
            "🔀 <b>Routes</b>\n\n"
            "No routes configured."
        )

        return

    lines = [
        "🔀 <b>Source → Destinations</b>",
        "",
    ]

    for route in routes:

        source_id = route.get(
            "source_id"
        )

        destinations = route.get(
            "destinations",
            [],
        )

        lines.append(
            f"📡 <b>Source:</b> "
            f"<code>{source_id}</code>"
        )

        if destinations:

            for destination_id in destinations:

                lines.append(
                    f"   └── 📤 "
                    f"<code>{destination_id}</code>"
                )

        else:

            lines.append(
                "   └── ⚠️ No destinations"
            )

        lines.append("")

    await message.answer(
        "\n".join(lines)
    )


@router.message(Command("clearoutes"))
async def cmd_clearoutes(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:
        await storage.clear_routes()
    except Exception as exc:
        await message.answer(
            f"❌ Error:\n<code>{exc}</code>"
        )
        return

    await message.answer(
        "✅ <b>All routes cleared.</b>"
    )


# ============================================================
# CHANNEL POST CAPTURE
# ============================================================

async def capture_channel_post(
    message: Message,
) -> None:
    """
    Capture new posts from configured source channels.

    Important:
        Telegram sends channel posts through channel_post,
        not normal message updates.
    """

    if not message.chat:
        return

    source_id = safe_int(
        message.chat.id
    )

    if source_id is None:
        return

    # --------------------------------------------------------
    # Check source configuration
    # --------------------------------------------------------

    try:
        sources = await storage.get_sources()
    except Exception as exc:
        logger.error(
            "Could not load sources while capturing post: %s",
            exc,
        )
        return

    configured = False

    for source in sources:

        sid = safe_int(
            source.get("chat_id")
        )

        if (
            sid == source_id
            and source.get("enabled", True)
        ):
            configured = True
            break

    if not configured:
        return

    # ========================================================
    # MEDIA GROUP / ALBUM
    # ========================================================

    media_group_id = message.media_group_id

    if media_group_id:

        # ----------------------------------------------------
        # Give Telegram time to deliver the remaining album
        # messages.
        # ----------------------------------------------------

        await asyncio.sleep(1.2)

        try:
            posts = await storage.get_posts()
        except Exception as exc:
            logger.error(
                "Could not load posts for album: %s",
                exc,
            )
            return

        target = None

        for post in posts:

            post_source = safe_int(
                post.get("source_chat_id")
            )

            if post_source != source_id:
                continue

            if str(
                post.get("media_group_id")
            ) != str(media_group_id):
                continue

            target = post
            break

        # ----------------------------------------------------
        # Existing album
        # ----------------------------------------------------

        if target is not None:

            message_ids = []

            for mid in target.get(
                "message_ids",
                [],
            ):

                value = safe_int(mid)

                if value is not None:
                    message_ids.append(value)

            if message.message_id not in message_ids:

                message_ids.append(
                    message.message_id
                )

                message_ids = sorted(
                    set(message_ids)
                )

                target["message_ids"] = message_ids

                # Keep first message as message_id
                if message_ids:
                    target["message_id"] = message_ids[0]

                await storage.save_posts(
                    posts
                )

                logger.info(
                    "Album updated: source=%s group=%s ids=%s",
                    source_id,
                    media_group_id,
                    message_ids,
                )

            return

        # ----------------------------------------------------
        # New album
        # ----------------------------------------------------

        new_post = {
            "source_chat_id": source_id,
            "message_ids": [
                message.message_id
            ],
            "message_id": message.message_id,
            "type": "album",
            "media_group_id": media_group_id,
            "caption": (
                message.caption
                or message.text
            ),
            "created_at": storage.now_iso(),
        }

        posts.append(new_post)

        try:
            await storage.save_posts(posts)
        except Exception as exc:
            logger.error(
                "Could not save new album: %s",
                exc,
            )
            return

        logger.info(
            "New album captured: source=%s group=%s message=%s",
            source_id,
            media_group_id,
            message.message_id,
        )

        return

    # ========================================================
    # NORMAL MESSAGE
    # ========================================================

    message_type = "text"

    if message.video:
        message_type = "video"

    elif message.photo:
        message_type = "photo"

    elif message.document:
        message_type = "document"

    elif message.audio:
        message_type = "audio"

    elif message.animation:
        message_type = "animation"

    elif message.voice:
        message_type = "voice"

    elif message.video_note:
        message_type = "video_note"

    elif message.sticker:
        message_type = "sticker"

    elif message.contact:
        message_type = "contact"

    elif message.location:
        message_type = "location"

    elif message.poll:
        message_type = "poll"

    caption = (
        message.caption
        or message.text
    )

    try:

        added = await storage.add_post(
            source_chat_id=source_id,
            message_id=message.message_id,
            message_type=message_type,
            caption=caption,
        )

    except Exception as exc:

        logger.exception(
            "Could not save channel post: %s",
            exc,
        )

        return

    if added:

        logger.info(
            "New post captured: "
            "source=%s message=%s type=%s",
            source_id,
            message.message_id,
            message_type,
        )


# IMPORTANT:
# Telegram channel posts arrive through channel_post.
router.channel_post()(capture_channel_post)


# ============================================================
# SCAN
# ============================================================

@router.message(Command("scan"))
async def cmd_scan(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:

        sources = await storage.get_sources()
        posts = await storage.get_posts()

    except Exception as exc:

        await message.answer(
            f"❌ Database error:\n<code>{exc}</code>"
        )

        return

    source_ids = set()

    for source in sources:

        source_id = safe_int(
            source.get("chat_id")
        )

        if source_id is not None:
            source_ids.add(source_id)

    configured_posts = []

    for post in posts:

        source_id = safe_int(
            post.get("source_chat_id")
        )

        if source_id in source_ids:
            configured_posts.append(post)

    await message.answer(
        "📊 <b>Post Database</b>\n\n"
        f"Sources: <b>{len(sources)}</b>\n"
        f"Posts: <b>{len(configured_posts)}</b>\n\n"
        "ℹ️ New source channel posts are captured "
        "automatically while the bot has access."
    )


# ============================================================
# CLEAR POSTS
# ============================================================

@router.message(Command("clearposts"))
async def cmd_clearposts(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:

        await storage.clear_posts()

        schedule = await storage.get_schedule()

        schedule["current_index"] = 0
        schedule["next_run_iso"] = None
        schedule["last_completed_iso"] = None

        await storage.save_schedule(
            schedule
        )

    except Exception as exc:

        await message.answer(
            f"❌ Error:\n<code>{exc}</code>"
        )

        return

    await message.answer(
        "✅ <b>Post database cleared.</b>\n\n"
        "Scheduler position has also been reset."
    )


# ============================================================
# SCHEDULER HELPERS
# ============================================================

def get_scheduler_from_dispatcher(
    message: Message,
) -> Any | None:
    """
    Retrieve scheduler from dispatcher workflow data.

    register_handlers() stores it as:
        dp['scheduler']
    """

    try:

        dispatcher = message.bot

        # Aiogram workflow data is normally injected into
        # handler arguments automatically. This helper exists
        # mainly for compatibility.

        return None

    except Exception:
        return None


# ============================================================
# START SCHEDULER
# ============================================================

@router.message(Command("startschedule"))
async def cmd_startschedule(
    message: Message,
    scheduler: Any,
) -> None:

    if not await admin_only(message):
        return

    if scheduler is None:

        await message.answer(
            "❌ Scheduler dependency unavailable."
        )

        return

    try:

        ok, text = await scheduler.start()

    except Exception as exc:

        logger.exception(
            "Scheduler start failed: %s",
            exc,
        )

        await message.answer(
            "❌ Scheduler failed to start.\n\n"
            f"<code>{exc}</code>"
        )

        return

    await message.answer(
        ("▶️ " if ok else "ℹ️ ")
        + text
    )


# ============================================================
# STOP SCHEDULER
# ============================================================

@router.message(Command("stopschedule"))
async def cmd_stopschedule(
    message: Message,
    scheduler: Any,
) -> None:

    if not await admin_only(message):
        return

    if scheduler is None:

        await message.answer(
            "❌ Scheduler dependency unavailable."
        )

        return

    try:

        ok, text = await scheduler.stop()

    except Exception as exc:

        logger.exception(
            "Scheduler stop failed: %s",
            exc,
        )

        await message.answer(
            "❌ Scheduler failed to stop.\n\n"
            f"<code>{exc}</code>"
        )

        return

    await message.answer(
        ("⏹️ " if ok else "ℹ️ ")
        + text
    )


# ============================================================
# STATUS
# ============================================================

@router.message(Command("status"))
async def cmd_status(
    message: Message,
    scheduler: Any,
) -> None:

    if not await admin_only(message):
        return

    try:

        schedule = await storage.get_schedule()
        settings = await storage.get_settings()

        sources = await storage.get_sources()
        channels = await storage.get_channels()
        routes = await storage.get_routes()
        posts = await storage.get_posts()

    except Exception as exc:

        await message.answer(
            f"❌ Could not load status:\n<code>{exc}</code>"
        )

        return

    running = False

    try:
        running = scheduler.is_running()
    except Exception:
        pass

    await message.answer(
        "📊 <b>Forwarder Status</b>\n\n"
        f"Scheduler: "
        f"<b>{'🟢 RUNNING' if running else '🔴 STOPPED'}</b>\n\n"
        f"📡 Sources: <b>{len(sources)}</b>\n"
        f"📤 Destinations: <b>{len(channels)}</b>\n"
        f"🔀 Routes: <b>{len(routes)}</b>\n"
        f"📦 Posts: <b>{len(posts)}</b>\n\n"
        f"⏱ Interval: "
        f"<b>{settings.get('interval_minutes')}</b> min\n"
        f"🔄 Mode: "
        f"<b>{settings.get('source_mode')}</b>\n"
        f"📍 Current index: "
        f"<b>{schedule.get('current_index', 0)}</b>\n"
        f"⏭ Next run:\n"
        f"<code>{schedule.get('next_run_iso') or 'Not scheduled'}</code>"
    )


# ============================================================
# NEXT
# ============================================================

@router.message(Command("next"))
async def cmd_next(
    message: Message,
    scheduler: Any,
) -> None:

    if not await admin_only(message):
        return

    if scheduler is None:

        await message.answer(
            "❌ Scheduler dependency unavailable."
        )

        return

    try:

        queue = await scheduler.build_queue()

    except Exception as exc:

        await message.answer(
            f"❌ Could not build queue:\n<code>{exc}</code>"
        )

        return

    if not queue:

        await message.answer(
            "📭 <b>Queue is empty.</b>"
        )

        return

    try:

        schedule = await storage.get_schedule()

        index = int(
            schedule.get(
                "current_index",
                0,
            )
            or 0
        )

    except Exception:

        index = 0

    index %= len(queue)

    post = queue[index]

    await message.answer(
        "⏭️ <b>Next Post</b>\n\n"
        f"📡 Source:\n"
        f"<code>{post.get('source_chat_id')}</code>\n\n"
        f"🆔 Message IDs:\n"
        f"<code>{post.get('message_ids')}</code>\n\n"
        f"📦 Type:\n"
        f"<b>{post.get('type', 'unknown')}</b>\n\n"
        f"📍 Position:\n"
        f"<b>{index + 1}/{len(queue)}</b>"
    )


# ============================================================
# INTERVAL
# ============================================================

@router.message(Command("setinterval"))
async def cmd_setinterval(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    args = command_args(message)

    if len(args) != 1:

        await message.answer(
            "Usage:\n"
            "<code>/setinterval 10</code>"
        )

        return

    try:

        minutes = float(args[0])

    except (TypeError, ValueError):

        await message.answer(
            "❌ Invalid interval."
        )

        return

    if minutes <= 0:

        await message.answer(
            "❌ Interval must be greater than 0."
        )

        return

    try:

        settings = await storage.get_settings()

        settings["interval_minutes"] = minutes

        await storage.save_settings(
            settings
        )

    except Exception as exc:

        await message.answer(
            f"❌ Could not save interval:\n<code>{exc}</code>"
        )

        return

    await message.answer(
        "✅ <b>Interval updated</b>\n\n"
        f"⏱ <b>{minutes}</b> minutes"
    )


# ============================================================
# SOURCE MODE
# ============================================================

@router.message(Command("setsourcemode"))
async def cmd_setsourcemode(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    args = command_args(message)

    if len(args) != 1:

        await message.answer(
            "Usage:\n\n"
            "<code>/setsourcemode round_robin</code>\n"
            "<code>/setsourcemode sequential</code>"
        )

        return

    mode = args[0].lower()

    if mode not in {
        "round_robin",
        "sequential",
    }:

        await message.answer(
            "❌ Invalid mode.\n\n"
            "Allowed:\n"
            "• round_robin\n"
            "• sequential"
        )

        return

    try:

        settings = await storage.get_settings()

        settings["source_mode"] = mode

        await storage.save_settings(
            settings
        )

    except Exception as exc:

        await message.answer(
            f"❌ Could not save mode:\n<code>{exc}</code>"
        )

        return

    await message.answer(
        "✅ <b>Source mode updated</b>\n\n"
        f"🔄 <b>{mode}</b>"
    )


# ============================================================
# RESET
# ============================================================

@router.message(Command("reset"))
async def cmd_reset(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    try:

        schedule = await storage.get_schedule()

        schedule["current_index"] = 0
        schedule["next_run_iso"] = None
        schedule["last_completed_iso"] = None

        await storage.save_schedule(
            schedule
        )

    except Exception as exc:

        await message.answer(
            f"❌ Reset failed:\n<code>{exc}</code>"
        )

        return

    await message.answer(
        "✅ <b>Scheduler position reset.</b>"
    )


# ============================================================
# REGISTER HANDLERS
# ============================================================

def register_handlers(
    dp: Dispatcher,
    bot: Bot,
    scheduler: Any,
) -> None:
    """
    Register all handlers.

    Important:
        Scheduler is placed into Dispatcher workflow data so
        handlers can receive:

            scheduler: Any

        automatically.
    """

    # --------------------------------------------------------
    # Include router
    # --------------------------------------------------------

    dp.include_router(router)

    # --------------------------------------------------------
    # Dispatcher workflow data
    # --------------------------------------------------------

    dp["bot"] = bot
    dp["scheduler"] = scheduler

    logger.info(
        "Handlers registered successfully."
        )
