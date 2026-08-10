"""
handlers.py

All Telegram command handlers, plus the channel_post listener that captures
new video message IDs from the source channel as they arrive (the only
Bot-API-legal way to build the sequence going forward -- see README for the
historical-backfill limitation).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import storage
from config import CONFIG
from utils import log

router = Router()

# Filled in by bot.py after the scheduler is constructed, so handlers can
# call scheduler.enable() / disable() / reset() without a circular import.
scheduler = None


def bind_scheduler(sched) -> None:
    global scheduler
    scheduler = sched


def is_owner(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == CONFIG.owner_id


async def deny(message: Message) -> None:
    await message.reply("You are not authorized to use this command.")


# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.reply(
        "🤖 Telegram Video Auto Distributor Bot\n\n"
        "I copy one video from the source channel to all configured "
        "destination channels every "
        f"{CONFIG.interval_minutes} minutes, on an endless {CONFIG.total_videos}-video cycle.\n\n"
        "Send /help to see available commands."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📋 Commands\n\n"
        "/status - show current bot & scheduler status\n"
        "/next - show which video is up next\n"
    )
    if is_owner(message):
        text += (
            "\nOwner-only:\n"
            "/addchannel <id> - add a destination channel\n"
            "/removechannel <id> - remove a destination channel\n"
            "/channels - list destination channels\n"
            "/scan - explain/prepare source video collection\n"
            "/importvideos - reply to a videos.json attachment to import IDs\n"
            "/startschedule - start automatic scheduling\n"
            "/stopschedule - stop scheduling (progress kept)\n"
            "/reset - reset the sequence back to video #1 (confirmation required)\n"
            "/reload - reload state from disk\n"
        )
    await message.reply(text)


# ---------------------------------------------------------------------------
# Status / next
# ---------------------------------------------------------------------------

@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    snap = await scheduler.status_snapshot()
    sch = snap["schedule"]
    current_video = sch["current_index"] + 1
    next_run = sch.get("next_run")
    next_run_str = "not scheduled"
    if next_run:
        try:
            dt = datetime.fromisoformat(next_run)
            next_run_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            next_run_str = next_run

    text = (
        "🤖 Bot Status: ONLINE\n\n"
        f"📦 Total Videos: {snap['total_videos']}\n"
        f"▶️ Current Video: {current_video}/{snap['total_videos'] or CONFIG.total_videos}\n"
        f"🔁 Cycle: {sch.get('cycle', 1)}\n"
        f"📢 Destination Channels: {snap['total_channels']}\n"
        f"⏰ Interval: {CONFIG.interval_minutes} minutes\n"
        f"🕐 Next Video: {next_run_str}\n"
        f"⚙️ Scheduler: {'RUNNING' if sch.get('running') else 'STOPPED'}"
    )
    await message.reply(text)


@router.message(Command("next"))
async def cmd_next(message: Message) -> None:
    videos_data = await storage.get_videos()
    videos = videos_data.get("videos", [])
    schedule = await storage.get_schedule()
    idx = schedule["current_index"]
    if not videos:
        await message.reply("No videos loaded yet. Use /scan for instructions.")
        return
    if idx >= len(videos):
        idx = 0
    await message.reply(
        f"Next up: video #{idx + 1}/{len(videos)} (source message_id={videos[idx]})"
    )


# ---------------------------------------------------------------------------
# Channel management (owner only)
# ---------------------------------------------------------------------------

@router.message(Command("addchannel"))
async def cmd_addchannel(message: Message, command: CommandObject, bot: Bot) -> None:
    if not is_owner(message):
        await deny(message)
        return
    if not command.args:
        await message.reply("Usage: /addchannel -1001234567890")
        return
    try:
        channel_id = int(command.args.strip().split()[0])
    except ValueError:
        await message.reply("Invalid channel ID. It should look like -1001234567890.")
        return

    try:
        chat = await bot.get_chat(channel_id)
        member = await bot.get_chat_member(channel_id, (await bot.get_me()).id)
        if member.status not in ("administrator", "creator"):
            await message.reply(
                f"I can see '{chat.title}' but I'm not an admin there yet. "
                "Please make me an admin, then try again."
            )
            return
    except Exception as e:
        await message.reply(f"Could not verify that channel: {e}")
        return

    added = await storage.add_channel(channel_id)
    if added:
        await message.reply(f"✅ Added destination channel: {chat.title} ({channel_id})")
    else:
        await message.reply("That channel is already in the list.")


@router.message(Command("removechannel"))
async def cmd_removechannel(message: Message, command: CommandObject) -> None:
    if not is_owner(message):
        await deny(message)
        return
    if not command.args:
        await message.reply("Usage: /removechannel -1001234567890")
        return
    try:
        channel_id = int(command.args.strip().split()[0])
    except ValueError:
        await message.reply("Invalid channel ID.")
        return

    removed = await storage.remove_channel(channel_id)
    if removed:
        await message.reply(f"✅ Removed destination channel {channel_id}")
    else:
        await message.reply("That channel was not in the list.")


@router.message(Command("channels"))
async def cmd_channels(message: Message) -> None:
    if not is_owner(message):
        await deny(message)
        return
    channels = await storage.get_channels()
    if not channels:
        await message.reply("No destination channels configured yet. Use /addchannel.")
        return
    lines = "\n".join(f"- {c}" for c in channels)
    await message.reply(f"📢 Destination channels ({len(channels)}):\n{lines}")


# ---------------------------------------------------------------------------
# Source video collection
# ---------------------------------------------------------------------------

@router.message(Command("scan"))
async def cmd_scan(message: Message) -> None:
    if not is_owner(message):
        await deny(message)
        return
    videos_data = await storage.get_videos()
    count = len(videos_data.get("videos", []))
    await message.reply(
        "ℹ️ Telegram Bot API limitation\n\n"
        "A bot account cannot enumerate arbitrary old message history of a "
        "channel -- the Bot API simply does not expose that method. I can only:\n\n"
        "1) Capture new videos automatically as they're posted from now on "
        "(already active, since I'm an admin in the source channel), and\n"
        "2) Import an existing list of message IDs that you prepare "
        "separately (e.g. with a Telegram Desktop export or a userbot "
        "script you control), via /importvideos.\n\n"
        f"Currently loaded: {count} video(s).\n\n"
        "See README.md section 'Telegram API limitations' for the full "
        "explanation and a step-by-step way to prepare the 1440-message list."
    )


@router.message(Command("importvideos"))
async def cmd_importvideos(message: Message, bot: Bot) -> None:
    if not is_owner(message):
        await deny(message)
        return

    target = message.reply_to_message
    if not target or not target.document:
        await message.reply(
            "Reply to a JSON file (e.g. videos.json, format: "
            '{"videos": [101, 102, 103]}) with /importvideos to import it.'
        )
        return

    try:
        file = await bot.get_file(target.document.file_id)
        buf = await bot.download_file(file.file_path)
        payload = json.loads(buf.read().decode("utf-8"))
        ids = payload.get("videos", payload if isinstance(payload, list) else [])
        ids = [int(x) for x in ids]
    except Exception as e:
        await message.reply(f"Could not parse that file as a video ID list: {e}")
        return

    added = await storage.import_video_ids(ids)
    videos_data = await storage.get_videos()
    await message.reply(
        f"✅ Imported {added} new video ID(s). "
        f"Total now loaded: {len(videos_data['videos'])}."
    )


@router.channel_post(F.video | F.document)
async def capture_channel_video(message: Message) -> None:
    """Passively captures new video posts from the source channel, in order,
    as Telegram delivers them -- this is the mechanism that keeps the
    sequence growing correctly going forward."""
    if message.chat.id != CONFIG.source_channel_id:
        return
    if not message.video:
        return  # only true video messages count toward the 1440 sequence
    added = await storage.add_video_id(message.message_id)
    if added:
        log.info(f"Captured new source video: message_id={message.message_id}")


# ---------------------------------------------------------------------------
# Scheduler control (owner only)
# ---------------------------------------------------------------------------

@router.message(Command("startschedule"))
async def cmd_startschedule(message: Message) -> None:
    if not is_owner(message):
        await deny(message)
        return
    videos_data = await storage.get_videos()
    channels = await storage.get_channels()
    if not videos_data.get("videos"):
        await message.reply("Cannot start: no videos loaded. Use /scan for instructions.")
        return
    if not channels:
        await message.reply("Cannot start: no destination channels configured. Use /addchannel.")
        return
    await scheduler.enable()
    await message.reply("▶️ Scheduler started.")


@router.message(Command("stopschedule"))
async def cmd_stopschedule(message: Message) -> None:
    if not is_owner(message):
        await deny(message)
        return
    await scheduler.disable()
    await message.reply("⏸️ Scheduler stopped. Progress has been preserved.")


@router.message(Command("reset"))
async def cmd_reset(message: Message, command: CommandObject) -> None:
    if not is_owner(message):
        await deny(message)
        return
    if (command.args or "").strip().upper() != "YES":
        await message.reply(
            "⚠️ This will reset the video sequence to Video #1.\n\n"
            "Are you sure? Send: /reset YES"
        )
        return
    await scheduler.reset()
    await message.reply("🔄 Sequence has been reset to Video #1.")


@router.message(Command("reload"))
async def cmd_reload(message: Message) -> None:
    if not is_owner(message):
        await deny(message)
        return
    # storage.py reads fresh from disk on every call, so "reload" is just a
    # user-facing confirmation that state is not cached.
    snap = await scheduler.status_snapshot()
    await message.reply(
        f"🔄 Reloaded from disk.\n"
        f"Videos: {snap['total_videos']} | Channels: {snap['total_channels']} | "
        f"Current index: {snap['schedule']['current_index']}"
    )
