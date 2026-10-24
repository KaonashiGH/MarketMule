# MarketMule

A Discord bot built from independent features:

- **Forums:** members fill in a form, and the bot publishes a clean, tagged forum post under their name. A channel can offer one form (bug reports) or several (loadouts and trade-ups), each with its own button on the pinned post. Posts can be rated with a rubric.
- **Stalls:** each member gets one persistent shop post and manages its items with buttons.

## Project layout

```
marketmule/
  core/          shared by every feature: bot, settings, database, staff checks
  features/
    forums/      the forums feature (its __init__.py lists what each file does)
    stalls/      the stalls feature (same)
templates/
  forums/        one .json file per form
  stalls/        stalls.json: stall wording and limits
config/
  blocked_words.txt  words the bot won't post
tests/           automated tests
deploy/          server files: supervisor config and update script
run.bat          run the bot on Windows
```

Rules: `core/` never imports from `features/`, and features never import from each other.

## Commands

| Command | Who | What it does |
| --- | --- | --- |
| `/forums setup channel form` | Manage Server | Adds a form to a forum channel and rebuilds its pinned post. Run it again with another form to add a second one. |
| `/forums remove channel [form]` | Manage Server | Removes one form, or every form if none is picked. Existing posts stay. |
| `/forums list` | Manage Server | Shows which channels offer which forms. |
| `/marketmule staffrole role` | Manage Server | Sets the staff role for every feature. |
| `/marketmule icons` | Manage Server | Uploads the icons in `assets/icons/` as the bot's own emojis. Safe to run again: it only adds missing ones. |
| `/marketmule status` | Manage Server | Shows loaded features, the staff role and how many icons are uploaded. |
| `/manage` or **Apps → Manage post** | Staff | Changes a post's status or tags. Final statuses lock the post. |
| `/leaderboard form` | Everyone | Shows the top-rated posts for a form that has a rating rubric. |
| **Apps → Remove rating** | Staff | On a rating: deletes it and takes its score out of the average and leaderboard. |
| `/ratings block member [reason]` | Staff | Stops a member from rating. `/ratings unblock` undoes it, `/ratings blocked` lists them. |
| `/stalls setup channel` | Manage Server | Makes a forum the stalls channel and pins its Open my stall post. |

To show staff tools to a role without Manage Threads, add it under **Server Settings → Integrations → MarketMule**.

## Setting up a forum channel

1. Create the forum channel. For **@everyone**, turn **Create Posts** off and **Send Messages in Posts** on.
2. Give the bot's role these permissions in the channel: View Channel, Manage Channels, Manage Webhooks, Manage Threads, Send Messages, Create Public Threads, Send Messages in Threads, Embed Links, Attach Files, Read Message History, Add Reactions.
3. Run `/forums setup` once per form for that channel, then `/marketmule staffrole`.

Forms that share a channel share Discord's limit of **20 tags per forum**. Setup stops with a clear message if they don't fit.

## Editing forms

Each file in `templates/forums/` is one form. The main keys:

| Key | What it controls |
| --- | --- |
| `panel` | The form's section on the pinned post: title, text and button. |
| `pages` | One popup per page, **5 items max** each. |
| `title_format` | The post title, e.g. `[{area}] - {summary}`. |
| `header` | Answers shown in the small line at the top of the post. |
| `sections` | Headings and body of the post. `{key}` inserts an answer. Skipped questions are hidden. |
| `credit` | Footer wording, e.g. "Reported by". |
| `style` | `"text"` (default) or `"card"`: a colored box with dividers, a picture gallery and a link button. |
| `icon` | The form's icon, e.g. `"mm_pricecheck"`. Shown on the post and the Start button. |
| `thumbnail` | Card posts: `true` shows the form's icon in the top-right corner instead of in the title. |
| `banner`, `banner_on_posts` | A banner from `assets/banners/`, e.g. `"pricecheck"` for `pricecheck.png` (1200×240). It shows on the Start here post; set `banner_on_posts` to `true` to put it on every post too. |
| `card_title`, `link_button` | Card posts only: the big title line, and an answer to show as a button, e.g. `{"key": "link", "label": "View on CSFloat"}`. |
| `statuses`, `final_statuses` | Staff-only status tags, and which ones close a post. |
| `author_close` | Optional. A button in each new post that lets its author close it, e.g. "Mark as priced". Set `status`, `button_label` and `text`. |
| `rating` | Optional. A rubric members use to rate posts. See "Ratings" below. |
| `tag_emojis`, `reactions` | Emoji on tags, and reactions added to new posts. |

In sections, start a line with `> ` to quote the answer (long answers stay quoted on every line), and add `"divider": true` to draw a line above a section in card posts.

Question types: `text` (add `"style": "paragraph"` for long answers), `select` (a dropdown; add `"tag": true` to make the answer a tag), `file` (an upload box) and `note` (text only).

The bot checks every form against Discord's limits when it starts and refuses to start if one is invalid, naming the problem. After editing, deploy, then run `/forums setup` again on that channel to refresh its pinned post.

## Ratings

A form with a `rating` block gets a **Rate** button under each new post. Members score each category 1-10, and the bot posts the rating in the thread under their name and keeps an average on the post. Each member gets one rating per post (rating again updates it), and members can't rate their own posts or closed ones.

| Key | What it controls |
| --- | --- |
| `title` | First line of each posted rating, e.g. "LOADOUT LAB RATING". |
| `button_label`, `emoji` | The Rate button. The emoji also marks the average. |
| `categories` | One `{"key", "label", "description"}` per scored line. Each is worth 10 points. |
| `verdict` | Optional. `{"label", "options"}` for a pick-one verdict, e.g. SEND IT / SAVE YOUR MONEY. |
| `note` | Optional. `{"label", "placeholder", "required"}` for a written note. |
| `guide` | Optional. Text shown privately by a "How to review" button. |
| `leaderboard_min` | Ratings a post needs before it appears in `/leaderboard`. Defaults to 3. |

The rating form fits 5 inputs per page, so larger rubrics are split into steps automatically.

## Stalls

Each member can open **one** stall from the pinned post. The bot posts it under their name, with a shop window message underneath that lists their items and holds the owner's buttons:

| Button | What it does |
| --- | --- |
| ➕ Add item / ✏️ Edit item / 🗑️ Remove item | Manage listed items. Trade holds are entered as days left and shown as a local date with a countdown. |
| ✅ Mark sold | Moves an item to Recently Sold (the last 5 are shown). |
| ⚙️ Edit stall info | Title, links and negotiation status. |
| 🔼 Bump | Moves the stall to the top. Once every 12 hours. |
| ⛔ Close stall | Closes it. The owner can reopen it with Reopen stall or Open my stall. |

Only the owner and staff can use a stall's buttons. Adding an item or marking one sold also bumps the stall, at most once every 6 hours. Store links must be `https://csfloat.com/...` and trade links `https://steamcommunity.com/tradeoffer/new/...`, which blocks look-alike phishing links. Stalls with no updates for 30 days close automatically. If staff close a stall, only staff can reopen it.

**Channel setup:** set @everyone to **Create Posts: off** and **Send Messages in Posts: off**, so only the bot can post in stalls and replies can't bump them. Keep the forum sorted by **Recent Activity**.

Wording, tag names and the limits above (cooldowns, inactive days, recently sold count, longest trade hold) are in `templates/stalls/stalls.json`. Restart the bot after editing it.

## Icons

MarketMule's icons are PNGs in `assets/icons/`, named like `mm_pricecheck.png`. Run `/marketmule icons` once to upload them as the bot's own app emojis. They don't use any server emoji slots, and nobody needs the Developer Portal.

To use one, write its name between colons in any form or stall wording, e.g. `"heading": ":mm_stickers: Stickers"`. If an icon isn't uploaded yet, it's left out and the post still works. Forum tags can't use app emojis, so tags keep normal emoji. To add an icon, drop a square PNG (128×128, under 256 KB, name starting with `mm_`) into `assets/icons/`, deploy, and run `/marketmule icons` again.

## Blocked words

Discord's AutoMod doesn't check messages the bot posts through webhooks, so the bot checks form answers and rating notes itself. Add words or phrases to `config/blocked_words.txt`, one per line. Capital letters don't matter and only whole words match. If an answer contains one, the member is told which word and can edit their answers without retyping everything. Changes to the list apply without a restart.

## Running it

**Discord application:** create one at <https://discord.com/developers/applications>, copy the token from the **Bot** tab, and invite it with the `bot` and `applications.commands` scopes and permissions integer `326954503248`. No privileged intents are needed.

**On Windows:** run `run.bat`. The first run creates `.env`. Fill in `DISCORD_TOKEN` and `GUILD_ID`, then run it again. Only run one copy per token.

**On the server (Ubuntu), one time:**

```bash
apt install -y git python3-venv supervisor
cd /home && git clone git@github.com:KaonashiGH/MarketMule.git marketmule && cd marketmule
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env && chmod 600 .env
cp deploy/marketmule.conf /etc/supervisor/conf.d/ && supervisorctl reread && supervisorctl update
```

**Deploying changes:** push to GitHub, then on the server run `bash /home/marketmule/deploy/update.sh`. Logs are in `/var/log/marketmule.err.log`.

## Adding a feature

1. Create `marketmule/features/<name>/` with an `__init__.py` that defines `async def setup(bot)`.
2. Keep its tables in its own `storage.py` as a `MIGRATIONS` tuple, and call `bot.db.migrate("<name>", MIGRATIONS)` from `setup`.
3. Add `"marketmule.features.<name>"` to `FEATURES` in `core/bot.py`.

**Database changes:** never edit a migration that has been deployed. Add a new one at the end instead. The bot applies new migrations automatically on startup.

## Development

```bash
pip install -r requirements-dev.txt
pytest          # tests
ruff check .    # lint
ruff format .   # format
```
