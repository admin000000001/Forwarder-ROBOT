from __future__ import annotations

import json
import logging
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

import storage
from config import CONFIG
from telegram_utils import esc, verify_chat_access

logger = logging.getLogger("forwarder")

# IMPORTANT:
# Router MUST be created at module level.
router = Router()


# ============================================================
# HELPERS
# ============================================================

def is_owner(message: Message) -> bool:
    try:
        return int(message.from_user.id) == int(CONFIG.owner_id)
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
        "/importposts — reply to a JSON file\n"
        "/scan\n\n"

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
        f"Scheduler: <code>{'RUNNING' if schedule.get('running') else 'STOPPED'}</code>\n"
        f"Current index: <code>{schedule.get('current_index', 0)}</code>\n"
        f"Next run: <code>{esc(str(schedule.get('next_run_iso') or 'N/A'))}</code>"
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

    index = schedule.get("current_index", 0)

    if index >= len(posts):
        index = 0

    post = posts[index]

    await message.reply(
        "⏭ <b>Next Post</b>\n\n"
        f"Post index: <code>{index}</code>\n"
        f"Source: <code>{post.get('source_chat_id')}</code>\n"
        f"Message IDs: <code>{post.get('message_ids')}</code>"
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

    if any(int(s["chat_id"]) == chat_id for s in sources):
        await message.reply("⚠️ This source channel is already configured.")
        return

    result = await verify_chat_access(bot, chat_id)

    if not result.ok:
        await message.reply(
            "❌ Cannot access this source channel.\n\n"
            f"<code>{chat_id}</code>\n"
            f"Error: {esc(str(result.error))}\n\n"
            "Make sure the bot is an admin in the source channel."
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
        await message.reply("Usage: <code>/removesource -1001234567890</code>")
        return

    sources = await storage.get_sources()

    new_sources = [
        s for s in sources
        if int(s.get("chat_id")) != chat_id
    ]

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
            f"{i}. {status} "
            f"{format_chat(source)}"
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
        await message.reply("Usage: <code>/sourceinfo -1001234567890</code>")
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

    if any(int(c["chat_id"]) == chat_id for c in channels):
        await message.reply("⚠️ Destination already exists.")
        return

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
        await message.reply("Usage: <code>/removechannel -1001234567890</code>")
        return

    channels = await storage.get_channels()

    new_channels = [
        c for c in channels
        if int(c.get("chat_id")) != chat_id
    ]

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
        await message.reply("📭 No destination channels configured.")
        return

    lines = ["📤 <b>Destination Channels</b>\n"]

    for i, channel in enumerate(channels, 1):
        status = "🟢" if channel.get("enabled", True) else "🔴"

        lines.append(
            f"{i}. {status} "
            f"{format_chat(channel)}"
        )

    await message.reply("\n".join(lines))


@router.message(Command("clearchannels"))
async def cmd_clearchannels(message: Message) -> None:
    if not await owner_only(message):
        return

    await storage.save_channels([])

    await message.reply("✅ All destination channels removed.")


@router.message(Command("channelinfo"))
async def cmd_channelinfo(message: Message, bot: Bot) -> None:
    if not await owner_only(message):
        return

    chat_id = parse_chat_id(message)

    if chat_id is None:
        await message.reply("Usage: <code>/channelinfo -1001234567890</code>")
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
# ROUTE MANAGEMENT
# ============================================================

async def get_routes() -> list[dict[str, Any]]:
    """
    Routes are stored inside settings.json:

    {
        "routes": [
            {
                "source_id": -1001,
                "destinations": [-1002, -1003]
            }
        ]
    }
    """

    settings = await storage.get_settings()

    routes = settings.get("routes", [])

    if not isinstance(routes, list):
        routes = []

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
            "<code>/addroute SOURCE_ID DESTINATION_ID</code>\n\n"
            "Example:\n"
            "<code>/addroute -1001111111111 -1002111111111</code>"
        )
        return

    try:
        source_id = int(parts[1])
        destination_id = int(parts[2])
    except ValueError:
        await message.reply("❌ Both IDs must be numbers.")
        return

    routes = await get_routes()

    route = None

    for item in routes:
        if int(item.get("source_id")) == source_id:
            route = item
            break

    if route is None:
        route = {
            "source_id": source_id,
            "destinations": [],
        }
        routes.append(route)

    destinations = route.setdefault("destinations", [])

    if destination_id in destinations:
        await message.reply("⚠️ This route already exists.")
        return

    destinations.append(destination_id)

    await save_routes(routes)

    await message.reply(
        "✅ <b>Route added</b>\n\n"
        f"Source:\n<code>{source_id}</code>\n\n"
        f"Destination:\n<code>{destination_id}</code>"
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
        await message.reply("❌ Invalid channel ID.")
        return

    routes = await get_routes()

    changed = False

    for route in routes:
        if int(route.get("source_id")) == source_id:
            destinations = route.get("destinations", [])

            if destination_id in destinations:
                destinations.remove(destination_id)
                changed = True

    routes = [
        r for r in routes
        if r.get("destinations")
    ]

    await save_routes(routes)

    if changed:
        await message.reply("✅ Route removed.")
    else:
        await message.reply("❌ Route not found.")


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

    lines = ["🔀 <b>Source → Destination Routes</b>\n"]

    for i, route in enumerate(routes, 1):
        source_id = route.get("source_id")
        destinations = route.get("destinations", [])

        lines.append(
            f"<b>{i}. Source:</b> <code>{source_id}</code>"
        )

        for dest in destinations:
            lines.append(
                f"   └── <code>{dest}</code>"
            )

        lines.append("")

    await message.reply("\n".join(lines))


@router.message(Command("clearroutes"))
async def cmd_clearroutes(message: Message) -> None:
    if not await owner_only(message):
        return

    await save_routes([])

    await message.reply("✅ All routes cleared.")


# ============================================================
# SCHEDULER
# ============================================================

@router.message(Command("startschedule"))
async def cmd_startschedule(message: Message, scheduler) -> None:
    if not await owner_only(message):
        return

    ok, text = await scheduler.start()

    await message.reply(
        ("✅ " if ok else "⚠️ ") + esc(text)
    )


@router.message(Command("stopschedule"))
async def cmd_stopschedule(message: Message, scheduler) -> None:
    if not await owner_only(message):
        return

    ok, text = await scheduler.stop()

    await message.reply(
        ("✅ " if ok else "⚠️ ") + esc(text)
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
        await message.reply("❌ Interval must be a positive number.")
        return

    settings = await storage.get_settings()
    settings["interval_minutes"] = minutes

    await storage.save_settings(settings)

    await message.reply(
        f"✅ Scheduler interval set to <b>{minutes} minutes</b>."
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
            "or\n"
            "<code>/setsourcemode sequential</code>"
        )
        return

    mode = parts[1].lower()

    if mode not in ("round_robin", "sequential"):
        await message.reply(
            "❌ Mode must be:\n"
            "<code>round_robin</code>\n"
            "or\n"
            "<code>sequential</code>"
        )
        return

    settings = await storage.get_settings()
    settings["source_mode"] = mode

    await storage.save_settings(settings)

    await message.reply(
        f"✅ Source mode set to <code>{mode}</code>."
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message, scheduler) -> None:
    if not await owner_only(message):
        return

    await scheduler.reset()

    await message.reply(
        "✅ Scheduler sequence reset to the first post."
    )


@router.message(Command("reload"))
async def cmd_reload(message: Message) -> None:
    if not await owner_only(message):
        return

    await message.reply(
        "✅ Configuration/state will be reloaded on the next scheduler cycle."
    )


# ============================================================
# POSTS
# ============================================================

@router.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    if not await owner_only(message):
        return

    posts = await storage.get_posts()

    await message.reply(
        "ℹ️ <b>Telegram Bot API limitation</b>\n\n"
        "A normal bot cannot retrieve arbitrary old channel history.\n\n"
        "You can:\n"
        "1. Automatically capture new posts from configured source channels.\n"
        "2. Import existing post IDs using /importposts.\n\n"
        f"Currently loaded: <b>{len(posts)} post(s)</b>"
    )


@router.message(Command("importposts"))
async def cmd_importposts(message: Message, bot: Bot) -> None:
    if not await owner_only(message):
        return

    if not message.reply_to_message:
        await message.reply(
            "❌ Reply to a JSON file with:\n"
            "<code>/importposts</code>"
        )
        return

    document = message.reply_to_message.document

    if not document:
        await message.reply("❌ The replied message must contain a JSON file.")
        return

    try:
        file = await bot.get_file(document.file_id)

        temp_path = "/tmp/forwarder_import.json"

        await bot.download_file(
            file.file_path,
            temp_path,
        )

        with open(temp_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            imported_posts = data.get("posts", [])
        elif isinstance(data, list):
            imported_posts = data
        else:
            imported_posts = []

        if not isinstance(imported_posts, list):
            imported_posts = []

        await storage.save_posts(imported_posts)

        await message.reply(
            f"✅ Imported <b>{len(imported_posts)}</b> post(s)."
        )

    except Exception as exc:
        logger.exception("Import failed")

        await message.reply(
            "❌ Import failed:\n\n"
            f"<code>{esc(str(exc))}</code>"
        )


# ============================================================
# REGISTER
# ============================================================

def register_handlers(dp, bot: Bot, scheduler) -> None:
    """
    Register all handlers on the dispatcher.

    We pass scheduler/bot through handler closure so handlers can
    access them without global variables.
    """

    # Remove previously attached handlers if possible.
    dp.include_router(router)

    # NOTE:
    # The scheduler argument is injected by wrapping specific handlers
    # through dispatcher data in bot.py / middleware in a normal setup.
    #
    # To keep compatibility with your current bot.py, expose objects
    # through router workflow below.

    # Store references for compatibility.
    router["bot"] = bot
    router["scheduler"] = scheduler
