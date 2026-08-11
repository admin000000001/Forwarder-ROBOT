# Forwarder-ROBOT

A production-ready Telegram bot that automatically distributes posts from
one or more **source channels** to one or more **destination channels** on
a fixed schedule (default: 1 post every 30 minutes). Fully controllable
from Telegram — no server console access needed after setup.

Runs on Termux/Android and on any regular Linux VPS.

---

## 1. Installation (Linux / VPS)

```bash
git clone <your-repo-url> Forwarder-ROBOT
cd Forwarder-ROBOT
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your BOT_TOKEN and OWNER_ID
python3 bot.py
```

## 2. Installation (Termux)

```bash
pkg update && pkg upgrade
pkg install python git
git clone <your-repo-url> Forwarder-ROBOT
cd Forwarder-ROBOT
pip install -r requirements.txt
cp .env.example .env
nano .env   # fill in BOT_TOKEN and OWNER_ID
python bot.py
```

## 3. Python requirements

- Python 3.12+ (3.14 also supported — dependencies were chosen to avoid
  packages that force native/Rust compilation, which is a common source
  of Termux install failures).
- See `requirements.txt`: `aiogram`, `python-dotenv`, `aiofiles` only.
  No Flask, FastAPI, psutil, or database drivers.

## 4. `.env` setup

Copy `.env.example` to `.env` and fill in:

| Variable | Meaning | Default |
|---|---|---|
| `BOT_TOKEN` | Your bot's token from @BotFather | *(required)* |
| `OWNER_ID` | Your numeric Telegram user ID | *(required)* |
| `INTERVAL_MINUTES` | Minutes between posts | `30` |
| `TOTAL_POSTS` | Informational target post count (display only — actual cycle length is however many posts are loaded) | `1440` |
| `AUTO_QUEUE_NEW_POSTS` | Auto-capture new posts from sources | `true` |
| `SOURCE_MODE` | `round_robin` or `sequential` | `round_robin` |
| `MISSED_SCHEDULE_POLICY` | Only `next` is supported — never bursts missed posts | `next` |

`INTERVAL_MINUTES` and `SOURCE_MODE` can also be changed live from Telegram
with `/setinterval` and `/setsourcemode` — those overrides are stored in
`data/settings.json` and take priority over `.env`.

**Never** put real credentials in `.env.example` — only `.env` (which is
git-ignored).

## 5. How to obtain your Telegram user ID

Message [@userinfobot](https://t.me/userinfobot) (or any similar ID bot)
on Telegram — it replies with your numeric user ID. Use that as
`OWNER_ID`.

## 6. How to create a bot

1. Message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts.
3. Copy the token it gives you into `BOT_TOKEN`.

## 7. Adding the bot as a source-channel admin

1. Open the source channel → Administrators → Add Admin.
2. Add your bot, granting at least **"Post Messages"** / read access.
3. Get the channel's numeric ID (forward a message from it to
   [@userinfobot](https://t.me/userinfobot), or use any ID-lookup bot) —
   it looks like `-1001234567890`.

## 8. Adding the bot to destination channels

Same as above: add the bot as **administrator** (it needs "Post Messages"
permission) in every channel you want content copied into.

## 9. `/addsource`

```
/addsource -1001234567890
```

Verifies the bot can access the chat and is an administrator, then stores
it in `data/sources.json`.

## 10. `/sources`

Lists every configured source with its live status (`✅ Active` /
`⚠️ Unavailable`).

## 11. `/addchannel`

```
/addchannel -1009876543210
```

Same verification flow as `/addsource`, but for destinations.

## 12. `/channels`

Lists all destination channels and their status.

## 13. `/importposts`

Since the Bot API cannot enumerate a channel's old history, reply to a
`.json` file (uploaded as a Telegram document) with `/importposts`:

```json
[
  { "source_chat_id": -1001234567890, "message_id": 101 },
  { "source_chat_id": -1001234567890, "message_id": 102 }
]
```

Invalid entries, unknown sources, and duplicates are skipped and reported
— the import never crashes on bad data.

## 14. Scheduler setup

```
/startschedule    start distribution
/stopschedule     pause distribution
/status           see current state
/next             preview the next post
```

Only one scheduler loop can ever run at a time (duplicate-protected), and
if the bot was running the scheduler when it stopped, it automatically
resumes on the next startup.

## 15. 30-minute configuration

Default interval is 30 minutes / 48 posts a day. Change it any time with:

```
/setinterval 30
```

## 16. Restart behavior

Progress (`data/schedule.json`) is saved **after** each successful post,
so a crash or Termux stop never loses the sequence: post #127 sent, bot
restarts → next post is #128, not #1.

If the bot was offline through several scheduled intervals,
`MISSED_SCHEDULE_POLICY=next` ensures it sends only the **next** post once
it's back online — it never floods channels by dumping every missed post
at once.

## 17. Troubleshooting

- **"Bad Request: can't parse entities"** — should never happen; every
  dynamic value sent in an HTML-mode message is escaped with
  `html.escape`. If you see this, please file an issue with the exact
  command used.
- **Source/destination shows "⚠️ Unavailable"** — the bot lost admin
  rights, was removed, or the channel was deleted. Re-add the bot as
  admin, then use `/reload`.
- **`pydantic-core` build errors on Termux** — make sure you're using the
  `requirements.txt` from this repo; it's pinned to versions with
  prebuilt wheels and avoids forcing a source build.
- **Nothing gets posted** — check `/status`: you need at least one active
  source with loaded posts (`/importposts` or auto-capture) *and* at
  least one active destination, and the scheduler must be running
  (`/startschedule`).

## 18. Telegram Bot API history limitation

A normal Bot API bot **cannot** fetch a channel's arbitrary old message
history — there is no such method. This bot supports two ways to get
content in:

1. **Automatic capture** — while the bot is an admin in a source channel,
   every *new* post is captured automatically.
2. **`/importposts`** — for existing content, export the message IDs you
   want (e.g. from your own records) into a JSON file and import them.

## 19. Termux keep-alive instructions

Termux may kill background processes to save battery. To keep the bot
alive:

```bash
termux-wake-lock
```

Also disable battery optimization for Termux in Android settings, and
consider running the bot inside `tmux` so it survives Termux app restarts:

```bash
pkg install tmux
tmux new -s forwarder
python bot.py
# detach with Ctrl+B then D; reattach later with: tmux attach -t forwarder
```

## 20. Security

- `BOT_TOKEN` and `OWNER_ID` are read only from environment variables —
  never hardcoded, never logged.
- Only `OWNER_ID` can manage sources, destinations, imports, and the
  scheduler. Everyone else can only use `/start`, `/help`, `/status`,
  `/next`.
- This bot only distributes content from channels its owner already
  administers — it does not attempt to bypass Telegram's permission model
  or access private channels it hasn't been added to.

---

## Project structure

```
Forwarder-ROBOT/
├── bot.py              entry point / startup sequence
├── config.py            .env loading & validation
├── handlers.py           all command handlers + auto-capture
├── scheduler.py          distribution loop, queue building, resume logic
├── storage.py            atomic JSON persistence
├── telegram_utils.py      escaping, verification, retrying copy calls
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── data/
    ├── sources.json
    ├── channels.json
    ├── posts.json
    ├── schedule.json
    └── settings.json
```
