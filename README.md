# Word Tree — Level Designer

Flask + Tailwind level editor for the Word Tree puzzle game. The tree editor and
preview are the original standalone designer, wrapped in a level library with
user management, persistence and batch import/export.

## Run

```bash
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
printf 'SECRET_KEY=dev\nOPENAI_API_KEY=\nOPENAI_MODEL=gpt-4o-mini\n' > .env

FLASK_APP=run.py ./venv/bin/flask create-user admin   # first user is always admin
FLASK_APP=run.py ./venv/bin/flask seed-demo           # optional sample level
./venv/bin/python run.py                              # http://127.0.0.1:5001
```

## CLI

| Command | What it does |
| --- | --- |
| `flask init-db` | create tables (also runs automatically on startup) |
| `flask create-user NAME` | create a user; the very first one becomes admin |
| `flask reset-password NAME` | set a new password |
| `flask seed-demo` | insert the VEHICLE sample level |

## Layout

```
app/
  blueprints/   auth, editor (page), levels (REST API), admin (users), ai
  services/ai.py   the only place that talks to OpenAI
  static/css/designer.css   the prototype's stylesheet + app-shell additions
  static/js/designer.js     tree editing + preview, exposes window.Designer
  static/js/app.js          sidebar, saving, batch I/O, AI wiring
  templates/
```

## Editing the tree

Two ways to edit the same tree: the **Structure** row list, or the tree itself.
Every action exists in both, so collapse whichever you don't need (`Alt+2`).

- **Click** a word to select it; a toolbar appears above it with hidden/shown,
  rename, add child, ✨ AI children, ↻ regenerate, reorder and delete.
- **Double-click** a word to rename it in place (`Enter` saves, `Esc` cancels).
- **Drag** a word to move it with its whole branch, **Alt+double-click** to snap
  it back. `↻ Tree` in the header rebuilds every word under the root.

## Keyboard

- `⌘S` / `Ctrl+S` — save
- `Alt+↑` / `Alt+↓` — previous / next level
- `Enter` — rename the selected word, `Esc` — deselect
- `Alt+1` / `Alt+2` — collapse / expand the level list / the structure panel
- `/` — focus the level search
- `Esc` — close any modal

## Level JSON

A single level, and the batch format:

```json
{
  "format": "wordtree.levels.v1",
  "levels": [
    {
      "level": 1, "name": "Vehicles", "moves": 30, "coins": 100,
      "status": "draft", "hiddenCount": 9,
      "tree": {
        "word": "VEHICLE", "hidden": true,
        "children": [{ "word": "LAND", "hidden": false, "offset": { "x": 8, "y": 0 } }]
      }
    }
  ]
}
```

Import accepts a bare level object, a list, or the wrapper above. On a level
number clash you pick `skip`, `overwrite` or `renumber` (import as new levels).

## API

| Method | Route | |
| --- | --- | --- |
| GET | `/api/levels` | summaries for the sidebar |
| POST | `/api/levels` | create |
| GET/PUT/DELETE | `/api/levels/<id>` | read / update / delete |
| POST | `/api/levels/duplicate/<id>` | copy as a new level |
| GET | `/api/levels/export?ids=1,2` | batch export (all levels if `ids` omitted) |
| POST | `/api/levels/import?mode=skip` | batch import |
| GET | `/api/ai/status` | whether an OpenAI key is configured |
| POST | `/api/ai/generate-tree` | `{topic, breadth, depth, hideFromDepth}` |
| POST | `/api/ai/suggest-children` | `{word, path, avoid, count}` |
| POST | `/api/ai/regenerate-branch` | `{word, shape, path, avoid, keepWord}` — rebuild a node and its subtree |

All routes require a session; `/admin/users` requires the admin role. Non-GET
requests need the `X-CSRFToken` header (the front end reads it from the
`csrf-token` meta tag).

## Deploying (Railway)

`railpack.json` sets the start command — Railway's builder can't autodetect it
because the entrypoint is `run.py`, not `app.py`:

```
gunicorn run:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

The 120s timeout matters: AI calls regularly take longer than gunicorn's 30s
default, which would kill the worker mid-request.

Set these variables on the service:

| Variable | |
| --- | --- |
| `SECRET_KEY` | required — the fallback is a known dev value, so sessions are forgeable without it |
| `DATABASE_URL` | Postgres URL. Not needed if you attach a **volume** — see below. |
| `ADMIN_USERNAME` + `ADMIN_PASSWORD` | creates the first admin on boot, see below |
| `OPENAI_API_KEY` | optional; the AI buttons degrade gracefully without it |
| `OPENAI_MODEL` | optional, defaults to `gpt-4o-mini` |

### Where the data lives

The container filesystem is rebuilt on every deploy, so a SQLite file that
isn't on a volume loses every level and user each time you push. Two ways out:

- **Attach a volume.** No config needed, whatever mount path you pick: with no
  `DATABASE_URL` set, the app puts `wordtree.db` inside
  `RAILWAY_VOLUME_MOUNT_PATH`. SQLite runs in WAL mode with a 15s busy timeout
  so the two gunicorn workers don't trip over each other.
- **Attach Postgres** and set `DATABASE_URL` — it always wins over the volume.

Startup logs which one you got: `Database: sqlite at … (on the volume,
persists)`, `Database: postgresql://***@…`, or a loud warning that the file
will be wiped. If you ever see permission errors writing to the volume, set
`RAILWAY_RUN_UID=0` on the service.

### The first admin

Tables are created on startup but users are not, and an empty users table just
renders a login nobody can pass — with no error. So set `ADMIN_USERNAME` and
`ADMIN_PASSWORD` and redeploy: `_bootstrap_admin()` creates that admin **only
when the users table is completely empty**, logs a line saying so, and never
touches an existing account. Delete both variables afterwards.

Without them the app logs `No users exist yet, so nobody can sign in.` on every
boot, which is the answer to "why can't I log in".

The alternative is a shell inside the running container:

```bash
railway ssh                       # then, in the container:
FLASK_APP=run.py flask create-user admin
```

`railway run` does **not** work for this — it runs the command on your own
machine with the service's variables injected, so it would write to a local
database (and Railway's internal `DATABASE_URL` isn't reachable from there
anyway).

## Moving to Supabase later

Nothing outside `app/models.py` touches the database directly, and the
connection comes from `DATABASE_URL`. Point it at the Supabase Postgres URL,
`pip install psycopg[binary]`, and add Alembic before the first schema change.

## Tests

```bash
./venv/bin/python tests/smoke_test.py
```
