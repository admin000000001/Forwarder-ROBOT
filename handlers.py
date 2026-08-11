"""
handlers.py

All Telegram command handlers and the automatic new-post capture handler.
Owner-only commands verify message.from_user.id == CONFIG.owner_id.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import storage
from config import CONFIG
from scheduler import Scheduler
from telegram_utils import esc, verify_chat_access

logger = logging.getLogger("forwarder")

router_scheduler: Scheduler | None = None


def set_scheduler(scheduler: Scheduler) -> None:
    global router_scheduler
    router_scheduler = scheduler


def owner_only(handler: Callable) -> Callable:
    @wraps(handler)
    async def wrapper(message: Message, *args: Any, **kwargs: Any) -> Any:
        if message.from_user is None or message.from_user.id != CONFIG.owner_id:
            await message.answer("⛔ This command is restricted to the bot owner.")
            return None
        return await handler(message, *args, **kwargs)

    return wrapper


def _parse_chat_id_arg(text: str) -> int | None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1].strip())
    except ValueError:
        return None


def register_handlers(dp: Dispatcher, bot: Bot, scheduler: Scheduler) -> None:
    set_scheduler(scheduler)

    # -- basic commands ---------------------------------------------------

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        await message.answer(
            "🤖 <b>Forwarder-ROBOT</b>\n\n"
            "I automatically distribute posts from source channels to "
            "destination channels on a schedule.\n\n"
            "Send /help to see available commands."
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        is_owner = message.from_user is not None and message.from_user.id == CONFIG.owner_id
        lines = [
            "🤖 <b>Forwarder-ROBOT — Help</b>",
            "",
            "<b>Public commands</b>",
            "/start — welcome message",
            "/help — this message",
            "/status — bot and scheduler status",
            "/next — show the next scheduled post",
        ]
        if is_owner:
            lines += [
                "",
                "<b>Source management</b>",
                "/addsource &lt;chat_id&gt;",
                "/removesource &lt;chat_id&gt;",
                "/sources",
                "/setsource &lt;chat_id&gt; (alias of addsource)",
                "/clearsources",
                "/sourceinfo &lt;chat_id&gt;",
                "",
                "<b>Destination management</b>",
                "/addchannel &lt;chat_id&gt;",
                "/removechannel &lt;chat_id&gt;",
                "/channels",
                "/clearchannels",
                "/channelinfo &lt;chat_id&gt;",
                "",
                "<b>Posts</b>",
                "/importposts (reply to a .json file)",
                "/scan",
                "",
                "<b>Scheduler</b>",
                "/startschedule",
                "/stopschedule",
                "/setinterval &lt;minutes&gt;",
                "/setsourcemode round_robin|sequential",
                "/reset",
                "/reload",
            ]
        await message.answer("\n".join(lines))

    # -- source management --------------------------------------------------

    @dp.message(Command("addsource", "setsource"))
    @owner_only
    async def cmd_addsource(message: Message) -> None:
        chat_id = _parse_chat_id_arg(message.text or "")
        if chat_id is None:
            await message.answer("Usage: <code>/addsource -1001234567890</code>")
            return

        result = await verify_chat_access(bot, chat_id)
        if not result.ok:
            await message.answer(
                "❌ Cannot access this source channel.\n\n"
                f"Reason: {esc(result.error)}\n\n"
                "Possible reasons:\n"
                "• Wrong channel ID\n"
                "• Bot is not a member\n"
                "• Bot is not an administrator\n"
                "• Channel was deleted"
            )
            return

        sources = await storage.get_sources()
        if any(s["chat_id"] == chat_id for s in sources):
            await message.answer("ℹ️ This source is already added.")
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
        await message.answer(f"✅ Source added: {esc(result.title)} (<code>{chat_id}</code>)")

    @dp.message(Command("removesource"))
    @owner_only
    async def cmd_removesource(message: Message) -> None:
        chat_id = _parse_chat_id_arg(message.text or "")
        if chat_id is None:
            await message.answer("Usage: <code>/removesource -1001234567890</code>")
            return
        sources = await storage.get_sources()
        new_sources = [s for s in sources if s["chat_id"] != chat_id]
        if len(new_sources) == len(sources):
            await message.answer("ℹ️ That source was not found.")
            return
        await storage.save_sources(new_sources)
        await message.answer(f"✅ Source removed: <code>{chat_id}</code>")

    @dp.message(Command("clearsources"))
    @owner_only
    async def cmd_clearsources(message: Message) -> None:
        await storage.save_sources([])
        await message.answer("✅ All sources cleared.")

    @dp.message(Command("sources"))
    @owner_only
    async def cmd_sources(message: Message) -> None:
        sources = await storage.get_sources()
        if not sources:
            await message.answer("📚 No sources configured. Use /addsource &lt;chat_id&gt;.")
            return
        lines = ["📚 <b>Sources</b>", ""]
        for i, s in enumerate(sources, start=1):
            status = "✅ Active" if s.get("enabled", True) else "⚠️ Unavailable"
            lines.append(
                f"{i}. {esc(s.get('title') or 'Unknown')}\n"
                f"ID: <code>{s['chat_id']}</code>\n"
                f"Status: {status}\n"
            )
        await message.answer("\n".join(lines))

    @dp.message(Command("sourceinfo"))
    @owner_only
    async def cmd_sourceinfo(message: Message) -> None:
        chat_id = _parse_chat_id_arg(message.text or "")
        if chat_id is None:
            await message.answer("Usage: <code>/sourceinfo -1001234567890</code>")
            return
        sources = await storage.get_sources()
        match = next((s for s in sources if s["chat_id"] == chat_id), None)
        if not match:
            await message.answer("ℹ️ That source is not configured.")
            return
        status = "✅ Active" if match.get("enabled", True) else "⚠️ Unavailable"
        await message.answer(
            f"📚 <b>{esc(match.get('title') or 'Unknown')}</b>\n"
            f"ID: <code>{match['chat_id']}</code>\n"
            f"Username: {esc(match.get('username') or 'N/A')}\n"
            f"Status: {status}"
        )

    # -- destination management ----------------------------------------

    @dp.message(Command("addchannel"))
    @owner_only
    async def cmd_addchannel(message: Message) -> None:
        chat_id = _parse_chat_id_arg(message.text or "")
        if chat_id is None:
            await message.answer("Usage: <code>/addchannel -1001234567890</code>")
            return

        result = await verify_chat_access(bot, chat_id)
        if not result.ok:
            await message.answer(
                "❌ Cannot access this destination channel.\n\n"
                f"Reason: {esc(result.error)}\n\n"
                "Possible reasons:\n"
                "• Wrong channel ID\n"
                "• Bot is not a member\n"
                "• Bot is not an administrator\n"
                "• Channel was deleted"
            )
            return

        channels = await storage.get_channels()
        if any(c["chat_id"] == chat_id for c in channels):
            await message.answer("ℹ️ This destination is already added.")
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
        await message.answer(f"✅ Destination added: {esc(result.title)} (<code>{chat_id}</code>)")

    @dp.message(Command("removechannel"))
    @owner_only
    async def cmd_removechannel(message: Message) -> None:
        chat_id = _parse_chat_id_arg(message.text or "")
        if chat_id is None:
            await message.answer("Usage: <code>/removechannel -1001234567890</code>")
            return
        channels = await storage.get_channels()
        new_channels = [c for c in channels if c["chat_id"] != chat_id]
        if len(new_channels) == len(channels):
            await message.answer("ℹ️ That destination was not found.")
            return
        await storage.save_channels(new_channels)
        await message.answer(f"✅ Destination removed: <code>{chat_id}</code>")

    @dp.message(Command("clearchannels"))
    @owner_only
    async def cmd_clearchannels(message: Message) -> None:
        await storage.save_channels([])
        await message.answer("✅ All destinations cleared.")

    @dp.message(Command("channels"))
    @owner_only
    async def cmd_channels(message: Message) -> None:
        channels = await storage.get_channels()
        if not channels:
            await message.answer("📡 No destinations configured. Use /addchannel &lt;chat_id&gt;.")
            return
        lines = ["📡 <b>Destination Channels</b>", ""]
        for i, c in enumerate(channels, start=1):
            status = "✅ Active" if c.get("enabled", True) else "⚠️ Unavailable"
            lines.append(
                f"{i}. {esc(c.get('title') or 'Unknown')}\n"
                f"ID: <code>{c['chat_id']}</code>\n"
                f"Status: {status}\n"
            )
        await message.answer("\n".join(lines))

    @dp.message(Command("channelinfo"))
    @owner_only
    async def cmd_channelinfo(message: Message) -> None:
        chat_id = _parse_chat_id_arg(message.text or "")
        if chat_id is None:
            await message.answer("Usage: <code>/channelinfo -1001234567890</code>")
            return
        channels = await storage.get_channels()
        match = next((c for c in channels if c["chat_id"] == chat_id), None)
        if not match:
            await message.answer("ℹ️ That destination is not configured.")
            return
        status = "✅ Active" if match.get("enabled", True) else "⚠️ Unavailable"
        await message.answer(
            f"📡 <b>{esc(match.get('title') or 'Unknown')}</b>\n"
            f"ID: <code>{match['chat_id']}</code>\n"
            f"Username: {esc(match.get('username') or 'N/A')}\n"
            f"Status: {status}"
        )

    # -- posts / import -----------------------------------------------------

    @dp.message(Command("importposts"))
    @owner_only
    async def cmd_importposts(message: Message) -> None:
        if not message.reply_to_message or not message.reply_to_message.document:
            await message.answer(
                "Usage: reply to a <code>.json</code> file with /importposts.\n\n"
                "Expected format:\n"
                "<code>[{\"source_chat_id\": -1001234567890, \"message_id\": 101}]</code>"
            )
            return

        doc = message.reply_to_message.document
        try:
            file = await bot.get_file(doc.file_id)
            file_bytes = await bot.download_file(file.file_path)
            raw = file_bytes.read().decode("utf-8")
            entries = json.loads(raw)
            if not isinstance(entries, list):
                raise ValueError("Root JSON element must be a list")
        except Exception as exc:  # noqa: BLE001
            await message.answer(f"❌ Failed to read/parse JSON file: {esc(exc)}")
            return

        sources = await storage.get_sources()
        known_source_ids = {s["chat_id"] for s in sources}
        posts = await storage.get_posts()
        existing_pairs = {
            (p["source_chat_id"], mid) for p in posts for mid in p["message_ids"]
        }

        imported = 0
        skipped_invalid = 0
        skipped_unknown_source = 0
        skipped_duplicate = 0

        for entry in entries:
            if not isinstance(entry, dict):
                skipped_invalid += 1
                continue
            source_chat_id = entry.get("source_chat_id")
            message_id = entry.get("message_id")
            if not isinstance(source_chat_id, int) or not isinstance(message_id, int):
                skipped_invalid += 1
                continue
            if source_chat_id not in known_source_ids:
                skipped_unknown_source += 1
                continue
            if (source_chat_id, message_id) in existing_pairs:
                skipped_duplicate += 1
                continue

            posts.append(
                {
                    "post_uid": storage.new_uid(),
                    "source_chat_id": source_chat_id,
                    "message_ids": [message_id],
                    "type": "single",
                    "added_at": storage.now_iso(),
                }
            )
            existing_pairs.add((source_chat_id, message_id))
            imported += 1

        await storage.save_posts(posts)
        await message.answer(
            "✅ Import complete\n\n"
            f"Imported: {imported}\n"
            f"Skipped (invalid entry): {skipped_invalid}\n"
            f"Skipped (unknown source): {skipped_unknown_source}\n"
            f"Skipped (duplicate): {skipped_duplicate}"
        )

    @dp.message(Command("scan"))
    @owner_only
    async def cmd_scan(message: Message) -> None:
        posts = await storage.get_posts()
        await message.answer(
            "ℹ️ <b>Telegram Bot API limitation</b>\n\n"
            "A normal bot cannot retrieve arbitrary old channel history.\n\n"
            "You can:\n"
            "1. Automatically capture new posts from configured source channels.\n"
            "2. Import existing message IDs using /importposts.\n\n"
            f"Currently loaded: {len(posts)} post(s)"
        )

    # -- scheduler control ------------------------------------------------

    @dp.message(Command("startschedule"))
    @owner_only
    async def cmd_startschedule(message: Message) -> None:
        ok, msg = await scheduler.start()
        await message.answer(("✅ " if ok else "ℹ️ ") + esc(msg))

    @dp.message(Command("stopschedule"))
    @owner_only
    async def cmd_stopschedule(message: Message) -> None:
        ok, msg = await scheduler.stop()
        await message.answer(("✅ " if ok else "ℹ️ ") + esc(msg))

    @dp.message(Command("setinterval"))
    @owner_only
    async def cmd_setinterval(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("Usage: <code>/setinterval 30</code>")
            return
        minutes = int(parts[1].strip())
        if minutes <= 0:
            await message.answer("❌ Interval must be a positive number of minutes.")
            return
        settings = await storage.get_settings()
        settings["interval_minutes"] = minutes
        await storage.save_settings(settings)
        await message.answer(f"✅ Interval set to {minutes} minutes.")

    @dp.message(Command("setsourcemode"))
    @owner_only
    async def cmd_setsourcemode(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        mode = parts[1].strip().lower() if len(parts) > 1 else ""
        if mode not in ("round_robin", "sequential"):
            await message.answer("Usage: <code>/setsourcemode round_robin</code> or <code>/setsourcemode sequential</code>")
            return
        settings = await storage.get_settings()
        settings["source_mode"] = mode
        await storage.save_settings(settings)
        await message.answer(f"✅ Source mode set to {esc(mode.upper())}.")

    @dp.message(Command("reset"))
    @owner_only
    async def cmd_reset(message: Message) -> None:
        await scheduler.reset()
        await message.answer("✅ Schedule progress reset to post #1.")

    @dp.message(Command("reload"))
    @owner_only
    async def cmd_reload(message: Message) -> None:
        # Re-read all JSON storage from disk (picks up manual edits).
        await storage.get_sources()
        await storage.get_channels()
        await storage.get_posts()
        await storage.get_schedule()
        await storage.get_settings()
        await message.answer("✅ Configuration reloaded from disk.")

    # -- status / next --------------------------------------------------------

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        sources = await storage.get_sources()
        channels = await storage.get_channels()
        posts = await storage.get_posts()
        schedule = await storage.get_schedule()
        settings = await Scheduler.get_effective_settings()

        queue = await Scheduler.build_queue()
        total = len(queue)
        current_index = schedule.get("current_index", 0) % total if total else 0
        next_run = schedule.get("next_run_iso") or "not scheduled yet"

        active_sources = sum(1 for s in sources if s.get("enabled", True))
        active_channels = sum(1 for c in channels if c.get("enabled", True))

        lines = [
            "🤖 <b>BOT STATUS</b>",
            "",
            "Bot: ✅ Online",
            f"Scheduler: {'🟢 Running' if scheduler.is_running() else '🔴 Stopped'}",
            "",
            f"Interval: {settings['interval_minutes']} minutes",
            "",
            f"Source channels: {active_sources} (of {len(sources)})",
            f"Destination channels: {active_channels} (of {len(channels)})",
            "",
            f"Posts loaded: {len(posts)}",
            "",
            f"Current post: #{current_index + 1 if total else 0}",
            f"Next post: #{(current_index % total) + 1 if total else 0}",
            "",
            f"Next run:\n{esc(next_run)}",
            "",
            f"Cycle:\n{current_index + 1 if total else 0} / {total}",
            "",
            f"Source mode:\n{esc(settings['source_mode'].upper())}",
        ]
        await message.answer("\n".join(lines))

    @dp.message(Command("next"))
    async def cmd_next(message: Message) -> None:
        queue = await Scheduler.build_queue()
        if not queue:
            await message.answer("⏭ <b>NEXT POST</b>\n\nNo posts are currently queued.")
            return
        schedule = await storage.get_schedule()
        index = schedule.get("current_index", 0) % len(queue)
        post = queue[index]
        sources = await storage.get_sources()
        src = next((s for s in sources if s["chat_id"] == post["source_chat_id"]), None)
        src_name = src.get("title") if src else str(post["source_chat_id"])
        post_type = "ALBUM" if post.get("type") == "album" else "SINGLE POST"
        next_run = schedule.get("next_run_iso") or "not scheduled yet"

        await message.answer(
            "⏭ <b>NEXT POST</b>\n\n"
            f"Post: #{index + 1}\n"
            f"Source: {esc(src_name)}\n"
            f"Type: {esc(post_type)}\n\n"
            f"Scheduled:\n{esc(next_run)}"
        )

    # -- automatic new-post capture ------------------------------------------

    @dp.channel_post()
    async def on_channel_post(message: Message) -> None:
        settings = await storage.get_settings()
        auto_queue = settings.get("auto_queue_new_posts")
        if auto_queue is None:
            auto_queue = CONFIG.auto_queue_new_posts
        if not auto_queue:
            return

        sources = await storage.get_sources()
        source_ids = {s["chat_id"] for s in sources if s.get("enabled", True)}
        if message.chat.id not in source_ids:
            return  # ignore sources that are not configured

        posts = await storage.get_posts()
        existing_pairs = {
            (p["source_chat_id"], mid) for p in posts for mid in p["message_ids"]
        }
        if (message.chat.id, message.message_id) in existing_pairs:
            return  # avoid duplicate IDs

        media_group_id = message.media_group_id
        if media_group_id:
            # Try to append to an existing in-progress album entry for this group.
            match = next(
                (
                    p
                    for p in posts
                    if p.get("media_group_id") == media_group_id
                    and p["source_chat_id"] == message.chat.id
                ),
                None,
            )
            if match:
                if message.message_id not in match["message_ids"]:
                    match["message_ids"].append(message.message_id)
                    match["message_ids"].sort()
            else:
                posts.append(
                    {
                        "post_uid": storage.new_uid(),
                        "source_chat_id": message.chat.id,
                        "message_ids": [message.message_id],
                        "type": "album",
                        "media_group_id": media_group_id,
                        "added_at": storage.now_iso(),
                    }
                )
        else:
            posts.append(
                {
                    "post_uid": storage.new_uid(),
                    "source_chat_id": message.chat.id,
                    "message_ids": [message.message_id],
                    "type": "single",
                    "added_at": storage.now_iso(),
                }
            )

        await storage.save_posts(posts)
        logger.info(
            "[INFO] Captured new post from source %s (message %s)",
            message.chat.id,
            message.message_id,
        )

    # -- fallback for unknown commands from non-owners ----------------------

    @dp.message(F.text.startswith("/"))
    async def on_unknown_command(message: Message) -> None:
        is_owner = message.from_user is not None and message.from_user.id == CONFIG.owner_id
        if not is_owner:
            await message.answer("⛔ Unknown command or restricted to the bot owner. Send /help.")
# ============================================================
# ROUTE MANAGEMENT
# ============================================================

@router.message(Command("addroute"))
async def cmd_addroute(message: Message) -> None:

    if not is_owner(message):
        await message.reply("❌ Owner only.")
        return

    parts = (message.text or "").split()

    if len(parts) != 3:
        await message.reply(
            "Usage:\n"
            "/addroute <source_id> <destination_id>\n\n"
            "Example:\n"
            "/addroute -1001111111111 -1002111111111"
        )
        return

    try:
        source_id = int(parts[1])
        destination_id = int(parts[2])

    except ValueError:
        await message.reply(
            "❌ Source ID and destination ID must be numbers."
        )
        return

    added = await storage.add_route(
        source_id,
        destination_id
    )

    if added:
        await message.reply(
            "✅ Route added.\n\n"
            f"Source: <code>{source_id}</code>\n"
            f"Destination: <code>{destination_id}</code>"
        )
    else:
        await message.reply(
            "ℹ️ This route already exists."
        )


@router.message(Command("removeroute"))
async def cmd_removeroute(message: Message) -> None:

    if not is_owner(message):
        await message.reply("❌ Owner only.")
        return

    parts = (message.text or "").split()

    if len(parts) != 3:
        await message.reply(
            "Usage:\n"
            "/removeroute <source_id> <destination_id>"
        )
        return

    try:
        source_id = int(parts[1])
        destination_id = int(parts[2])

    except ValueError:
        await message.reply(
            "❌ Invalid channel ID."
        )
        return

    removed = await storage.remove_route(
        source_id,
        destination_id
    )

    if removed:
        await message.reply(
            "✅ Route removed."
        )
    else:
        await message.reply(
            "❌ Route not found."
        )


@router.message(Command("routes"))
async def cmd_routes(message: Message) -> None:

    if not is_owner(message):
        await message.reply("❌ Owner only.")
        return

    routes = await storage.get_routes()

    if not routes:
        await message.reply(
            "📭 No source → destination routes configured."
        )
        return

    lines = [
        "🔀 <b>Source → Destination Routes</b>",
        ""
    ]

    for route in routes:

        source_id = route.get("source_id")

        destinations = route.get(
            "destinations",
            []
        )

        lines.append(
            f"📥 <code>{source_id}</code>"
        )

        for destination_id in destinations:

            lines.append(
                f"   └── 📤 <code>{destination_id}</code>"
            )

        lines.append("")

    await message.reply(
        "\n".join(lines)
    )


@router.message(Command("clearoutes"))
async def cmd_clearoutes(message: Message) -> None:

    if not is_owner(message):
        await message.reply("❌ Owner only.")
        return

    await storage.clear_routes()

    await message.reply(
        "🗑 All source → destination routes removed."
)
