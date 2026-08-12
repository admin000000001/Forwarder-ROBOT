from __future__ import annotations

import json
import logging
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

import storage
from config import CONFIG

logger = logging.getLogger("forwarder")

router = Router()

# ============================================================
# GLOBAL DEPENDENCIES
# ============================================================

_BOT: Bot | None = None
_SCHEDULER: Any = None


# ============================================================
# HELPERS
# ============================================================

def esc(value: Any) -> str:
    text = str(value)

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def is_owner(message: Message) -> bool:

    try:
        owner_id = int(
            CONFIG.owner_id
        )

        user_id = int(
            message.from_user.id
        )

        return user_id == owner_id

    except Exception as exc:

        logger.error(
            "Owner check failed: %s",
            exc,
        )

        return False


async def owner_only(
    message: Message,
) -> bool:

    if not is_owner(message):

        await message.reply(
            "❌ You are not authorized."
        )

        return False

    return True


def get_arg(
    message: Message,
) -> str | None:

    if not message.text:
        return None

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) != 2:
        return None

    return parts[1].strip()


def get_chat_id(
    message: Message,
) -> int | None:

    arg = get_arg(message)

    if not arg:
        return None

    try:
        return int(arg)

    except ValueError:
        return None


def format_chat(
    item: dict[str, Any],
) -> str:

    title = esc(
        item.get(
            "title",
            "Unknown",
        )
    )

    chat_id = item.get(
        "chat_id",
        "?",
    )

    username = item.get(
        "username"
    )

    if username:
        return (
            f"{title} "
            f"— @{esc(username)} "
            f"(<code>{chat_id}</code>)"
        )

    return (
        f"{title} "
        f"(<code>{chat_id}</code>)"
    )


# ============================================================
# TELEGRAM ACCESS
# ============================================================

async def verify_chat(
    bot: Bot,
    chat_id: int,
) -> tuple[bool, str | None, str | None, str | None]:

    try:

        chat = await bot.get_chat(
            chat_id
        )

        title = (
            chat.title
            or chat.first_name
            or "Unknown"
        )

        username = getattr(
            chat,
            "username",
            None,
        )

        # ----------------------------------------------------
        # Verify bot membership when possible.
        # ----------------------------------------------------

        try:

            me = await bot.get_me()

            member = await bot.get_chat_member(
                chat_id,
                me.id,
            )

            status = str(
                member.status
            )

            if status in {
                "left",
                "kicked",
            }:

                return (
                    False,
                    title,
                    username,
                    "Bot is not a member/admin of this chat.",
                )

        except Exception as exc:

            # get_chat already succeeded.
            # Do not reject the chat just because
            # membership lookup is restricted.
            logger.warning(
                "Membership check failed for %s: %s",
                chat_id,
                exc,
            )

        return (
            True,
            title,
            username,
            None,
        )

    except Exception as exc:

        logger.exception(
            "Cannot access chat %s",
            chat_id,
        )

        return (
            False,
            None,
            None,
            str(exc),
        )


# ============================================================
# START
# ============================================================

@router.message(Command("start"))
async def cmd_start(
    message: Message,
):

    await message.reply(
        "🤖 <b>Forwarder-ROBOT</b>\n\n"
        "Bot is online.\n\n"
        "Use /help for commands."
    )


# ============================================================
# HELP
# ============================================================

@router.message(Command("help"))
async def cmd_help(
    message: Message,
):

    await message.reply(
        "🤖 <b>Forwarder-ROBOT — Help</b>\n\n"

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

@router.message(Command("status"))
async def cmd_status(
    message: Message,
):

    if not await owner_only(message):
        return

    sources = await storage.get_sources()
    channels = await storage.get_channels()
    posts = await storage.get_posts()
    schedule = await storage.get_schedule()
    routes = await storage.get_routes()

    await message.reply(
        "📊 <b>Bot Status</b>\n\n"
        f"Sources: <code>{len(sources)}</code>\n"
        f"Destinations: <code>{len(channels)}</code>\n"
        f"Routes: <code>{len(routes)}</code>\n"
        f"Posts: <code>{len(posts)}</code>\n"
        f"Scheduler: <code>"
        f"{'RUNNING' if schedule.get('running') else 'STOPPED'}"
        f"</code>"
    )


# ============================================================
# SOURCES
# ============================================================

@router.message(Command("addsource"))
async def cmd_addsource(
    message: Message,
):

    if not await owner_only(message):
        return

    chat_id = get_chat_id(message)

    if chat_id is None:

        await message.reply(
            "❌ Invalid command.\n\n"
            "Use:\n"
            "<code>/addsource -1003407857559</code>"
        )

        return

    if _BOT is None:

        await message.reply(
            "❌ Bot instance is not available."
        )

        return

    await message.reply(
        "⏳ Checking source channel..."
    )

    ok, title, username, error = (
        await verify_chat(
            _BOT,
            chat_id,
        )
    )

    if not ok:

        await message.reply(
            "❌ Cannot access source channel.\n\n"
            f"ID: <code>{chat_id}</code>\n"
            f"Error: <code>{esc(error)}</code>\n\n"
            "Make sure the bot is added to the source channel "
            "and preferably promoted to administrator."
        )

        return

    added = await storage.add_source(
        chat_id=chat_id,
        title=title,
        username=username,
    )

    if not added:

        await message.reply(
            "⚠️ This source is already configured.\n\n"
            f"<code>{chat_id}</code>"
        )

        return

    await message.reply(
        "✅ <b>Source added successfully!</b>\n\n"
        f"📡 Title: <b>{esc(title)}</b>\n"
        f"🆔 ID: <code>{chat_id}</code>\n\n"
        "New posts from this channel will now be captured automatically."
    )

    logger.info(
        "[SOURCE] Added %s (%s)",
        chat_id,
        title,
    )


@router.message(Command("setsource"))
async def cmd_setsource(
    message: Message,
):

    await cmd_addsource(message)


@router.message(Command("sources"))
async def cmd_sources(
    message: Message,
):

    if not await owner_only(message):
        return

    sources = await storage.get_sources()

    if not sources:

        await message.reply(
            "📭 <b>No sources configured.</b>\n\n"
            "Add one with:\n"
            "<code>/addsource -1001234567890</code>"
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
            if source.get(
                "enabled",
                True,
            )
            else "🔴"
        )

        lines.append(
            f"{index}. {status} "
            f"{format_chat(source)}"
        )

    await message.reply(
        "\n".join(lines)
    )


@router.message(Command("removesource"))
async def cmd_removesource(
    message: Message,
):

    if not await owner_only(message):
        return

    chat_id = get_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage:\n"
            "<code>/removesource -1001234567890</code>"
        )

        return

    removed = await storage.remove_source(
        chat_id
    )

    if removed:

        await message.reply(
            f"✅ Source removed:\n"
            f"<code>{chat_id}</code>"
        )

    else:

        await message.reply(
            "❌ Source not found."
        )


@router.message(Command("clearsources"))
async def cmd_clearsources(
    message: Message,
):

    if not await owner_only(message):
        return

    await storage.save_sources([])

    await message.reply(
        "✅ All sources cleared."
    )


@router.message(Command("sourceinfo"))
async def cmd_sourceinfo(
    message: Message,
):

    if not await owner_only(message):
        return

    chat_id = get_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage:\n"
            "<code>/sourceinfo -1001234567890</code>"
        )

        return

    if _BOT is None:
        return

    ok, title, username, error = (
        await verify_chat(
            _BOT,
            chat_id,
        )
    )

    if not ok:

        await message.reply(
            f"❌ Cannot access.\n"
            f"<code>{esc(error)}</code>"
        )

        return

    await message.reply(
        "📡 <b>Source Information</b>\n\n"
        f"Title: <b>{esc(title)}</b>\n"
        f"Username: <code>{esc(username or 'None')}</code>\n"
        f"ID: <code>{chat_id}</code>"
    )


# ============================================================
# DESTINATIONS
# ============================================================

@router.message(Command("addchannel"))
async def cmd_addchannel(
    message: Message,
):

    if not await owner_only(message):
        return

    chat_id = get_chat_id(message)

    if chat_id is None:

        await message.reply(
            "Usage:\n"
            "<code>/addchannel -1001234567890</code>"
        )

        return

    if _BOT is None:
        return

    await message.reply(
        "⏳ Checking destination..."
    )

    ok, title, username, error = (
        await verify_chat(
            _BOT,
            chat_id,
        )
    )

    if not ok:

        await message.reply(
            "❌ Cannot access destination.\n\n"
            f"<code>{esc(error)}</code>"
        )

        return

    added = await storage.add_channel(
        chat_id,
        title,
        username,
    )

    if not added:

        await message.reply(
            "⚠️ Destination already exists."
        )

        return

    await message.reply(
        "✅ <b>Destination added</b>\n\n"
        f"Title: <b>{esc(title)}</b>\n"
        f"ID: <code>{chat_id}</code>"
    )


@router.message(Command("channels"))
async def cmd_channels(
    message: Message,
):

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
        1,
    ):

        lines.append(
            f"{i}. "
            f"{format_chat(channel)}"
        )

    await message.reply(
        "\n".join(lines)
    )


@router.message(Command("removechannel"))
async def cmd_removechannel(
    message: Message,
):

    if not await owner_only(message):
        return

    chat_id = get_chat_id(message)

    if chat_id is None:
        return

    removed = await storage.remove_channel(
        chat_id
    )

    await message.reply(
        "✅ Destination removed."
        if removed
        else "❌ Destination not found."
    )


@router.message(Command("clearchannels"))
async def cmd_clearchannels(
    message: Message,
):

    if not await owner_only(message):
        return

    await storage.save_channels([])

    await message.reply(
        "✅ All destinations cleared."
    )


# ============================================================
# ROUTES
# ============================================================

@router.message(Command("addroute"))
async def cmd_addroute(
    message: Message,
):

    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 3:

        await message.reply(
            "Usage:\n"
            "<code>/addroute SOURCE_ID DEST_ID</code>\n\n"
            "Example:\n"
            "<code>/addroute -1003407857559 -1003967093162</code>"
        )

        return

    try:

        source_id = int(parts[1])
        dest_id = int(parts[2])

    except ValueError:

        await message.reply(
            "❌ IDs must be numbers."
        )

        return

    routes = await storage.get_routes()

    route = None

    for item in routes:

        if int(
            item["source_id"]
        ) == source_id:

            route = item
            break

    if route is None:

        route = {
            "source_id": source_id,
            "destinations": [],
        }

        routes.append(route)

    if dest_id in route[
        "destinations"
    ]:

        await message.reply(
            "⚠️ This route already exists."
        )

        return

    route[
        "destinations"
    ].append(dest_id)

    await storage.save_routes(
        routes
    )

    await message.reply(
        "✅ <b>Route added</b>\n\n"
        f"📡 Source:\n"
        f"<code>{source_id}</code>\n\n"
        f"📤 Destination:\n"
        f"<code>{dest_id}</code>"
    )


@router.message(Command("routes"))
async def cmd_routes(
    message: Message,
):

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

        source = route[
            "source_id"
        ]

        lines.append(
            f"📡 Source: <code>{source}</code>"
        )

        for destination in route[
            "destinations"
        ]:

            lines.append(
                f"   └─ <code>{destination}</code>"
            )

        lines.append("")

    await message.reply(
        "\n".join(lines)
    )


@router.message(Command("removeroute"))
async def cmd_removeroute(
    message: Message,
):

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
        dest_id = int(parts[2])

    except ValueError:
        return

    routes = await storage.get_routes()

    changed = False

    for route in routes:

        if int(
            route["source_id"]
        ) != source_id:
            continue

        if dest_id in route[
            "destinations"
        ]:

            route[
                "destinations"
            ].remove(dest_id)

            changed = True

    routes = [
        route
        for route in routes
        if route["destinations"]
    ]

    await storage.save_routes(
        routes
    )

    await message.reply(
        "✅ Route removed."
        if changed
        else "❌ Route not found."
    )


@router.message(Command("clearroutes"))
async def cmd_clearroutes(
    message: Message,
):

    if not await owner_only(message):
        return

    await storage.save_routes([])

    await message.reply(
        "✅ All routes cleared."
    )


# ============================================================
# SCHEDULER
# ============================================================

@router.message(Command("startschedule"))
async def cmd_startschedule(
    message: Message,
):

    if not await owner_only(message):
        return

    if _SCHEDULER is None:

        await message.reply(
            "❌ Scheduler instance unavailable."
        )

        return

    ok, text = await _SCHEDULER.start()

    await message.reply(
        ("✅ " if ok else "⚠️ ")
        + esc(text)
    )


@router.message(Command("stopschedule"))
async def cmd_stopschedule(
    message: Message,
):

    if not await owner_only(message):
        return

    if _SCHEDULER is None:
        return

    ok, text = await _SCHEDULER.stop()

    await message.reply(
        ("✅ " if ok else "⚠️ ")
        + esc(text)
    )


@router.message(Command("setinterval"))
async def cmd_setinterval(
    message: Message,
):

    if not await owner_only(message):
        return

    arg = get_arg(message)

    try:
        minutes = int(arg)
        if minutes <= 0:
            raise ValueError
    except Exception:

        await message.reply(
            "Usage:\n"
            "<code>/setinterval 10</code>"
        )

        return

    settings = await storage.get_settings()

    settings[
        "interval_minutes"
    ] = minutes

    await storage.save_settings(
        settings
    )

    await message.reply(
        f"✅ Interval set to "
        f"<b>{minutes} minutes</b>."
    )


@router.message(Command("setsourcemode"))
async def cmd_setsourcemode(
    message: Message,
):

    if not await owner_only(message):
        return

    arg = get_arg(message)

    if arg not in {
        "round_robin",
        "sequential",
    }:

        await message.reply(
            "Usage:\n"
            "<code>/setsourcemode sequential</code>\n"
            "or\n"
            "<code>/setsourcemode round_robin</code>"
        )

        return

    settings = await storage.get_settings()

    settings[
        "source_mode"
    ] = arg

    await storage.save_settings(
        settings
    )

    await message.reply(
        f"✅ Source mode: "
        f"<code>{arg}</code>"
    )


@router.message(Command("reset"))
async def cmd_reset(
    message: Message,
):

    if not await owner_only(message):
        return

    if _SCHEDULER is not None:
        await _SCHEDULER.reset()

    await message.reply(
        "✅ Scheduler reset."
    )


# ============================================================
# SCAN
# ============================================================

@router.message(Command("scan"))
async def cmd_scan(
    message: Message,
):

    if not await owner_only(message):
        return

    sources = await storage.get_sources()
    posts = await storage.get_posts()

    source_ids = {
        int(source["chat_id"])
        for source in sources
    }

    per_source = {}

    for post in posts:

        try:
            source_id = int(
                post["source_chat_id"]
            )
        except Exception:
            continue

        per_source[
            source_id
        ] = per_source.get(
            source_id,
            0,
        ) + 1

    lines = [
        "📊 <b>Post Scanner</b>\n",
        f"Configured sources: "
        f"<b>{len(sources)}</b>",
        f"Currently loaded: "
        f"<b>{len(posts)} post(s)</b>\n",
    ]

    for source_id in source_ids:

        lines.append(
            f"📡 <code>{source_id}</code>: "
            f"<b>{per_source.get(source_id, 0)}</b> post(s)"
        )

    lines.extend(
        [
            "",
            "ℹ️ New channel posts are captured "
            "automatically while the bot is running.",
        ]
    )

    await message.reply(
        "\n".join(lines)
    )


# ============================================================
# NEW CHANNEL POST CAPTURE
# ============================================================

@router.channel_post()
async def capture_channel_post(
    message: Message,
):

    """
    IMPORTANT:

    This is the part that captures NEW posts
    from source channels.

    Telegram sends channel posts as channel_post updates,
    not normal message updates.
    """

    try:

        source_id = int(
            message.chat.id
        )

        sources = await storage.get_sources()

        configured = any(
            int(source["chat_id"])
            == source_id
            and source.get(
                "enabled",
                True,
            )
            for source in sources
        )

        if not configured:

            return

        post = {
            "source_chat_id": source_id,
            "message_ids": [
                int(message.message_id)
            ],
            "type": "text",
            "created_at": storage.now_iso(),
        }

        # ----------------------------------------------------
        # Detect media
        # ----------------------------------------------------

        if message.video:

            post["type"] = "video"

        elif message.photo:

            post["type"] = "photo"

        elif message.document:

            post["type"] = "document"

        elif message.audio:

            post["type"] = "audio"

        elif message.animation:

            post["type"] = "animation"

        elif message.voice:

            post["type"] = "voice"

        elif message.video_note:

            post["type"] = "video_note"

        elif message.caption:

            post["type"] = "caption"

        else:

            post["type"] = "text"

        added = await storage.add_post(
            post
        )

        if added:

            logger.info(
                "[CAPTURE] New post captured | "
                "source=%s | message_id=%s | type=%s",
                source_id,
                message.message_id,
                post["type"],
            )

        else:

            logger.info(
                "[CAPTURE] Duplicate ignored | "
                "source=%s | message_id=%s",
                source_id,
                message.message_id,
            )

    except Exception as exc:

        logger.exception(
            "[CAPTURE] Failed to capture channel post: %s",
            exc,
        )


# ============================================================
# IMPORT POSTS
# ============================================================

@router.message(Command("importposts"))
async def cmd_importposts(
    message: Message,
):

    if not await owner_only(message):
        return

    reply = message.reply_to_message

    if not reply or not reply.document:

        await message.reply(
            "❌ Reply to a JSON file and send:\n"
            "<code>/importposts</code>"
        )

        return

    if _BOT is None:
        return

    try:

        file = await _BOT.get_file(
            reply.document.file_id
        )

        path = "/tmp/forwarder_import.json"

        await _BOT.download_file(
            file.file_path,
            path,
        )

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if isinstance(data, dict):

            imported = data.get(
                "posts",
                [],
            )

        elif isinstance(data, list):

            imported = data

        else:

            imported = []

        if not isinstance(
            imported,
            list,
        ):

            imported = []

        count = 0

        for post in imported:

            if not isinstance(
                post,
                dict,
            ):
                continue

            if await storage.add_post(
                post
            ):

                count += 1

        await message.reply(
            f"✅ Imported "
            f"<b>{count}</b> new post(s)."
        )

    except Exception as exc:

        logger.exception(
            "Import failed"
        )

        await message.reply(
            "❌ Import failed:\n"
            f"<code>{esc(exc)}</code>"
        )


# ============================================================
# REGISTER
# ============================================================

def register_handlers(
    dp,
    bot: Bot,
    scheduler,
) -> None:

    global _BOT
    global _SCHEDULER

    _BOT = bot
    _SCHEDULER = scheduler

    # --------------------------------------------------------
    # VERY IMPORTANT:
    # Do not attach same router twice.
    # --------------------------------------------------------

    if router.parent_router is None:

        dp.include_router(
            router
        )

        logger.info(
            "[HANDLERS] Router registered"
        )

    else:

        logger.info(
            "[HANDLERS] Router already registered"
        )
