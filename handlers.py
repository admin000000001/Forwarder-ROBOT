from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

import storage
from config import CONFIG
from telegram_utils import esc, verify_chat_access

logger = logging.getLogger("forwarder")

router = Router()

# Media-group collection tasks
_album_tasks: dict[tuple[int, str], asyncio.Task] = {}


# ============================================================
# HELPERS
# ============================================================

def is_owner(message: Message) -> bool:
    try:
        return (
            message.from_user is not None
            and int(message.from_user.id) == int(CONFIG.owner_id)
        )
    except Exception:
        return False


async def owner_only(message: Message) -> bool:
    if not is_owner(message):
        await message.reply("❌ You are not authorized to use this command.")
        return False
    return True


def parse_chat_id(message: Message) -> int | None:
    if not message.text:
        return None

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        return None

    try:
        return int(parts[1].strip())
    except ValueError:
        return None


def format_chat(chat: dict[str, Any]) -> str:
    cid = chat.get("chat_id", "?")
    title = chat.get("title") or "Unknown"
    username = chat.get("username")

    if username:
        return f"{esc(str(title))} — @{esc(str(username))} (`{cid}`)"

    return f"{esc(str(title))} (`{cid}`)"


async def source_is_configured(chat_id: int) -> bool:
    sources = await storage.get_sources()

    for source in sources:
        try:
            if int(source.get("chat_id")) == int(chat_id):
                return source.get("enabled", True)
        except Exception:
            continue

    return False


async def save_single_post(message: Message) -> bool:
    """
    Save one Telegram channel post.

    We only store message IDs.
    Actual media/text/caption stays on Telegram and is copied later
    using copyMessage().
    """

    posts = await storage.get_posts()

    source_chat_id = int(message.chat.id)
    message_id = int(message.message_id)

    # Duplicate protection
    for post in posts:
        try:
            if int(post.get("source_chat_id")) != source_chat_id:
                continue

            ids = post.get("message_ids", [])

            if message_id in ids:
                return False

        except Exception:
            continue

    post = {
        "uid": storage.new_uid(),
        "source_chat_id": source_chat_id,
        "message_ids": [message_id],
        "type": "single",
        "media_group_id": None,
        "created_at": storage.now_iso(),
    }

    posts.append(post)

    await storage.save_posts(posts)

    logger.info(
        "[SOURCE] Post saved: source=%s message_id=%s total=%s",
        source_chat_id,
        message_id,
        len(posts),
    )

    return True


async def save_album_after_delay(
    chat_id: int,
    media_group_id: str,
) -> None:
    """
    Collect album messages arriving within a short period.

    Telegram sends album items as separate channel_post updates.
    """

    try:
        await asyncio.sleep(1.5)

        posts = await storage.get_posts()

        matching: list[dict[str, Any]] = []

        for post in posts:
            try:
                if (
                    int(post.get("source_chat_id")) == int(chat_id)
                    and str(post.get("media_group_id")) == str(media_group_id)
                ):
                    matching.append(post)
            except Exception:
                continue

        if len(matching) <= 1:
            return

        # Sort by first message ID
        matching.sort(
            key=lambda x: int(x.get("message_ids", [0])[0])
        )

        all_ids: list[int] = []

        for post in matching:
            for mid in post.get("message_ids", []):
                if mid not in all_ids:
                    all_ids.append(int(mid))

        first = matching[0]

        # Remove old individual records
        posts = [
            p
            for p in posts
            if p not in matching
        ]

        album_post = {
            "uid": storage.new_uid(),
            "source_chat_id": int(chat_id),
            "message_ids": all_ids,
            "type": "album",
            "media_group_id": str(media_group_id),
            "created_at": storage.now_iso(),
        }

        posts.append(album_post)

        await storage.save_posts(posts)

        logger.info(
            "[SOURCE] Album saved: source=%s group=%s messages=%s",
            chat_id,
            media_group_id,
            len(all_ids),
        )

    except asyncio.CancelledError:
        raise

    except Exception:
        logger.exception(
            "[SOURCE] Album processing failed: source=%s group=%s",
            chat_id,
            media_group_id,
        )

    finally:
        _album_tasks.pop((int(chat_id), str(media_group_id)), None)


# ============================================================
# CHANNEL POST CAPTURE
# ============================================================

@router.channel_post()
async def handle_channel_post(message: Message) -> None:
    """
    IMPORTANT:

    This handler receives NEW posts sent to Telegram channels
    where the bot is an administrator.

    It supports:
      video
      photo
      document
      audio
      animation/GIF
      voice
      video note
      text
      captions
      other Telegram message types
    """

    try:
        chat_id = int(message.chat.id)
        message_id = int(message.message_id)

        logger.info(
            "[SOURCE] New channel post received: chat=%s message_id=%s",
            chat_id,
            message_id,
        )

        # Check source configuration
        configured = await source_is_configured(chat_id)

        if not configured:
            logger.warning(
                "[SOURCE] Ignored post from unconfigured/disabled source: %s",
                chat_id,
            )
            return

        # --------------------------------------------------------
        # Album / Media Group
        # --------------------------------------------------------

        if message.media_group_id:
            key = (chat_id, str(message.media_group_id))

            # Save each album item temporarily as a post
            posts = await storage.get_posts()

            already_exists = False

            for post in posts:
                try:
                    if (
                        int(post.get("source_chat_id")) == chat_id
                        and message_id in post.get("message_ids", [])
                    ):
                        already_exists = True
                        break
                except Exception:
                    continue

            if not already_exists:
                posts.append(
                    {
                        "uid": storage.new_uid(),
                        "source_chat_id": chat_id,
                        "message_ids": [message_id],
                        "type": "album_item",
                        "media_group_id": str(message.media_group_id),
                        "created_at": storage.now_iso(),
                    }
                )

                await storage.save_posts(posts)

                logger.info(
                    "[SOURCE] Album item captured: chat=%s message_id=%s group=%s",
                    chat_id,
                    message_id,
                    message.media_group_id,
                )

            # Restart collection timer
            old_task = _album_tasks.get(key)

            if old_task and not old_task.done():
                old_task.cancel()

            _album_tasks[key] = asyncio.create_task(
                save_album_after_delay(
                    chat_id,
                    str(message.media_group_id),
                )
            )

            return

        # --------------------------------------------------------
        # Normal post
        # --------------------------------------------------------

        saved = await save_single_post(message)

        if saved:
            logger.info(
                "[SOURCE] New post successfully added to database: %s",
                message_id,
            )

    except Exception:
        logger.exception("[SOURCE] Failed to process channel post")


# ============================================================
# START / HELP
# ============================================================

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.reply(
        "🤖 <b>Forwarder-ROBOT</b>\n\n"
        "Telegram post distribution bot.\n\n"
        "Use /help to see available commands."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "🤖 <b>Forwarder-ROBOT — Help</b>\n\n"

        "<b>Public commands</b>\n"
        "/start — welcome message\n"
        "/help — this message\n"
        "/status — bot and scheduler status\n"
        "/next — show next scheduled post\n\n"

        "<b>Source management</b>\n"
        "/addsource &lt;chat_id&gt;\n"
        "/removesource &lt;chat_id&gt;\n"
        "/sources\n"
        "/setsource &lt;chat_id&gt;\n"
        "/clearsources\n"
        "/sourceinfo &lt;chat_id&gt;\n\n"

        "<b>Destination management</b>\n"
        "/addchannel &lt;chat_id&gt;\n"
        "/removechannel &lt;chat_id&gt;\n"
        "/channels\n"
        "/clearchannels\n"
        "/channelinfo &lt;chat_id&gt;\n\n"

        "<b>Source → Destination routes</b>\n"
        "/addroute &lt;source_id&gt; &lt;dest_id&gt;\n"
        "/removeroute &lt;source_id&gt; &lt;dest_id&gt;\n"
        "/routes\n"
        "/clearroutes\n\n"

        "<b>Posts</b>\n"
        "/importposts — reply to JSON file\n"
        "/scan — show stored post count\n\n"

        "<b>Scheduler</b>\n"
        "/startschedule\n"
        "/stopschedule\n"
        "/setinterval &lt;minutes&gt;\n"
        "/setsourcemode round_robin|sequential\n"
        "/reset\n"
        "/reload"
    )

    await message.reply(text)


# ============================================================
# STATUS
# ============================================================

@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not await owner_only(message):
        return

    schedule = await storage.get_schedule()
    sources = await storage.get_sources()
    channels = await storage.get_channels()
    posts = await storage.get_posts()

    await message.reply(
        "📊 <b>Bot Status</b>\n\n"
        f"Sources: <code>{len(sources)}</code>\n"
        f"Destinations: <code>{len(channels)}</code>\n"
        f"Posts: <code>{len(posts)}</code>\n"
        f"Scheduler: <code>"
        f"{'RUNNING' if schedule.get('running') else 'STOPPED'}"
        f"</code>\n"
        f"Current index: <code>{schedule.get('current_index', 0)}</code>\n"
        f"Next run: <code>"
        f"{esc(str(schedule.get('next_run_iso') or 'N/A'))}"
        f"</code>"
    )


@router.message(Command("next"))
async def cmd_next(message: Message) -> None:
    if not await owner_only(message):
        return

    schedule = await storage.get_schedule()
    posts = await storage.get_posts()

    if not posts:
        await message.reply("ℹ️ No posts are currently loaded.")
        return

    index = int(schedule.get("current_index", 0)) % len(posts)

    post = posts[index]

    await message.reply(
        "⏭ <b>Next Post</b>\n\n"
        f"Index: <code>{index}</code>\n"
        f"Source: <code>{post.get('source_chat_id')}</code>\n"
        f"Message IDs: <code>{post.get('message_ids')}</code>\n"
        f"Type: <code>{esc(str(post.get('type', 'single')))}</code>"
    )


# ============================================================
# SOURCE MANAGEMENT
# ============================================================

@router.message(Command("addsource"))
async def cmd_addsource(message: Message, bot: Bot) -> None:
    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:
        await message.reply(
            "Usage:\n"
            "<code>/addsource -1001234567890</code>"
        )
        return

    sources = await storage.get_sources()

    for source in sources:
        try:
            if int(source.get("chat_id")) == chat_id:
                await message.reply(
                    "⚠️ This source channel is already configured."
                )
                return
        except Exception:
            continue

    result = await verify_chat_access(bot, chat_id)

    if not result.ok:
        await message.reply(
            "❌ Cannot access this source channel.\n\n"
            f"ID: <code>{chat_id}</code>\n"
            f"Error: {esc(str(result.error))}\n\n"
            "Make sure the bot is an administrator in the source channel."
        )
        return

    sources.append(
        {
            "chat_id": chat_id,
            "title": result.title,
            "username": result.username,
            "enabled": True,
        }
    )

    await storage.save_sources(sources)

    await message.reply(
        "✅ <b>Source added</b>\n\n"
        f"Title: {esc(str(result.title or 'Unknown'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


@router.message(Command("setsource"))
async def cmd_setsource(message: Message, bot: Bot) -> None:
    await cmd_addsource(message, bot)


@router.message(Command("removesource"))
async def cmd_removesource(message: Message) -> None:
    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:
        await message.reply(
            "Usage: <code>/removesource -1001234567890</code>"
        )
        return

    sources = await storage.get_sources()

    new_sources = []

    for source in sources:
        try:
            if int(source.get("chat_id")) != chat_id:
                new_sources.append(source)
        except Exception:
            new_sources.append(source)

    if len(new_sources) == len(sources):
        await message.reply("❌ Source channel not found.")
        return

    await storage.save_sources(new_sources)

    await message.reply(
        f"✅ Source removed: <code>{chat_id}</code>"
    )


@router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    if not await owner_only(message):
        return

    sources = await storage.get_sources()

    if not sources:
        await message.reply("📭 No source channels configured.")
        return

    lines = ["📡 <b>Source Channels</b>\n"]

    for i, source in enumerate(sources, 1):
        status = "🟢" if source.get("enabled", True) else "🔴"

        lines.append(
            f"{i}. {status} {format_chat(source)}"
        )

    await message.reply("\n".join(lines))


@router.message(Command("clearsources"))
async def cmd_clearsources(message: Message) -> None:
    if not await owner_only(message):
        return

    await storage.save_sources([])

    await message.reply("✅ All source channels removed.")


@router.message(Command("sourceinfo"))
async def cmd_sourceinfo(message: Message, bot: Bot) -> None:
    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:
        await message.reply(
            "Usage: <code>/sourceinfo -1001234567890</code>"
        )
        return

    result = await verify_chat_access(bot, chat_id)

    if not result.ok:
        await message.reply(
            f"❌ Cannot access <code>{chat_id}</code>\n"
            f"{esc(str(result.error))}"
        )
        return

    await message.reply(
        "📡 <b>Source Information</b>\n\n"
        f"Title: {esc(str(result.title or 'Unknown'))}\n"
        f"Username: {esc(str(result.username or 'None'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


# ============================================================
# DESTINATION MANAGEMENT
# ============================================================

@router.message(Command("addchannel"))
async def cmd_addchannel(message: Message, bot: Bot) -> None:
    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:
        await message.reply(
            "Usage:\n"
            "<code>/addchannel -1001234567890</code>"
        )
        return

    channels = await storage.get_channels()

    for channel in channels:
        try:
            if int(channel.get("chat_id")) == chat_id:
                await message.reply(
                    "⚠️ Destination already exists."
                )
                return
        except Exception:
            continue

    result = await verify_chat_access(bot, chat_id)

    if not result.ok:
        await message.reply(
            "❌ Cannot access destination channel.\n\n"
            f"ID: <code>{chat_id}</code>\n"
            f"Error: {esc(str(result.error))}"
        )
        return

    channels.append(
        {
            "chat_id": chat_id,
            "title": result.title,
            "username": result.username,
            "enabled": True,
        }
    )

    await storage.save_channels(channels)

    await message.reply(
        "✅ <b>Destination added</b>\n\n"
        f"Title: {esc(str(result.title or 'Unknown'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


@router.message(Command("removechannel"))
async def cmd_removechannel(message: Message) -> None:
    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:
        await message.reply(
            "Usage: <code>/removechannel -1001234567890</code>"
        )
        return

    channels = await storage.get_channels()

    new_channels = []

    for channel in channels:
        try:
            if int(channel.get("chat_id")) != chat_id:
                new_channels.append(channel)
        except Exception:
            new_channels.append(channel)

    if len(new_channels) == len(channels):
        await message.reply("❌ Destination not found.")
        return

    await storage.save_channels(new_channels)

    await message.reply(
        f"✅ Destination removed: <code>{chat_id}</code>"
    )


@router.message(Command("channels"))
async def cmd_channels(message: Message) -> None:
    if not await owner_only(message):
        return

    channels = await storage.get_channels()

    if not channels:
        await message.reply(
            "📭 No destination channels configured."
        )
        return

    lines = ["📤 <b>Destination Channels</b>\n"]

    for i, channel in enumerate(channels, 1):
        status = "🟢" if channel.get("enabled", True) else "🔴"

        lines.append(
            f"{i}. {status} {format_chat(channel)}"
        )

    await message.reply("\n".join(lines))


@router.message(Command("clearchannels"))
async def cmd_clearchannels(message: Message) -> None:
    if not await owner_only(message):
        return

    await storage.save_channels([])

    await message.reply(
        "✅ All destination channels removed."
    )


@router.message(Command("channelinfo"))
async def cmd_channelinfo(message: Message, bot: Bot) -> None:
    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:
        await message.reply(
            "Usage: <code>/channelinfo -1001234567890</code>"
        )
        return

    result = await verify_chat_access(bot, chat_id)

    if not result.ok:
        await message.reply(
            f"❌ Cannot access <code>{chat_id}</code>\n"
            f"{esc(str(result.error))}"
        )
        return

    await message.reply(
        "📤 <b>Destination Information</b>\n\n"
        f"Title: {esc(str(result.title or 'Unknown'))}\n"
        f"Username: {esc(str(result.username or 'None'))}\n"
        f"ID: <code>{chat_id}</code>"
    )


# ============================================================
# ROUTES
# ============================================================

async def get_routes() -> list[dict[str, Any]]:
    settings = await storage.get_settings()

    routes = settings.get("routes", [])

    if not isinstance(routes, list):
        return []

    return routes


async def save_routes(routes: list[dict[str, Any]]) -> None:
    settings = await storage.get_settings()
    settings["routes"] = routes
    await storage.save_settings(settings)


@router.message(Command("addroute"))
async def cmd_addroute(message: Message) -> None:
    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 3:
        await message.reply(
            "Usage:\n"
            "<code>/addroute SOURCE_ID DESTINATION_ID</code>"
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

    routes = await get_routes()

    route = None

    for item in routes:
        try:
            if int(item.get("source_id")) == source_id:
                route = item
                break
        except Exception:
            continue

    if route is None:
        route = {
            "source_id": source_id,
            "destinations": [],
        }
        routes.append(route)

    destinations = route.setdefault(
        "destinations",
        [],
    )

    if destination_id in destinations:
        await message.reply(
            "⚠️ This route already exists."
        )
        return

    destinations.append(destination_id)

    await save_routes(routes)

    await message.reply(
        "✅ <b>Route added</b>\n\n"
        f"Source: <code>{source_id}</code>\n"
        f"Destination: <code>{destination_id}</code>"
    )


@router.message(Command("removeroute"))
async def cmd_removeroute(message: Message) -> None:
    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 3:
        await message.reply(
            "Usage:\n"
            "<code>/removeroute SOURCE_ID DESTINATION_ID</code>"
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

    routes = await get_routes()
    changed = False

    for route in routes:
        try:
            if int(route.get("source_id")) != source_id:
                continue

            destinations = route.get(
                "destinations",
                [],
            )

            if destination_id in destinations:
                destinations.remove(destination_id)
                changed = True

        except Exception:
            continue

    routes = [
        route
        for route in routes
        if route.get("destinations")
    ]

    await save_routes(routes)

    await message.reply(
        "✅ Route removed."
        if changed
        else "❌ Route not found."
    )


@router.message(Command("routes"))
async def cmd_routes(message: Message) -> None:
    if not await owner_only(message):
        return

    routes = await get_routes()

    if not routes:
        await message.reply(
            "📭 No routes configured.\n\n"
            "Example:\n"
            "<code>/addroute -1001111111111 -1002111111111</code>"
        )
        return

    lines = [
        "🔀 <b>Source → Destination Routes</b>\n"
    ]

    for i, route in enumerate(routes, 1):
        source_id = route.get("source_id")
        destinations = route.get(
            "destinations",
            [],
        )

        lines.append(
            f"<b>{i}. Source:</b> "
            f"<code>{source_id}</code>"
        )

        for destination in destinations:
            lines.append(
                f"   └── <code>{destination}</code>"
            )

        lines.append("")

    await message.reply("\n".join(lines))


@router.message(Command("clearroutes"))
async def cmd_clearroutes(message: Message) -> None:
    if not await owner_only(message):
        return

    await save_routes([])

    await message.reply(
        "✅ All routes cleared."
    )


# ============================================================
# SCHEDULER
# ============================================================

@router.message(Command("startschedule"))
async def cmd_startschedule(
    message: Message,
    scheduler,
) -> None:

    if not await owner_only(message):
        return

    if scheduler is None:
        await message.reply(
            "❌ Scheduler dependency is unavailable."
        )
        return

    ok, text = await scheduler.start()

    await message.reply(
        ("✅ " if ok else "⚠️ ") + esc(str(text))
    )


@router.message(Command("stopschedule"))
async def cmd_stopschedule(
    message: Message,
    scheduler,
) -> None:

    if not await owner_only(message):
        return

    if scheduler is None:
        await message.reply(
            "❌ Scheduler dependency is unavailable."
        )
        return

    ok, text = await scheduler.stop()

    await message.reply(
        ("✅ " if ok else "⚠️ ") + esc(str(text))
    )


@router.message(Command("setinterval"))
async def cmd_setinterval(message: Message) -> None:
    if not await owner_only(message):
        return

    if not message.text:
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.reply(
            "Usage:\n"
            "<code>/setinterval 20</code>"
        )
        return

    try:
        minutes = int(parts[1])

        if minutes <= 0:
            raise ValueError

    except ValueError:
        await message.reply(
            "❌ Interval must be a positive integer."
        )
        return

    settings = await storage.get_settings()
    settings["interval_minutes"] = minutes

    await storage.save_settings(settings)

    await message.reply(
        f"✅ Scheduler interval set to "
        f"<b>{minutes} minutes</b>."
    )


@router.message(Command("setsourcemode"))
async def cmd_setsourcemode(message: Message) -> None:
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
        "sequential",
    ):
        await message.reply(
            "❌ Invalid mode.\n\n"
            "Use:\n"
            "<code>round_robin</code>\n"
            "or\n"
            "<code>sequential</code>"
        )
        return

    settings = await storage.get_settings()
    settings["source_mode"] = mode

    await storage.save_settings(settings)

    await message.reply(
        f"✅ Source mode set to "
        f"<code>{mode}</code>."
    )


@router.message(Command("reset"))
async def cmd_reset(
    message: Message,
    scheduler,
) -> None:

    if not await owner_only(message):
        return

    if scheduler is None:
        await message.reply(
            "❌ Scheduler dependency unavailable."
        )
        return

    await scheduler.reset()

    await message.reply(
        "✅ Scheduler sequence reset."
    )


@router.message(Command("reload"))
async def cmd_reload(message: Message) -> None:
    if not await owner_only(message):
        return

    await message.reply(
        "✅ State is stored on disk and will be "
        "used by the next scheduler cycle."
    )


# ============================================================
# POSTS / SCAN
# ============================================================

@router.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    if not await owner_only(message):
        return

    posts = await storage.get_posts()
    sources = await storage.get_sources()

    await message.reply(
        "ℹ️ <b>Telegram Bot API limitation</b>\n\n"
        "A normal bot cannot retrieve arbitrary old "
        "channel history.\n\n"
        "However, NEW posts from configured source "
        "channels are captured automatically.\n\n"
        f"Configured sources: <b>{len(sources)}</b>\n"
        f"Currently loaded posts: <b>{len(posts)}</b>\n\n"
        "Use /importposts to load an existing JSON list."
    )


# ============================================================
# IMPORT POSTS
# ============================================================

@router.message(Command("importposts"))
async def cmd_importposts(
    message: Message,
    bot: Bot,
) -> None:

    if not await owner_only(message):
        return

    replied = message.reply_to_message

    if not replied or not replied.document:
        await message.reply(
            "❌ Reply to a JSON file with:\n"
            "<code>/importposts</code>"
        )
        return

    document = replied.document

    temp_path = "/tmp/forwarder_import.json"

    try:
        file = await bot.get_file(
            document.file_id
        )

        await bot.download_file(
            file.file_path,
            temp_path,
        )

        with open(
            temp_path,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if isinstance(data, dict):
            imported_posts = data.get(
                "posts",
                [],
            )
        elif isinstance(data, list):
            imported_posts = data
        else:
            imported_posts = []

        if not isinstance(
            imported_posts,
            list,
        ):
            imported_posts = []

        # Validate basic structure
        valid_posts = []

        for post in imported_posts:
            if not isinstance(post, dict):
                continue

            if (
                "source_chat_id" not in post
                or "message_ids" not in post
            ):
                continue

            try:
                source_id = int(
                    post["source_chat_id"]
                )
            except Exception:
                continue

            message_ids = post.get(
                "message_ids"
            )

            if not isinstance(
                message_ids,
                list,
            ):
                continue

            clean_ids = []

            for mid in message_ids:
                try:
                    clean_ids.append(int(mid))
                except Exception:
                    pass

            if not clean_ids:
                continue

            post["source_chat_id"] = source_id
            post["message_ids"] = clean_ids

            if "uid" not in post:
                post["uid"] = storage.new_uid()

            if "type" not in post:
                post["type"] = (
                    "album"
                    if len(clean_ids) > 1
                    else "single"
                )

            if "created_at" not in post:
                post["created_at"] = storage.now_iso()

            valid_posts.append(post)

        await storage.save_posts(
            valid_posts
        )

        await message.reply(
            "✅ <b>Import completed</b>\n\n"
            f"Imported: <b>{len(valid_posts)}</b> post(s)"
        )

    except Exception as exc:
        logger.exception(
            "[IMPORT] Failed"
        )

        await message.reply(
            "❌ Import failed:\n\n"
            f"<code>{esc(str(exc))}</code>"
        )

    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


# ============================================================
# REGISTER
# ============================================================

def register_handlers(
    dp,
    bot: Bot,
    scheduler,
) -> None:
    """
    Register handlers.

    IMPORTANT:
    aiogram Router cannot be attached twice to the same
    Dispatcher.

    Also exposes scheduler through Dispatcher workflow data,
    so handlers with `scheduler` parameter can receive it.
    """

    # Dependency injection
    dp["bot_instance"] = bot
    dp["scheduler_instance"] = scheduler

    # Handler functions using `scheduler` receive this key.
    dp["scheduler"] = scheduler

    # Attach router only if it is not already attached.
    if router.parent_router is None:
        dp.include_router(router)

    logger.info(
        "[INFO] Handlers registered successfully"
            )
