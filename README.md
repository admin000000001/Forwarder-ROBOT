# Telegram 30-Minute Video Auto Distributor Bot

Copies one video from a private source channel to any number of destination
channels every 30 minutes, forever, using Telegram's native `copyMessage`
(no re-upload). Position survives restarts; nothing is ever sent twice.

---

## 1. Create the bot

1. Open **@BotFather** on Telegram.
2. Send `/newbot`, follow the prompts.
3. Copy the token it gives you — this is `BOT_TOKEN`.

## 2. Find your OWNER_ID

1. Open **@userinfobot** (or any similar "what's my ID" bot).
2. Send it any message; it replies with your numeric Telegram user ID.
3. That number is `OWNER_ID`.

## 3. Get the Source Channel ID

1. Add your bot to the private source channel **as an administrator**
   (needs "Post Messages" is not required, but it does need to be able to
   read channel posts and you should grant it full admin rights to be safe).
2. Forward any message from that channel to **@userinfobot** (or
   **@JsonDumpBot** / **@RawDataBot**) — it will show a chat ID like
   `-1001234567890`. That's `SOURCE_CHANNEL_ID`.

## 4. Add the bot as admin everywhere

- Source channel: admin (required to read channel posts and copy from it).
- Every destination channel: admin, with "Post Messages" permission.

## 5. Install Python

**Termux:**
```bash
pkg update && pkg upgrade
pkg install python
```

**Linux / VPS:** install Python 3.12+ via your distro's package manager.

## 6. Install dependencies

```bash
cd telegram_video_bot
pip install -r requirements.txt
```
(On Termux, use the same command — no extra steps needed.)

## 7. Prepare the 1440-video message ID list

**⚠️ Telegram API limitation, read this carefully:**

A **bot account** cannot fetch arbitrary old history of a channel — the Bot
API has no "list all past messages" method. This is a Telegram platform
limitation, not something this project can work around. This bot does **not**
pretend otherwise. It gives you two honest paths:

**Path A — going forward (automatic, zero effort):**
Once the bot is an admin in the source channel, it automatically records the
`message_id` of every new video posted there, in order, into `data/videos.json`.
If you're setting this up *before* posting your 1440 videos, just add the bot
first and then post the videos — they'll be captured as they go.

**Path B — for videos already posted (manual, one-time):**
You need to obtain the message IDs yourself, since only you (as a full user,
not the bot) can browse existing channel history. Two practical ways:
- Open the channel in **Telegram Desktop**, and note that each message has a
  "Copy Message Link" option — the trailing number in that link
  (`https://t.me/c/XXXXXXXXXX/<message_id>`) is the message ID.
- Or use a **userbot script** you control (e.g. with Telethon/Pyrogram under
  your own account, not this bot) to iterate `get_chat_history` and dump the
  video message IDs to a JSON file. This project intentionally does not
  include a userbot, since that runs under a different set of Telegram rules
  than a Bot API bot.

Once you have the list, create a file like:
```json
{
    "videos": [101, 102, 103, 104]
}
```
Send it to the bot as a document, reply to it with:
```
/importvideos
```
and the IDs will be merged into `data/videos.json` (duplicates are ignored).

Run `/scan` any time for an in-chat reminder of this process and a current count.

## 8. Configure `.env`

```bash
cp .env.example .env
```
Edit `.env`:
```
BOT_TOKEN=123456:ABC-your-token
OWNER_ID=123456789
SOURCE_CHANNEL_ID=-1001234567890

INTERVAL_MINUTES=30
TOTAL_VIDEOS=1440
SHUFFLE=false
MISSED_SCHEDULE_POLICY=next
```
The bot validates these at startup and exits with a clear error if anything
required is missing.

## 9. Start the bot

```bash
python bot.py
```

## 10. Add destination channels

In your DM with the bot (as the owner):
```
/addchannel -1001111111111
/addchannel -1002222222222
/channels
```
The bot verifies it can actually post in each channel before accepting it.

## 11. Start the schedule

```
/startschedule
```
Sends video #1 on the next 30-minute grid tick, then #2, #3, ... wrapping
back to #1 after #1440, forever.

## 12. Check status

```
/status
```
```
🤖 Bot Status: ONLINE
📦 Total Videos: 1440
▶️ Current Video: 37/1440
🔁 Cycle: 1
📢 Destination Channels: 5
⏰ Interval: 30 minutes
🕐 Next Video: 12:30 PM
⚙️ Scheduler: RUNNING
```

## 13. Reset the sequence (if you ever need to)

```
/reset
```
Bot asks for confirmation; confirm with:
```
/reset YES
```

## 14. Restart recovery

If the process restarts (crash, redeploy, `pkg upgrade`, VPS reboot):
- `data/schedule.json` already has the current index, so it **resumes from
  the correct video** — it never jumps back to #1.
- If a delivery to some channels succeeded and others hadn't been reached
  yet when the process died, the bot resumes that *same* video and only
  sends to the channels that hadn't received it yet — no duplicates, no
  skips.
- If the bot was offline through one or more entire 30-minute slots,
  `MISSED_SCHEDULE_POLICY=next` (the default) makes it resume with just the
  next video instead of blasting out every video it missed.

## 15. Running 24/7 on Termux

Termux kills background processes aggressively unless kept alive. Use `tmux`:
```bash
pkg install tmux
tmux new -s videobot
python bot.py
# detach: Ctrl+B then D
```
Reattach any time with:
```bash
tmux attach -t videobot
```
Also consider `termux-wake-lock` to stop Android from killing Termux, and
disabling battery optimization for Termux in Android settings.

## 16. Running on a VPS / Koyeb / Render / Railway

Any host that can run `python bot.py` as a long-lived process works —
this is a plain polling bot with no web server, so no port needs to be
exposed. Use each platform's "worker" / "background process" service type
(not a web service), set the environment variables from `.env.example` in
the platform's dashboard, and set the start command to `python bot.py`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Bot exits immediately with `[CONFIG ERROR]` | Missing/invalid `.env` value | Check the exact variable named in the error |
| "BOT_TOKEN is invalid" | Wrong/revoked token | Get a fresh token from @BotFather |
| "Cannot access SOURCE_CHANNEL_ID" | Bot not added to channel, or wrong ID | Re-check step 3 and re-add the bot |
| "Bot is not an administrator in source channel" | Bot added but not promoted | Promote it to admin |
| `/addchannel` says it can't verify the channel | Bot not admin there yet | Add & promote it, then retry |
| Videos not appearing in `/status` count | Nothing captured yet and nothing imported | See section 7 |
| A destination channel keeps failing | Bot lost admin, or was removed | Check `/channels` and re-add/re-promote |
| Duplicate video sent after a crash | Should not happen — file a bug | Check `bot.log` for the exact index/channel involved |

## Telegram API limitations (summary)

- Bots cannot browse arbitrary old channel history — see section 7.
- `forwardMessage` shows "Forwarded from ..."; this bot always uses
  `copyMessage` instead, which sends a clean copy with no source
  attribution, per the requirement.
- Flood limits (`RetryAfter`) are respected automatically: the bot waits the
  exact duration Telegram requests before retrying, per channel.
- Bot API rate limits mean very large numbers of destination channels will
  naturally take a few seconds longer per cycle; the scheduler accounts for
  this by scheduling the *next* run from a fixed grid, not from "whenever
  the last send finished," so a slow cycle doesn't cause creeping drift
  across days/weeks.

## Project structure

```
telegram_video_bot/
├── bot.py           # entry point: startup checks, polling, shutdown
├── config.py        # env var loading & validation
├── scheduler.py      # drift-free 30-min loop, retries, duplicate-safe delivery
├── storage.py        # atomic JSON persistence (videos/channels/schedule)
├── handlers.py       # all commands + source channel capture
├── utils.py           # logging + atomic JSON read/write helpers
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── data/
    ├── videos.json     # source video message_id sequence
    ├── channels.json   # destination channel IDs
    └── schedule.json   # current index, next run time, delivery tracking
```
