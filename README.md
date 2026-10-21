# MarketMule

A Discord bot built from independent features. It currently has one feature:

- **Forums**: members fill in a form, and the bot publishes a standardized forum post under their name and avatar. Used for bug reports and feature requests. Staff manage each post's status and tags through a hidden menu.

## Project layout

```
marketmule/
  __main__.py          entry point: python -m marketmule
  core/                shared by every feature
    bot.py             bot class, feature list, command sync
    config.py          .env loading
    database.py        shared SQLite connection and per-feature migrations
    staff.py           who counts as staff
    admin.py           /marketmule commands
  features/
    forums/            the forums feature (see its __init__.py for a file guide)
templates/
  forums/              form definitions for the forums feature
tests/                 automated tests (pytest)
deploy/                server files: supervisor config and update script
run.bat                start the bot on Windows for local testing
```

**Rules that keep it maintainable**

- `core/` never imports from `features/`.
- Features never import from each other. Shared code goes in `core/`.
- Each feature owns its own tables, prefixed with its name (for example `forum_posts`).

## Adding a feature

1. Create `marketmule/features/<name>/` with an `__init__.py` that defines `async def setup(bot)`.
2. Put its tables in `storage.py` as a `MIGRATIONS` tuple and call `bot.db.migrate("<name>", MIGRATIONS)` from `setup`.
3. Put any templates in `templates/<name>/`.
4. Add `"marketmule.features.<name>"` to `FEATURES` in `core/bot.py`.

`features/forums/` is the reference example.

## Database changes

Each feature lists its schema as numbered migrations. On startup, the bot runs only the migrations a database hasn't had yet, so existing data, including the live server's, is upgraded automatically.

- **Never edit or reorder a migration** once it has been deployed.
- To change a table, **append** a new migration, for example `ALTER TABLE forum_posts ADD COLUMN priority TEXT;`.

## Discord application

1. Create an application at <https://discord.com/developers/applications>.
2. On the **Bot** tab, reset and copy the token. No privileged intents are needed.
3. Under **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes, then these bot permissions:
   - View Channels
   - Manage Channels
   - Manage Webhooks
   - Manage Threads
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Embed Links
   - Attach Files
   - Read Message History
   - Add Reactions

   The permissions integer is `326954503248`.
4. Open the generated URL to add the bot to your server.

## Running locally (Windows)

Run `run.bat`. The first run creates `.venv` and `.env`. Fill in `DISCORD_TOKEN` and `GUILD_ID` in `.env`, then run it again.

Only run one copy of the bot per token. If the bot is running on the server, use a separate test bot locally.

## Running on the server (Ubuntu)

One-time setup, as root:

```bash
apt update && apt install -y git python3-venv supervisor
cd /home
git clone git@github.com:OWNER/REPO.git marketmule
cd marketmule
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
cp deploy/marketmule.conf /etc/supervisor/conf.d/
supervisorctl reread && supervisorctl update
```

Replace `OWNER/REPO` with the GitHub repository.

To deploy new changes after they're pushed to GitHub:

```bash
bash /home/marketmule/deploy/update.sh
```

Logs are in `/var/log/marketmule.out.log` and `/var/log/marketmule.err.log`. Data is stored in `data/marketmule.db`.

## Commands

| Command | Purpose |
| --- | --- |
| `/marketmule staffrole role:<role>` | Sets the role that counts as staff in every feature. |
| `/marketmule status` | Shows the loaded features and the staff role. |
| `/forums setup channel:<forum> template:<form>` | Creates tags, a webhook and the pinned Start here post. |
| `/forums list` | Shows configured forums. |
| `/forums remove channel:<forum>` | Disconnects a forum. |
| `/manage` or **Apps → Manage post** | Staff: change a post's status or tags. |

## Forums: server setup

1. Create the forum channels, for example `#bug-reports` and `#feature-requests`.
2. In each channel's permissions, set these for **@everyone**:
   - **Create Posts**: off, so posts can only be made through the form.
   - **Send Messages in Posts**: on.
3. In each channel's permissions, explicitly allow the bot's role the permissions listed above. Without this, the @everyone override also blocks the bot.
4. Run `/forums setup` for each channel, then `/marketmule staffrole`.

### Managing posts

Inside a post, right-click the first message and choose **Apps → Manage post**, or run `/manage`. A form opens with the post's current status and tags.

- Changing the status notifies the reporter.
- Final statuses lock and archive the post.

These tools are visible by default only to members with Manage Threads. To show them to another role, open **Server Settings → Integrations → MarketMule** and add the role to `Manage post` and `/manage`.

### Templates

Each file in `templates/forums/` defines one form:

| Key | Contents |
| --- | --- |
| `pages` | One Discord modal per page, each with up to 5 items. |
| `sections` | Body of the published post. `{key}` inserts an answer. |
| `statuses`, `final_statuses` | Status tags, and which of them close a post. |
| `tag_emojis` | Emoji shown on each tag. |
| `reactions` | Emojis added to new posts. |

Field types:

- `text`: add `"style": "paragraph"` for multi-line answers.
- `select`: a dropdown. Add `"tag": true` to apply the answer as a forum tag.
- `file`: an upload box.
- `note`: text shown in the form.

After editing a template, restart the bot and run `/forums setup` again for that forum. Templates are checked against Discord's limits on startup, and the bot refuses to start if one is invalid.

## Development

```bash
pip install -r requirements-dev.txt
pytest          # run the tests
ruff check .    # lint
ruff format .   # format
```
