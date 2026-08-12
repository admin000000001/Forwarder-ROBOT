"""
handlers.py

Telegram command handlers + automatic channel post capture.

Features:
- Fixed owner authorization
- Optional additional admins
- Source management
- Destination management
- Source -> Destination routes
- Automatic channel post capture
- Media album capture
- Scheduler controls
- Status / queue information
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
# OWNER / ADMIN
# ============================================================

# Your Telegram USER ID
OWNER_ADMIN_ID = 8753552605


def get_admin_ids() -> set[int]:
    """
    Return all authorized Telegram user IDs.

    OWNER_ADMIN_ID is always authorized.

    Optional Render environment variables:

        ADMIN_ID=123456789

    or:

        ADMIN_IDS=123456789,987654321
    """

    admin_ids: set[int] = {
        OWNER_ADMIN_ID
    }

    # --------------------------------------------------------
    # Optional single admin
    # --------------------------------------------------------

    raw_single = os.getenv(
        "ADMIN_ID",
        "",
    ).strip()

    if raw_single:

        try:
            admin_ids.add(
                int(raw_single)
            )

        except ValueError:

            logger.warning(
                "Invalid ADMIN_ID environment variable: %s",
                raw_single,
            )

    # --------------------------------------------------------
    # Optional multiple admins
    # --------------------------------------------------------

    raw_multiple = os.getenv(
        "ADMIN_IDS",
        "",
    ).strip()

    if raw_multiple:

        for value in raw_multiple.split(","):

            value = value.strip()

            if not value:
                continue

            try:

                admin_ids.add(
                    int(value)
                )

            except ValueError:

                logger.warning(
                    "Invalid ADMIN_IDS value: %s",
                    value,
                )

    return admin_ids


def is_admin(message: Message) -> bool:
    """
    Check whether the message sender is authorized.
    """

    if not message.from_user:
        return False

    user_id = int(
        message.from_user.id
    )

    return user_id in get_admin_ids()


async def admin_only(
    message: Message,
) -> bool:
    """
    Protect admin commands.
    """

    if is_admin(message):
        return True

    logger.warning(
        "Unauthorized command attempt: user_id=%s command=%s",
        message.from_user.id
        if message.from_user
        else "unknown",
        message.text or "",
    )

    await message.answer(
        "⛔ Unauthorized\n\n"
        "You are not allowed to use this bot."
    )

    return False


# ============================================================
# HELPERS
# ============================================================

def parse_id(
    message: Message,
) -> int | None:
    """
    Parse a single Telegram chat ID.

    Example:

        /addsource -1001234567890
    """

    text = message.text or ""

    parts = text.split()

    if len(parts) != 2:
        return None

    try:
        return int(parts[1])

    except (TypeError, ValueError):
        return None


def safe_int(
    value: Any,
) -> int | None:
    """
    Safely convert value to int.
    """

    try:
        return int(value)

    except (TypeError, ValueError):
        return None


# ============================================================
# START / HELP
# ============================================================

@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_help(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    text = """
🤖 <b>Forwarder-ROBOT</b>

<b>👑 Admin</b>
Your account is authorized.

<b>📡 Sources</b>

/addsource CHAT_ID
/removesource CHAT_ID
/sources
/clearsources

<b>📢 Destinations</b>

/addchannel CHAT_ID
/removechannel CHAT_ID
/channels
/clearchannels

<b>🔀 Routes</b>

/addroute SOURCE_ID DEST_ID
/removeroute SOURCE_ID DEST_ID
/routes
/clearoutes

<b>📦 Posts</b>

/scan
/clearposts

<b>⏱ Scheduler</b>

/startschedule
/stopschedule
/status
/next
/setinterval MINUTES
/setsourcemode round_robin
/setsourcemode sequential
/reset

<b>Example</b>

<code>/addsource -1003407857559</code>

<code>/addsource -1004488672586</code>

<code>/addchannel -1003967093162</code>

<code>/addchannel -1004369290699</code>

<code>/addroute -1003407857559 -1003967093162</code>

<code>/addroute -1004488672586 -1004369290699</code>

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

    chat_id = parse_id(message)

    if chat_id is None:

        await message.answer(
            "❌ Invalid command.\n\n"
            "Usage:\n"
            "<code>/addsource -1001234567890</code>"
        )

        return

    # --------------------------------------------------------
    # Telegram verification
    # --------------------------------------------------------

    try:

        chat = await bot.get_chat(
            chat_id
        )

    except Exception as exc:

        logger.error(
            "Source get_chat failed for %s: %s",
            chat_id,
            exc,
        )

        await message.answer(
            "❌ <b>Source channel access failed.</b>\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            "Make sure:\n"
            "• The ID is correct\n"
            "• The bot is added to the source channel\n"
            "• The bot has permission to read channel posts"
        )

        return

    # --------------------------------------------------------
    # Save source
    # --------------------------------------------------------

    try:

        ok, result = await storage.add_source(
            chat_id=chat_id,
            title=chat.title,
            username=chat.username,
        )

    except Exception as exc:

        logger.exception(
            "storage.add_source failed"
        )

        await message.answer(
            "❌ Could not save source.\n\n"
            f"<code>{exc}</code>"
        )

        return

    if ok:

        await message.answer(
            "✅ <b>Source added successfully</b>\n\n"
            f"📡 {chat.title or 'Unknown'}\n"
            f"🆔 <code>{chat_id}</code>"
        )

        logger.info(
            "Source added: %s (%s)",
            chat.title,
            chat_id,
        )

    else:

        await message.answer(
            f"ℹ️ {result}\n\n"
            f"📡 <code>{chat_id}</code>"
        )


@router.message(Command("removesource"))
async def cmd_removesource(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    chat_id = parse_id(message)

    if chat_id is None:

        await message.answer(
            "Usage:\n"
            "<code>/removesource -1001234567890</code>"
        )

        return

    try:

        removed = await storage.remove_source(
            chat_id
        )

    except Exception as exc:

        logger.exception(
            "remove_source failed"
        )

        await message.answer(
            f"❌ Error: <code>{exc}</code>"
        )

        return

    if removed:

        await message.answer(
            "✅ Source removed.\n\n"
            f"<code>{chat_id}</code>"
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

    sources = await storage.get_sources()

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

    for index, source in enumerate(
        sources,
        1,
    ):

        chat_id = source.get(
            "chat_id"
        )

        title = source.get(
            "title"
        ) or "Unknown"

        username = source.get(
            "username"
        )

        enabled = source.get(
            "enabled",
            True,
        )

        status = (
            "🟢"
            if enabled
            else "🔴"
        )

        lines.append(
            f"{index}. {status} <b>{title}</b>"
        )

        lines.append(
            f"   🆔 <code>{chat_id}</code>"
        )

        if username:

            lines.append(
                f"   👤 @{username}"
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

        logger.exception(
            "clear_sources failed"
        )

        await message.answer(
            f"❌ Error: <code>{exc}</code>"
        )

        return

    await message.answer(
        "✅ All sources and their routes cleared."
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

    chat_id = parse_id(message)

    if chat_id is None:

        await message.answer(
            "❌ Invalid command.\n\n"
            "Usage:\n"
            "<code>/addchannel -1001234567890</code>"
        )

        return

    try:

        chat = await bot.get_chat(
            chat_id
        )

    except Exception as exc:

        logger.error(
            "Destination get_chat failed for %s: %s",
            chat_id,
            exc,
        )

        await message.answer(
            "❌ <b>Destination channel access failed.</b>\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            "Make sure the bot is an administrator "
            "of the destination channel."
        )

        return

    try:

        ok, result = await storage.add_channel(
            chat_id=chat_id,
            title=chat.title,
            username=chat.username,
        )

    except Exception as exc:

        logger.exception(
            "storage.add_channel failed"
        )

        await message.answer(
            f"❌ Could not save destination.\n\n"
            f"<code>{exc}</code>"
        )

        return

    if ok:

        await message.answer(
            "✅ <b>Destination added</b>\n\n"
            f"📡 {chat.title or 'Unknown'}\n"
            f"🆔 <code>{chat_id}</code>"
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

    chat_id = parse_id(message)

    if chat_id is None:

        await message.answer(
            "Usage:\n"
            "<code>/removechannel -1001234567890</code>"
        )

        return

    removed = await storage.remove_channel(
        chat_id
    )

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

    channels = await storage.get_channels()

    if not channels:

        await message.answer(
            "📢 <b>Destinations</b>\n\n"
            "No destinations configured."
        )

        return

    lines = [
        "📢 <b>Configured Destinations</b>",
        "",
    ]

    for index, channel in enumerate(
        channels,
        1,
    ):

        chat_id = channel.get(
            "chat_id"
        )

        title = channel.get(
            "title"
        ) or "Unknown"

        username = channel.get(
            "username"
        )

        enabled = channel.get(
            "enabled",
            True,
        )

        status = (
            "🟢"
            if enabled
            else "🔴"
        )

        lines.append(
            f"{index}. {status} <b>{title}</b>"
        )

        lines.append(
            f"   🆔 <code>{chat_id}</code>"
        )

        if username:

            lines.append(
                f"   👤 @{username}"
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

    await storage.clear_channels()

    await message.answer(
        "✅ All destinations cleared."
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

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 3:

        await message.answer(
            "Usage:\n"
            "<code>/addroute SOURCE_ID DEST_ID</code>\n\n"
            "Example:\n"
            "<code>/addroute "
            "-1003407857559 "
            "-1003967093162</code>"
        )

        return

    try:

        source_id = int(
            parts[1]
        )

        destination_id = int(
            parts[2]
        )

    except ValueError:

        await message.answer(
            "❌ Invalid chat ID."
        )

        return

    sources = await storage.get_sources()
    channels = await storage.get_channels()

    # --------------------------------------------------------
    # Check source
    # --------------------------------------------------------

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
            "❌ <b>Source is not configured.</b>\n\n"
            "First add it:\n"
            f"<code>/addsource {source_id}</code>"
        )

        return

    # --------------------------------------------------------
    # Check destination
    # --------------------------------------------------------

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
            "❌ <b>Destination is not configured.</b>\n\n"
            "First add it:\n"
            f"<code>/addchannel {destination_id}</code>"
        )

        return

    # --------------------------------------------------------
    # Add route
    # --------------------------------------------------------

    try:

        ok, result = await storage.add_route(
            source_id,
            destination_id,
        )

    except Exception as exc:

        logger.exception(
            "add_route failed"
        )

        await message.answer(
            "❌ Could not create route.\n\n"
            f"<code>{exc}</code>"
        )

        return

    if ok:

        await message.answer(
            "✅ <b>Route added</b>\n\n"
            f"📡 Source:\n"
            f"<code>{source_id}</code>\n\n"
            f"└─ 📢 Destination:\n"
            f"<code>{destination_id}</code>"
        )

        logger.info(
            "Route added: %s -> %s",
            source_id,
            destination_id,
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

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 3:

        await message.answer(
            "Usage:\n"
            "<code>/removeroute SOURCE_ID DEST_ID</code>"
        )

        return

    try:

        source_id = int(parts[1])
        destination_id = int(parts[2])

    except ValueError:

        await message.answer(
            "❌ Invalid chat ID."
        )

        return

    removed = await storage.remove_route(
        source_id,
        destination_id,
    )

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

    routes = await storage.get_routes()

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

            for destination in destinations:

                lines.append(
                    f"   └─ 📢 "
                    f"<code>{destination}</code>"
                )

        else:

            lines.append(
                "   └─ ⚠️ No destinations"
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

    await storage.clear_routes()

    await message.answer(
        "✅ All routes cleared."
    )


# ============================================================
# CHANNEL POST CAPTURE
# ============================================================

async def capture_channel_post(
    message: Message,
) -> None:
    """
    Automatically capture new posts from configured sources.
    """

    if not message.chat:
        return

    source_id = int(
        message.chat.id
    )

    sources = await storage.get_sources()

    # --------------------------------------------------------
    # Verify source is configured
    # --------------------------------------------------------

    configured = False

    for source in sources:

        sid = safe_int(
            source.get("chat_id")
        )

        if (
            sid == source_id
            and source.get(
                "enabled",
                True,
            )
        ):

            configured = True
            break

    if not configured:
        return

    # ========================================================
    # MEDIA GROUP / ALBUM
    # ========================================================

    if message.media_group_id:

        await asyncio.sleep(1.0)

        posts = await storage.get_posts()

        target = None

        for post in posts:

            post_source = safe_int(
                post.get(
                    "source_chat_id"
                )
            )

            if post_source != source_id:
                continue

            if (
                post.get(
                    "media_group_id"
                )
                == message.media_group_id
            ):

                target = post
                break

        # ----------------------------------------------------
        # Existing album
        # ----------------------------------------------------

        if target is not None:

            ids = []

            for value in target.get(
                "message_ids",
                [],
            ):

                converted = safe_int(
                    value
                )

                if converted is not None:
                    ids.append(converted)

            if message.message_id not in ids:

                ids.append(
                    message.message_id
                )

                ids = sorted(
                    set(ids)
                )

                target[
                    "message_ids"
                ] = ids

                target[
                    "message_id"
                ] = ids[0]

                await storage.save_posts(
                    posts
                )

                logger.info(
                    "Album updated: source=%s group=%s ids=%s",
                    source_id,
                    message.media_group_id,
                    ids,
                )

            return

        # ----------------------------------------------------
        # New album
        # ----------------------------------------------------

        posts.append(
            {
                "source_chat_id": source_id,
                "message_ids": [
                    message.message_id
                ],
                "message_id": message.message_id,
                "type": "album",
                "media_group_id": (
                    message.media_group_id
                ),
                "caption": (
                    message.caption
                    or message.text
                ),
                "created_at": storage.now_iso(),
            }
        )

        await storage.save_posts(
            posts
        )

        logger.info(
            "New album captured: source=%s group=%s",
            source_id,
            message.media_group_id,
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

    try:

        added = await storage.add_post(
            source_chat_id=source_id,
            message_id=message.message_id,
            message_type=message_type,
            caption=(
                message.caption
                or message.text
            ),
        )

    except Exception as exc:

        logger.exception(
            "Failed to save channel post"
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


# ============================================================
# CHANNEL POST ROUTER
# ============================================================

router.channel_post()(
    capture_channel_post
)


# ============================================================
# SCAN
# ============================================================

@router.message(Command("scan"))
async def cmd_scan(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    sources = await storage.get_sources()
    posts = await storage.get_posts()

    source_ids: set[int] = set()

    for source in sources:

        sid = safe_int(
            source.get("chat_id")
        )

        if sid is not None:

            source_ids.add(sid)

    configured_posts = []

    for post in posts:

        pid = safe_int(
            post.get(
                "source_chat_id"
            )
        )

        if pid in source_ids:

            configured_posts.append(
                post
            )

    await message.answer(
        "📊 <b>Telegram Bot Database</b>\n\n"
        f"Configured sources: "
        f"<b>{len(sources)}</b>\n"
        f"Loaded posts: "
        f"<b>{len(configured_posts)}</b>\n\n"
        "ℹ️ New channel posts are captured "
        "automatically.\n\n"
        "Telegram Bot API does not provide arbitrary "
        "old channel history."
    )


@router.message(Command("clearposts"))
async def cmd_clearposts(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    await storage.clear_posts()

    schedule = await storage.get_schedule()

    schedule[
        "current_index"
    ] = 0

    schedule[
        "next_run_iso"
    ] = None

    schedule[
        "last_completed_iso"
    ] = None

    await storage.save_schedule(
        schedule
    )

    await message.answer(
        "✅ Post database cleared.\n"
        "Scheduler position reset."
    )


# ============================================================
# SCHEDULER HELPERS
# ============================================================

def get_scheduler(
    message: Message,
):
    """
    Get Scheduler instance from dispatcher workflow data.
    """

    try:

        return message.bot.get(
            "scheduler"
        )

    except Exception:
        return None


# ============================================================
# START SCHEDULER
# ============================================================

@router.message(Command("startschedule"))
async def cmd_startschedule(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    scheduler = get_scheduler(
        message
    )

    if scheduler is None:

        await message.answer(
            "❌ Scheduler is unavailable."
        )

        return

    ok, text = await scheduler.start()

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
) -> None:

    if not await admin_only(message):
        return

    scheduler = get_scheduler(
        message
    )

    if scheduler is None:

        await message.answer(
            "❌ Scheduler is unavailable."
        )

        return

    ok, text = await scheduler.stop()

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
) -> None:

    if not await admin_only(message):
        return

    scheduler = get_scheduler(
        message
    )

    schedule = await storage.get_schedule()
    settings = await storage.get_settings()

    sources = await storage.get_sources()
    channels = await storage.get_channels()
    routes = await storage.get_routes()
    posts = await storage.get_posts()

    running = (
        scheduler.is_running()
        if scheduler
        else False
    )

    await message.answer(
        "📊 <b>Status</b>\n\n"
        f"Scheduler: "
        f"<b>{'RUNNING' if running else 'STOPPED'}</b>\n"
        f"Sources: <b>{len(sources)}</b>\n"
        f"Destinations: <b>{len(channels)}</b>\n"
        f"Routes: <b>{len(routes)}</b>\n"
        f"Posts: <b>{len(posts)}</b>\n"
        f"Interval: "
        f"<b>{settings.get('interval_minutes')}</b> min\n"
        f"Mode: "
        f"<b>{settings.get('source_mode')}</b>\n"
        f"Current index: "
        f"<b>{schedule.get('current_index', 0)}</b>"
    )


# ============================================================
# NEXT
# ============================================================

@router.message(Command("next"))
async def cmd_next(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    scheduler = get_scheduler(
        message
    )

    if scheduler is None:

        await message.answer(
            "❌ Scheduler is unavailable."
        )

        return

    queue = await scheduler.build_queue()

    if not queue:

        await message.answer(
            "📭 No posts in scheduler queue."
        )

        return

    schedule = await storage.get_schedule()

    index = safe_int(
        schedule.get(
            "current_index",
            0,
        )
    ) or 0

    index %= len(queue)

    post = queue[index]

    await message.answer(
        "⏭️ <b>Next Post</b>\n\n"
        f"Source: "
        f"<code>{post.get('source_chat_id')}</code>\n"
        f"Message IDs: "
        f"<code>{post.get('message_ids')}</code>\n"
        f"Type: "
        f"<b>{post.get('type')}</b>\n"
        f"Position: "
        f"<b>{index + 1}/{len(queue)}</b>"
    )


# ============================================================
# SET INTERVAL
# ============================================================

@router.message(Command("setinterval"))
async def cmd_setinterval(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 2:

        await message.answer(
            "Usage:\n"
            "<code>/setinterval 10</code>"
        )

        return

    try:

        minutes = float(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "❌ Invalid interval."
        )

        return

    if minutes <= 0:

        await message.answer(
            "❌ Interval must be greater than 0."
        )

        return

    settings = await storage.get_settings()

    settings[
        "interval_minutes"
    ] = minutes

    await storage.save_settings(
        settings
    )

    await message.answer(
        f"✅ Interval set to "
        f"<b>{minutes}</b> minutes."
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

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 2:

        await message.answer(
            "Usage:\n\n"
            "<code>/setsourcemode round_robin</code>\n"
            "<code>/setsourcemode sequential</code>"
        )

        return

    mode = parts[1].lower()

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

    settings = await storage.get_settings()

    settings[
        "source_mode"
    ] = mode

    await storage.save_settings(
        settings
    )

    await message.answer(
        f"✅ Source mode changed to "
        f"<b>{mode}</b>."
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

    schedule = await storage.get_schedule()

    schedule[
        "current_index"
    ] = 0

    schedule[
        "next_run_iso"
    ] = None

    schedule[
        "last_completed_iso"
    ] = None

    await storage.save_schedule(
        schedule
    )

    await message.answer(
        "✅ Scheduler position reset."
    )


# ============================================================
# REGISTER
# ============================================================

def register_handlers(
    dp: Dispatcher,
    bot: Bot,
    scheduler,
) -> None:
    """
    Register all handlers and expose scheduler
    through dispatcher workflow data.
    """

    # --------------------------------------------------------
    # Store dependencies
    # --------------------------------------------------------

    dp["scheduler"] = scheduler
    dp["bot"] = bot

    # --------------------------------------------------------
    # Include router
    # --------------------------------------------------------

    dp.include_router(
        router
    )

    logger.info(
        "Handlers registered successfully."
    )

    logger.info(
        "Owner admin ID: %s",
        OWNER_ADMIN_ID,
        )
