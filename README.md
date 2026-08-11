# Word Tree — Level Designer

Flask + Tailwind level editor for the Word Tree puzzle game. The tree editor and
preview are the original standalone designer, wrapped in a level library with
user management, persistence and batch import/export.

## Run

```bash
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env            # set SECRET_KEY, optionally OPENAI_API_KEY

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

## Keyboard

- `⌘S` / `Ctrl+S` — save
- `Alt+↑` / `Alt+↓` — previous / next level
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

All routes require a session; `/admin/users` requires the admin role. Non-GET
requests need the `X-CSRFToken` header (the front end reads it from the
`csrf-token` meta tag).

## Moving to Supabase later

Nothing outside `app/models.py` touches the database directly, and the
connection comes from `DATABASE_URL`. Point it at the Supabase Postgres URL,
`pip install psycopg[binary]`, and add Alembic before the first schema change.

## Tests

```bash
./venv/bin/python tests/smoke_test.py
```
