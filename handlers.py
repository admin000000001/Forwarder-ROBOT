"""
handlers.py

Forwarder-ROBOT handlers.

Important:
    - Captures NEW channel posts automatically.
    - Supports multiple source channels.
    - Supports source -> destination routes.
    - Supports text, photo, video, document, audio,
      animation, voice, sticker and media groups.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

import storage
from config import CONFIG
from telegram_utils import esc, verify_chat_access

logger = logging.getLogger("forwarder")

router = Router()

# ============================================================
# DEPENDENCIES
# ============================================================

_BOT: Bot | None = None
_SCHEDULER = None


# ============================================================
# OWNER
# ============================================================

def is_owner(
    message: Message
) -> bool:

    try:

        return (
            message.from_user is not None
            and int(
                message.from_user.id
            )
            == int(
                CONFIG.owner_id
            )
        )

    except Exception:
        return False


async def owner_only(
    message: Message
) -> bool:

    if not is_owner(message):

        await message.reply(
            "❌ You are not authorized."
        )

        return False

    return True


# ============================================================
# CHAT ID PARSER
# ============================================================

def parse_chat_id(
    message: Message
) -> int | None:

    if not message.text:
        return None

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) != 2:
        return None

    try:
        return int(
            parts[1].strip()
        )

    except ValueError:
        return None


# ============================================================
# CHAT FORMAT
# ============================================================

def format_chat(
    chat: dict[str, Any]
) -> str:

    cid = chat.get(
        "chat_id",
        "?"
    )

    title = chat.get(
        "title",
        "Unknown"
    )

    username = chat.get(
        "username"
    )

    if username:

        return (
            f"{esc(str(title))} "
            f"— @{esc(str(username))} "
            f"(<code>{cid}</code>)"
        )

    return (
        f"{esc(str(title))} "
        f"(<code>{cid}</code>)"
    )


# ============================================================
# START
# ============================================================

@router.message(
    Command("start")
)
async def cmd_start(
    message: Message
) -> None:

    await message.reply(
        "🤖 <b>Forwarder-ROBOT</b>\n\n"
        "Telegram post distribution bot.\n\n"
        "Use /help for commands."
    )


# ============================================================
# HELP
# ============================================================

@router.message(
    Command("help")
)
async def cmd_help(
    message: Message
) -> None:

    await message.reply(
        "🤖 <b>Forwarder-ROBOT</b>\n\n"

        "<b>Public</b>\n"
        "/start\n"
        "/help\n"
        "/status\n"
        "/next\n\n"

        "<b>Sources</b>\n"
        "/addsource &lt;chat_id&gt;\n"
        "/removesource &lt;chat_id&gt;\n"
        "/sources\n"
        "/setsource &lt;chat_id&gt;\n"
        "/clearsources\n"
        "/sourceinfo &lt;chat_id&gt;\n\n"

        "<b>Destinations</b>\n"
        "/addchannel &lt;chat_id&gt;\n"
        "/removechannel &lt;chat_id&gt;\n"
        "/channels\n"
        "/clearchannels\n"
        "/channelinfo &lt;chat_id&gt;\n\n"

        "<b>Routes</b>\n"
        "/addroute SOURCE_ID DEST_ID\n"
        "/removeroute SOURCE_ID DEST_ID\n"
        "/routes\n"
        "/clearroutes\n\n"

        "<b>Posts</b>\n"
        "/scan\n"
        "/importposts\n\n"

        "<b>Scheduler</b>\n"
        "/startschedule\n"
        "/stopschedule\n"
        "/setinterval &lt;minutes&gt;\n"
        "/setsourcemode round_robin|sequential\n"
        "/reset\n"
        "/reload"
    )


# ============================================================
# STATUS
# ============================================================

@router.message(
    Command("status")
)
async def cmd_status(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    sources = await storage.get_sources()
    channels = await storage.get_channels()
    posts = await storage.get_posts()
    schedule = await storage.get_schedule()
    routes = await storage.get_routes()

    await message.reply(
        "📊 <b>Status</b>\n\n"
        f"Sources: <code>{len(sources)}</code>\n"
        f"Destinations: <code>{len(channels)}</code>\n"
        f"Routes: <code>{len(routes)}</code>\n"
        f"Posts: <code>{len(posts)}</code>\n"
        f"Scheduler: <code>"
        f"{'RUNNING' if schedule.get('running') else 'STOPPED'}"
        f"</code>\n"
        f"Current index: "
        f"<code>{schedule.get('current_index', 0)}</code>\n"
        f"Next run: "
        f"<code>{esc(str(schedule.get('next_run_iso') or 'N/A'))}</code>"
    )


# ============================================================
# NEXT
# ============================================================

@router.message(
    Command("next")
)
async def cmd_next(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    posts = await storage.get_posts()

    if not posts:

        await message.reply(
            "ℹ️ No posts loaded."
        )

        return

    schedule = await storage.get_schedule()

    index = int(
        schedule.get(
            "current_index",
            0
        )
    )

    index %= len(posts)

    post = posts[index]

    await message.reply(
        "⏭ <b>Next Post</b>\n\n"
        f"Index: <code>{index}</code>\n"
        f"Source: <code>{post.get('source_chat_id')}</code>\n"
        f"Message IDs: "
        f"<code>{post.get('message_ids')}</code>"
    )


# ============================================================
# SOURCE MANAGEMENT
# ============================================================

@router.message(
    Command("addsource")
)
async def cmd_addsource(
    message: Message,
    bot: Bot
) -> None:

    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage:\n"
            "<code>/addsource -1001234567890</code>"
        )

        return

    result = await verify_chat_access(
        bot,
        chat_id
    )

    if not result.ok:

        await message.reply(
            "❌ Cannot access source.\n\n"
            f"ID: <code>{chat_id}</code>\n"
            f"Error: {esc(str(result.error))}\n\n"
            "Make sure the bot is admin."
        )

        return

    added = await storage.add_source(
        {
            "chat_id": chat_id,
            "title": result.title,
            "username": result.username,
            "enabled": True
        }
    )

    if not added:

        await message.reply(
            "⚠️ Source already exists."
        )

        return

    await message.reply(
        "✅ <b>Source added</b>\n\n"
        f"Title: {esc(str(result.title or 'Unknown'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


@router.message(
    Command("setsource")
)
async def cmd_setsource(
    message: Message,
    bot: Bot
) -> None:

    await cmd_addsource(
        message,
        bot
    )


@router.message(
    Command("removesource")
)
async def cmd_removesource(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage: "
            "<code>/removesource -1001234567890</code>"
        )

        return

    removed = await storage.remove_source(
        chat_id
    )

    await message.reply(
        "✅ Source removed."
        if removed
        else "❌ Source not found."
    )


@router.message(
    Command("sources")
)
async def cmd_sources(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    sources = await storage.get_sources()

    if not sources:

        await message.reply(
            "📭 No sources configured."
        )

        return

    lines = [
        "📡 <b>Sources</b>\n"
    ]

    for i, source in enumerate(
        sources,
        1
    ):

        status = (
            "🟢"
            if source.get("enabled", True)
            else "🔴"
        )

        lines.append(
            f"{i}. {status} "
            f"{format_chat(source)}"
        )

    await message.reply(
        "\n".join(lines)
    )


@router.message(
    Command("clearsources")
)
async def cmd_clearsources(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    await storage.save_sources([])

    await message.reply(
        "✅ All sources cleared."
    )


@router.message(
    Command("sourceinfo")
)
async def cmd_sourceinfo(
    message: Message,
    bot: Bot
) -> None:

    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage: "
            "<code>/sourceinfo -1001234567890</code>"
        )

        return

    result = await verify_chat_access(
        bot,
        chat_id
    )

    if not result.ok:

        await message.reply(
            f"❌ Cannot access "
            f"<code>{chat_id}</code>\n"
            f"{esc(str(result.error))}"
        )

        return

    await message.reply(
        "📡 <b>Source Info</b>\n\n"
        f"Title: "
        f"{esc(str(result.title or 'Unknown'))}\n"
        f"Username: "
        f"{esc(str(result.username or 'None'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


# ============================================================
# DESTINATIONS
# ============================================================

@router.message(
    Command("addchannel")
)
async def cmd_addchannel(
    message: Message,
    bot: Bot
) -> None:

    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage:\n"
            "<code>/addchannel -1001234567890</code>"
        )

        return

    result = await verify_chat_access(
        bot,
        chat_id
    )

    if not result.ok:

        await message.reply(
            "❌ Cannot access destination.\n\n"
            f"ID: <code>{chat_id}</code>\n"
            f"Error: {esc(str(result.error))}"
        )

        return

    added = await storage.add_channel(
        {
            "chat_id": chat_id,
            "title": result.title,
            "username": result.username,
            "enabled": True
        }
    )

    if not added:

        await message.reply(
            "⚠️ Destination already exists."
        )

        return

    await message.reply(
        "✅ <b>Destination added</b>\n\n"
        f"Title: "
        f"{esc(str(result.title or 'Unknown'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


@router.message(
    Command("removechannel")
)
async def cmd_removechannel(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage: "
            "<code>/removechannel -1001234567890</code>"
        )

        return

    removed = await storage.remove_channel(
        chat_id
    )

    await message.reply(
        "✅ Destination removed."
        if removed
        else "❌ Destination not found."
    )


@router.message(
    Command("channels")
)
async def cmd_channels(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    channels = await storage.get_channels()

    if not channels:

        await message.reply(
            "📭 No destinations configured."
        )

        return

    lines = [
        "📤 <b>Destinations</b>\n"
    ]

    for i, channel in enumerate(
        channels,
        1
    ):

        status = (
            "🟢"
            if channel.get("enabled", True)
            else "🔴"
        )

        lines.append(
            f"{i}. {status} "
            f"{format_chat(channel)}"
        )

    await message.reply(
        "\n".join(lines)
    )


@router.message(
    Command("clearchannels")
)
async def cmd_clearchannels(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    await storage.save_channels([])

    await message.reply(
        "✅ All destinations cleared."
    )


@router.message(
    Command("channelinfo")
)
async def cmd_channelinfo(
    message: Message,
    bot: Bot
) -> None:

    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage: "
            "<code>/channelinfo -1001234567890</code>"
        )

        return

    result = await verify_chat_access(
        bot,
        chat_id
    )

    if not result.ok:

        await message.reply(
            f"❌ Cannot access "
            f"<code>{chat_id}</code>\n"
            f"{esc(str(result.error))}"
        )

        return

    await message.reply(
        "📤 <b>Destination Info</b>\n\n"
        f"Title: "
        f"{esc(str(result.title or 'Unknown'))}\n"
        f"Username: "
        f"{esc(str(result.username or 'None'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


# ============================================================
# ROUTES
# ============================================================

@router.message(
    Command("addroute")
)
async def cmd_addroute(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 3:

        await message.reply(
            "Usage:\n"
            "<code>/addroute SOURCE_ID DEST_ID</code>"
        )

        return

    try:

        source_id = int(parts[1])
        destination_id = int(parts[2])

    except ValueError:

        await message.reply(
            "❌ IDs must be numbers."
        )

        return

    added = await storage.add_route(
        source_id,
        destination_id
    )

    if not added:

        await message.reply(
            "⚠️ Route already exists."
        )

        return

    await message.reply(
        "✅ <b>Route added</b>\n\n"
        f"Source: <code>{source_id}</code>\n"
        f"Destination: <code>{destination_id}</code>"
    )


@router.message(
    Command("removeroute")
)
async def cmd_removeroute(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 3:

        await message.reply(
            "Usage:\n"
            "<code>/removeroute SOURCE_ID DEST_ID</code>"
        )

        return

    try:

        source_id = int(parts[1])
        destination_id = int(parts[2])

    except ValueError:

        await message.reply(
            "❌ Invalid IDs."
        )

        return

    removed = await storage.remove_route(
        source_id,
        destination_id
    )

    await message.reply(
        "✅ Route removed."
        if removed
        else "❌ Route not found."
    )


@router.message(
    Command("routes")
)
async def cmd_routes(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    routes = await storage.get_routes()

    if not routes:

        await message.reply(
            "📭 No routes configured."
        )

        return

    lines = [
        "🔀 <b>Source → Destinations</b>\n"
    ]

    for route in routes:

        source_id = route.get(
            "source_id"
        )

        destinations = route.get(
            "destinations",
            []
        )

        lines.append(
            f"📡 <b>Source</b>: "
            f"<code>{source_id}</code>"
        )

        for dest in destinations:

            lines.append(
                f"   └─ <code>{dest}</code>"
            )

        lines.append("")

    await message.reply(
        "\n".join(lines)
    )


@router.message(
    Command("clearroutes")
)
async def cmd_clearroutes(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    await storage.save_routes([])

    await message.reply(
        "✅ All routes cleared."
    )


# ============================================================
# SCHEDULER
# ============================================================

@router.message(
    Command("startschedule")
)
async def cmd_startschedule(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    global _SCHEDULER

    if _SCHEDULER is None:

        await message.reply(
            "❌ Scheduler dependency is not initialized."
        )

        return

    ok, text = await _SCHEDULER.start()

    await message.reply(
        ("✅ " if ok else "⚠️ ")
        + esc(text)
    )


@router.message(
    Command("stopschedule")
)
async def cmd_stopschedule(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    global _SCHEDULER

    if _SCHEDULER is None:

        await message.reply(
            "❌ Scheduler dependency is not initialized."
        )

        return

    ok, text = await _SCHEDULER.stop()

    await message.reply(
        ("✅ " if ok else "⚠️ ")
        + esc(text)
    )


@router.message(
    Command("setinterval")
)
async def cmd_setinterval(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.reply(
            "Usage: <code>/setinterval 20</code>"
        )

        return

    try:

        minutes = int(parts[1])

        if minutes <= 0:
            raise ValueError

    except ValueError:

        await message.reply(
            "❌ Interval must be a positive number."
        )

        return

    settings = await storage.get_settings()

    settings["interval_minutes"] = minutes

    await storage.save_settings(
        settings
    )

    await message.reply(
        f"✅ Interval set to "
        f"<b>{minutes} minutes</b>."
    )


@router.message(
    Command("setsourcemode")
)
async def cmd_setsourcemode(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.reply(
            "Usage:\n"
            "<code>/setsourcemode round_robin</code>\n"
            "<code>/setsourcemode sequential</code>"
        )

        return

    mode = parts[1].lower()

    if mode not in (
        "round_robin",
        "sequential"
    ):

        await message.reply(
            "❌ Invalid mode."
        )

        return

    settings = await storage.get_settings()

    settings["source_mode"] = mode

    await storage.save_settings(
        settings
    )

    await message.reply(
        f"✅ Source mode: "
        f"<code>{mode}</code>"
    )


@router.message(
    Command("reset")
)
async def cmd_reset(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    global _SCHEDULER

    if _SCHEDULER is None:

        await message.reply(
            "❌ Scheduler not initialized."
        )

        return

    await _SCHEDULER.reset()

    await message.reply(
        "✅ Scheduler reset."
    )


@router.message(
    Command("reload")
)
async def cmd_reload(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    await message.reply(
        "✅ State is stored on disk and "
        "will be used on the next scheduler cycle."
    )


# ============================================================
# SCAN
# ============================================================

@router.message(
    Command("scan")
)
async def cmd_scan(
    message: Message
) -> None:

    if not await owner_only(message):
        return

    sources = await storage.get_sources()
    posts = await storage.get_posts()

    await message.reply(
        "ℹ️ <b>Telegram Bot API limitation</b>\n\n"
        "A normal bot cannot retrieve arbitrary "
        "old channel history.\n\n"
        "However, NEW posts are captured automatically "
        "while the bot is running and is an admin "
        "of the source channel.\n\n"
        f"Configured sources: "
        f"<b>{len(sources)}</b>\n"
        f"Currently loaded: "
        f"<b>{len(posts)} post(s)</b>"
    )


# ============================================================
# IMPORT POSTS
# ============================================================

@router.message(
    Command("importposts")
)
async def cmd_importposts(
    message: Message,
    bot: Bot
) -> None:

    if not await owner_only(message):
        return

    if not message.reply_to_message:

        await message.reply(
            "❌ Reply to a JSON file with "
            "<code>/importposts</code>."
        )

        return

    document = (
        message.reply_to_message.document
    )

    if document is None:

        await message.reply(
            "❌ Replied message must contain "
            "a JSON document."
        )

        return

    temp_path = os_temp_import_path()

    try:

        file = await bot.get_file(
            document.file_id
        )

        await bot.download_file(
            file.file_path,
            temp_path
        )

        with open(
            temp_path,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, dict):

            imported = data.get(
                "posts",
                []
            )

        elif isinstance(data, list):

            imported = data

        else:

            imported = []

        if not isinstance(
            imported,
            list
        ):

            imported = []

        # Normalize and deduplicate
        existing = await storage.get_posts()

        seen = {
            item.get("_key")
            for item in existing
            if item.get("_key")
        }

        added = 0

        for post in imported:

            if not isinstance(
                post,
                dict
            ):
                continue

            source_id = post.get(
                "source_chat_id"
            )

            message_ids = post.get(
                "message_ids"
            )

            if source_id is None:
                continue

            if not isinstance(
                message_ids,
                list
            ):
                continue

            try:

                source_id = int(
                    source_id
                )

                message_ids = [
                    int(x)
                    for x in message_ids
                ]

            except (
                TypeError,
                ValueError
            ):

                continue

            key = (
                f"{source_id}:"
                + ",".join(
                    str(x)
                    for x in sorted(
                        message_ids
                    )
                )
            )

            if key in seen:
                continue

            post["source_chat_id"] = source_id
            post["message_ids"] = message_ids
            post["_key"] = key
            post.setdefault(
                "uid",
                storage.new_uid()
            )
            post.setdefault(
                "created_at",
                storage.now_iso()
            )

            existing.append(post)

            seen.add(key)
            added += 1

        await storage.save_posts(
            existing
        )

        await message.reply(
            f"✅ Imported "
            f"<b>{added}</b> new post(s).\n"
            f"Total loaded: "
            f"<b>{len(existing)}</b>"
        )

    except Exception as exc:

        logger.exception(
            "Import failed"
        )

        await message.reply(
            "❌ Import failed:\n\n"
            f"<code>{esc(str(exc))}</code>"
        )

    finally:

        try:

            import os

            if os.path.exists(
                temp_path
            ):
                os.remove(
                    temp_path
                )

        except Exception:
            pass


def os_temp_import_path() -> str:

    import os

    return os.path.join(
        os.getcwd(),
        ".forwarder_import.json"
    )


# ============================================================
# NEW CHANNEL POST CAPTURE
# ============================================================

async def _capture_channel_post(
    message: Message
) -> None:

    """
    Captures NEW posts coming from configured source channels.

    IMPORTANT:
        Telegram sends channel posts through the channel_post
        update, not normal message updates.
    """

    if message.chat is None:
        return

    source_id = int(
        message.chat.id
    )

    sources = await storage.get_sources()

    configured = False

    for source in sources:

        try:

            if int(
                source.get("chat_id")
            ) == source_id:

                configured = (
                    source.get(
                        "enabled",
                        True
                    )
                )

                break

        except Exception:
            continue

    if not configured:
        return

    # --------------------------------------------------------
    # Detect message type
    # --------------------------------------------------------

    post_type = "message"

    if message.media_group_id:
        post_type = "album"

    elif message.video:
        post_type = "video"

    elif message.photo:
        post_type = "photo"

    elif message.document:
        post_type = "document"

    elif message.audio:
        post_type = "audio"

    elif message.animation:
        post_type = "animation"

    elif message.voice:
        post_type = "voice"

    elif message.video_note:
        post_type = "video_note"

    elif message.sticker:
        post_type = "sticker"

    elif message.text:
        post_type = "text"

    # --------------------------------------------------------
    # Normal single post
    # --------------------------------------------------------

    if not message.media_group_id:

        post = {
            "source_chat_id": source_id,

            "message_ids": [
                int(message.message_id)
            ],

            "type": post_type,

            "media_group_id": None,

            "caption": (
                message.caption
                or message.text
                or None
            ),

            "source_title": (
                message.chat.title
            ),

            "created_at": (
                storage.now_iso()
            )
        }

        added = await storage.add_post(
            post
        )

        if added:

            logger.info(
                "[CAPTURE] New %s post "
                "captured from %s "
                "message_id=%s",
                post_type,
                source_id,
                message.message_id
            )

        return

    # --------------------------------------------------------
    # Album / Media Group
    # --------------------------------------------------------

    media_group_id = (
        message.media_group_id
    )

    # Wait briefly so Telegram can deliver
    # the remaining album messages.
    await asyncio.sleep(1.0)

    posts = await storage.get_posts()

    # Look for already-created album.
    for existing in posts:

        if (
            existing.get(
                "source_chat_id"
            ) == source_id
            and existing.get(
                "media_group_id"
            ) == media_group_id
        ):

            return

    # Telegram delivers album messages separately.
    # At this point we at least capture this message.
    #
    # Additional album messages can be merged by scheduler/
    # utility layer if supported.
    post = {
        "source_chat_id": source_id,

        "message_ids": [
            int(message.message_id)
        ],

        "type": "album",

        "media_group_id": media_group_id,

        "caption": (
            message.caption
            or None
        ),

        "source_title": (
            message.chat.title
        ),

        "created_at": (
            storage.now_iso()
        )
    }

    added = await storage.add_post(
        post
    )

    if added:

        logger.info(
            "[CAPTURE] Album post "
            "captured from %s "
            "message_id=%s "
            "media_group=%s",
            source_id,
            message.message_id,
            media_group_id
        )


# ============================================================
# CHANNEL POST HANDLER
# ============================================================

@router.channel_post()
async def handle_channel_post(
    message: Message
) -> None:

    try:

        await _capture_channel_post(
            message
        )

    except Exception as exc:

        logger.exception(
            "[ERROR] Failed to capture "
            "channel post: %s",
            exc
        )


# ============================================================
# REGISTER
# ============================================================

def register_handlers(
    dp,
    bot: Bot,
    scheduler
) -> None:

    global _BOT
    global _SCHEDULER

    _BOT = bot
    _SCHEDULER = scheduler

    # Router can only be attached once.
    if router.parent_router is None:

        dp.include_router(
            router
        )

    logger.info(
        "[INFO] Handlers registered"
)
