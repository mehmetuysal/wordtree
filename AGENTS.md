# Working on this repo

Flask level designer for the Word Tree game. Python 3.11, virtualenv in `venv/`.

## Commands

```bash
./venv/bin/python run.py                      # dev server on :5001
./venv/bin/python tests/smoke_test.py         # end-to-end API check (temp sqlite db)
FLASK_APP=run.py ./venv/bin/flask create-user NAME
```

Always run `tests/smoke_test.py` after touching `app/blueprints/levels.py` or
`app/models.py` — it covers CRUD, validation, batch export/import modes and the
role checks.

## Conventions

- There are two editors for the same tree: the row list in the Structure pane
  (`buildRow`) and the node toolbar on the canvas (`nodeToolbar`). Both drive the
  same action helpers (`addChild`, `toggleHidden`, `deleteNode`, `moveNode`,
  `suggestChildren`, `regenerate`) — put new node actions there, never in one UI
  only. Stats and the warning bar live in the Preview header so they stay visible
  when the Structure pane is collapsed.
- `app/static/js/designer.js` is the ported prototype (tree editing + preview).
  Keep changes there minimal and behaviour-compatible; it talks to the shell
  only through `window.Designer`. All server/library logic lives in `app.js`.
- `app/static/css/designer.css` is the prototype stylesheet. New chrome uses
  Tailwind utility classes (play CDN, configured in `templates/base.html`);
  the designer panels keep their original CSS classes.
- Every OpenAI call goes through `app/services/ai.py`. It raises `AIUnavailable`
  when `OPENAI_API_KEY` is missing and the UI degrades gracefully.
- Trees are validated and uppercased server-side in `normalize_tree()`
  (`app/blueprints/levels.py`) — do not trust the client shape.
- No migrations yet: `db.create_all()` runs at startup. Add Alembic before the
  first breaking schema change, definitely before the Supabase move.
- Word uniqueness is a product rule, not a suggestion: a hidden word the player
  must place has to be unambiguous, so every word appears once per level and must
  fit exactly one parent. Prompts state it, `_blank_duplicates()` in
  `services/ai.py` enforces the exact-duplicate half (retry once, then leave the
  slot empty), and the editor highlights manual duplicates.
- Deployment start command lives in `railpack.json`, not in the Railway UI.
- `_bootstrap_admin()` (`app/__init__.py`) creates the first admin from
  `ADMIN_USERNAME`/`ADMIN_PASSWORD` and only when the users table is empty. Keep
  that guard — it's the difference between a bootstrap and a backdoor.
