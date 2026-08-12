"""
handlers.py

Telegram command handlers + automatic channel post capture.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message

import storage


logger = logging.getLogger("forwarder.handlers")

router = Router()


# ============================================================
# ADMIN
# ============================================================

def get_admin_ids() -> set[int]:

    result: set[int] = set()

    # Environment variable:
    # ADMIN_IDS=123456789,987654321

    raw = os.getenv(
        "ADMIN_IDS",
        "",
    )

    for value in raw.split(","):

        value = value.strip()

        if not value:
            continue

        try:
            result.add(int(value))
        except ValueError:
            pass

    # Optional single admin
    raw_single = os.getenv(
        "ADMIN_ID",
        "",
    ).strip()

    if raw_single:

        try:
            result.add(int(raw_single))
        except ValueError:
            pass

    return result


def is_admin(message: Message) -> bool:

    if not message.from_user:
        return False

    admin_ids = get_admin_ids()

    return message.from_user.id in admin_ids


async def admin_only(
    message: Message,
) -> bool:

    if is_admin(message):
        return True

    await message.answer(
        "⛔ You are not authorized."
    )

    return False


# ============================================================
# PARSE INTEGER
# ============================================================

def parse_id(
    message: Message,
    command_name: str,
) -> int | None:

    text = message.text or ""

    parts = text.split()

    if len(parts) != 2:

        return None

    try:

        return int(parts[1])

    except ValueError:

        return None


# ============================================================
# HELP
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

<b>Sources</b>
/addsource CHAT_ID
/removesource CHAT_ID
/sources
/clearsources

<b>Destinations</b>
/addchannel CHAT_ID
/removechannel CHAT_ID
/channels
/clearchannels

<b>Routes</b>
/addroute SOURCE_ID DEST_ID
/removeroute SOURCE_ID DEST_ID
/routes
/clearoutes

<b>Posts</b>
/scan
/clearposts

<b>Scheduler</b>
/startschedule
/stopschedule
/status
/next
/setinterval MINUTES
/setsourcemode round_robin
/setsourcemode sequential
/reset

<b>Example</b>

/addsource -1003407857559

/addsource -1004488672586

/addchannel -1003967093162

/addchannel -1004369290699

/addroute -1003407857559 -1003967093162

/addroute -1004488672586 -1004369290699

Then:

/routes

You should see:

Source A → Destination A
Source B → Destination B
"""

    await message.answer(
        text
    )


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

    chat_id = parse_id(
        message,
        "addsource",
    )

    if chat_id is None:

        await message.answer(
            "Usage:\n"
            "<code>/addsource -1001234567890</code>"
        )

        return

    try:

        chat = await bot.get_chat(
            chat_id
        )

    except Exception as exc:

        logger.error(
            "Source get_chat failed: %s",
            exc,
        )

        await message.answer(
            "❌ Source channel access failed.\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            "Make sure the bot is an administrator "
            "of the source channel."
        )

        return

    ok, result = await storage.add_source(
        chat_id=chat_id,
        title=chat.title,
        username=chat.username,
    )

    if ok:

        await message.answer(
            "✅ <b>Source added</b>\n\n"
            f"📡 {chat.title or chat_id}\n"
            f"🆔 <code>{chat_id}</code>"
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

    chat_id = parse_id(
        message,
        "removesource",
    )

    if chat_id is None:

        await message.answer(
            "Usage:\n"
            "<code>/removesource -1001234567890</code>"
        )

        return

    removed = await storage.remove_source(
        chat_id
    )

    await message.answer(
        "✅ Source removed."
        if removed
        else "⚠️ Source not found."
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
        "📡 <b>Configured Sources</b>\n"
    ]

    for index, source in enumerate(
        sources,
        1,
    ):

        status = (
            "🟢"
            if source.get("enabled", True)
            else "🔴"
        )

        lines.append(
            f"{index}. {status} "
            f"<code>{source.get('chat_id')}</code>\n"
            f"   {source.get('title', '')}"
        )

    await message.answer(
        "\n".join(lines)
    )


@router.message(Command("clearsources"))
async def cmd_clearsources(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    await storage.clear_sources()

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

    chat_id = parse_id(
        message,
        "addchannel",
    )

    if chat_id is None:

        await message.answer(
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
            "Destination get_chat failed: %s",
            exc,
        )

        await message.answer(
            "❌ Destination channel access failed.\n\n"
            f"Chat ID: <code>{chat_id}</code>\n\n"
            "Make sure the bot is an administrator "
            "of the destination channel."
        )

        return

    ok, result = await storage.add_channel(
        chat_id=chat_id,
        title=chat.title,
        username=chat.username,
    )

    await message.answer(
        (
            "✅ <b>Destination added</b>\n\n"
            f"📡 {chat.title or chat_id}\n"
            f"🆔 <code>{chat_id}</code>"
        )
        if ok
        else f"ℹ️ {result}"
    )


@router.message(Command("removechannel"))
async def cmd_removechannel(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    chat_id = parse_id(
        message,
        "removechannel",
    )

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
            "📡 <b>Destinations</b>\n\n"
            "No destinations configured."
        )

        return

    lines = [
        "📡 <b>Configured Destinations</b>\n"
    ]

    for index, channel in enumerate(
        channels,
        1,
    ):

        status = (
            "🟢"
            if channel.get("enabled", True)
            else "🔴"
        )

        lines.append(
            f"{index}. {status} "
            f"<code>{channel.get('chat_id')}</code>\n"
            f"   {channel.get('title', '')}"
        )

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

        source_id = int(parts[1])
        destination_id = int(parts[2])

    except ValueError:

        await message.answer(
            "❌ Invalid chat ID."
        )

        return

    sources = await storage.get_sources()

    channels = await storage.get_channels()

    source_exists = any(
        int(s.get("chat_id", 0))
        == source_id
        for s in sources
    )

    destination_exists = any(
        int(c.get("chat_id", 0))
        == destination_id
        for c in channels
    )

    if not source_exists:

        await message.answer(
            "❌ Source is not configured.\n\n"
            "First use:\n"
            f"<code>/addsource {source_id}</code>"
        )

        return

    if not destination_exists:

        await message.answer(
            "❌ Destination is not configured.\n\n"
            "First use:\n"
            f"<code>/addchannel {destination_id}</code>"
        )

        return

    ok, result = await storage.add_route(
        source_id,
        destination_id,
    )

    await message.answer(
        (
            "✅ <b>Route added</b>\n\n"
            f"📡 Source:\n"
            f"<code>{source_id}</code>\n"
            f"└─ <code>{destination_id}</code>"
        )
        if ok
        else f"ℹ️ {result}"
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
        "🔀 <b>Source → Destinations</b>\n"
    ]

    for route in routes:

        source_id = route["source_id"]

        destinations = route.get(
            "destinations",
            [],
        )

        lines.append(
            f"📡 Source: <code>{source_id}</code>"
        )

        if destinations:

            for destination in destinations:

                lines.append(
                    f"   └─ <code>{destination}</code>"
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
    Automatically store new posts coming from configured sources.
    """

    if not message.chat:
        return

    source_id = int(
        message.chat.id
    )

    sources = await storage.get_sources()

    configured = any(
        int(s.get("chat_id", 0))
        == source_id
        and s.get("enabled", True)
        for s in sources
    )

    if not configured:
        return

    # --------------------------------------------------------
    # Media group
    # --------------------------------------------------------

    if message.media_group_id:

        # Small delay allows multiple album messages
        # to arrive before we build the group.
        await asyncio.sleep(1.0)

        posts = await storage.get_posts()

        existing_ids = set()

        for post in posts:

            if int(
                post.get(
                    "source_chat_id",
                    0,
                )
            ) != source_id:
                continue

            if (
                post.get("media_group_id")
                != message.media_group_id
            ):
                continue

            for mid in post.get(
                "message_ids",
                [],
            ):

                existing_ids.add(
                    int(mid)
                )

        if message.message_id in existing_ids:
            return

        # At the moment the handler receives each album item
        # separately. Add each message into a temporary group.
        posts = await storage.get_posts()

        target = None

        for post in posts:

            if (
                int(
                    post.get(
                        "source_chat_id",
                        0,
                    )
                )
                == source_id
                and post.get(
                    "media_group_id"
                )
                == message.media_group_id
            ):

                target = post
                break

        if target:

            ids = [
                int(x)
                for x in target.get(
                    "message_ids",
                    [],
                )
            ]

            if message.message_id not in ids:

                ids.append(
                    message.message_id
                )

                ids.sort()

                target["message_ids"] = ids

                await storage.save_posts(
                    posts
                )

            return

        posts.append(
            {
                "source_chat_id": source_id,
                "message_ids": [
                    message.message_id
                ],
                "message_id": message.message_id,
                "type": "album",
                "media_group_id": message.media_group_id,
                "caption": message.caption,
                "created_at": storage.now_iso(),
            }
        )

        await storage.save_posts(
            posts
        )

        logger.info(
            "New album captured: %s / %s",
            source_id,
            message.media_group_id,
        )

        return

    # --------------------------------------------------------
    # Normal message
    # --------------------------------------------------------

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

    added = await storage.add_post(
        source_chat_id=source_id,
        message_id=message.message_id,
        message_type=message_type,
        caption=message.caption
        or message.text,
    )

    if added:

        logger.info(
            "New post captured: source=%s message=%s type=%s",
            source_id,
            message.message_id,
            message_type,
        )


# IMPORTANT:
# Channel posts use CHANNEL_POST observer.
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

    sources = await storage.get_sources()
    posts = await storage.get_posts()

    source_ids = {
        int(s.get("chat_id"))
        for s in sources
        if s.get("chat_id") is not None
    }

    configured_posts = [
        p
        for p in posts
        if int(
            p.get(
                "source_chat_id",
                0,
            )
        )
        in source_ids
    ]

    lines = [
        "📊 <b>Telegram Bot Post Database</b>",
        "",
        f"Configured sources: <b>{len(sources)}</b>",
        f"Currently loaded: <b>{len(configured_posts)} post(s)</b>",
        "",
        "ℹ️ The Bot API does not provide arbitrary old channel history.",
        "New posts are captured automatically while the bot is admin.",
    ]

    await message.answer(
        "\n".join(lines)
    )


@router.message(Command("clearposts"))
async def cmd_clearposts(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    await storage.clear_posts()

    schedule = await storage.get_schedule()

    schedule["current_index"] = 0
    schedule["next_run_iso"] = None

    await storage.save_schedule(
        schedule
    )

    await message.answer(
        "✅ Post database cleared."
    )


# ============================================================
# SCHEDULER
# ============================================================

@router.message(Command("startschedule"))
async def cmd_startschedule(
    message: Message,
    scheduler,
) -> None:

    if not await admin_only(message):
        return

    ok, text = await scheduler.start()

    await message.answer(
        ("▶️ " if ok else "ℹ️ ")
        + text
    )


@router.message(Command("stopschedule"))
async def cmd_stopschedule(
    message: Message,
    scheduler,
) -> None:

    if not await admin_only(message):
        return

    ok, text = await scheduler.stop()

    await message.answer(
        ("⏹️ " if ok else "ℹ️ ")
        + text
    )


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    scheduler,
) -> None:

    if not await admin_only(message):
        return

    schedule = await storage.get_schedule()
    settings = await storage.get_settings()

    sources = await storage.get_sources()
    channels = await storage.get_channels()
    routes = await storage.get_routes()
    posts = await storage.get_posts()

    await message.answer(
        "📊 <b>Status</b>\n\n"
        f"Scheduler: "
        f"<b>{'RUNNING' if scheduler.is_running() else 'STOPPED'}</b>\n"
        f"Sources: <b>{len(sources)}</b>\n"
        f"Destinations: <b>{len(channels)}</b>\n"
        f"Routes: <b>{len(routes)}</b>\n"
        f"Posts: <b>{len(posts)}</b>\n"
        f"Interval: <b>{settings.get('interval_minutes')} min</b>\n"
        f"Mode: <b>{settings.get('source_mode')}</b>\n"
        f"Current index: <b>{schedule.get('current_index', 0)}</b>"
    )


@router.message(Command("next"))
async def cmd_next(
    message: Message,
    scheduler,
) -> None:

    if not await admin_only(message):
        return

    queue = await scheduler.build_queue()

    if not queue:

        await message.answer(
            "📭 No posts in scheduler queue."
        )

        return

    schedule = await storage.get_schedule()

    index = int(
        schedule.get(
            "current_index",
            0,
        )
        or 0
    )

    index %= len(queue)

    post = queue[index]

    await message.answer(
        "⏭️ <b>Next Post</b>\n\n"
        f"Source: <code>{post.get('source_chat_id')}</code>\n"
        f"Message IDs: <code>{post.get('message_ids')}</code>\n"
        f"Type: <b>{post.get('type')}</b>\n"
        f"Position: <b>{index + 1}/{len(queue)}</b>"
    )


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

        minutes = float(parts[1])

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

    settings["interval_minutes"] = minutes

    await storage.save_settings(
        settings
    )

    await message.answer(
        f"✅ Interval set to <b>{minutes}</b> minutes."
    )


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
            "Usage:\n"
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
            "❌ Mode must be:\n"
            "round_robin\n"
            "sequential"
        )

        return

    settings = await storage.get_settings()

    settings["source_mode"] = mode

    await storage.save_settings(
        settings
    )

    await message.answer(
        f"✅ Source mode: <b>{mode}</b>"
    )


@router.message(Command("reset"))
async def cmd_reset(
    message: Message,
) -> None:

    if not await admin_only(message):
        return

    schedule = await storage.get_schedule()

    schedule["current_index"] = 0
    schedule["next_run_iso"] = None
    schedule["last_completed_iso"] = None

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

    # Inject bot/scheduler into handler workflow
    dp.include_router(router)

    # Middleware-like dependency injection
    # Aiogram handler parameters named `scheduler`
    # are supplied through dispatcher workflow data.
    dp["scheduler"] = scheduler
    dp["bot"] = bot

    logger.info(
        "Handlers registered successfully."
)
