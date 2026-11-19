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
  when `OPENAI_API_KEY` is missing and the UI degrades gracefully. One model for
  the whole app: `ai.DEFAULT_MODEL`, overridable with `OPENAI_MODEL`. Endpoints
  still accept a `model`, but `_pick_model()` only honours ids in
  `ai.KNOWN_MODELS` — never pass a client string to the API directly.
- Two AI settings are load-bearing, don't drop them: `reasoning_effort=low` (GPT-5
  models default to medium and then spend minutes on a one-word edit) and the
  client `timeout`/`max_retries=0` (the SDK defaults to 600s with 2 retries, which
  the UI shows as a permanently stuck button). `_complete()` logs model, effort,
  duration and tokens for every call, and turns SDK errors into `AIFailed` so the
  blueprint answers JSON instead of an HTML traceback.
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
- `app/config.py` loads `.env` itself, because `Config` reads the environment at
  import time, before `create_app()` runs. `OPENAI_MODEL` defaults to empty, which
  means `ai.DEFAULT_MODEL`. `tests/smoke_test.py` blanks the OpenAI/admin variables
  so a local `.env` can't leak into it.
- The approved levels live in `data/approved_levels.xlsx` (one tab per level,
  column = depth) and are synced into the database as **locked** levels
  (`Level.locked`): published, read-only. The API refuses to update, delete or
  import-overwrite them (403 / import error); Duplicate makes an editable copy.
  The UI greys them out (`applyLock()` in `app.js`), but the server is the guard.
  `flask --app run import-xlsx data/approved_levels.xlsx` is the only way to
  change them and runs before gunicorn on every deploy (`railpack.json`, `--quiet`):
  changed tabs are updated, unchanged ones skipped. An editable level on an
  approved number is kept and reported (`KEEP`) unless `--mode overwrite`; one
  with exactly the approved tree is adopted. `--dry-run` prints the per-tab problem
  report. Parsing is `services/xlsx_levels.py` (stdlib only) and reports sheet
  irregularities instead of fixing them silently. Tabs without a level number are
  matched by name. Run `tests/xlsx_import_test.py` after touching the parser or
  the command.
- `_add_missing_columns()` (`app/__init__.py`) adds purely additive columns that
  `create_all()` can't (it never alters existing tables). Anything beyond that
  needs Alembic.
- The house style comes from those levels, not from a fixed shape: uneven
  branches, mixed link types (kind of, part, symbol, scene, partner), concrete
  leaves under abstract themes, short phrases where natural (ICE CREAM), proper
  nouns allowed. `SYSTEM_PROMPT` and `STYLE_EXAMPLES` in `services/ai.py` carry
  it to every call — update the examples there when the style moves.
  `_clean_word()` keeps phrases (spaces, hyphens, apostrophes); don't go back to
  single-word clipping, it turned ICE CREAM into ICE.
- The AI's references are the approved levels read from `APPROVED_LEVELS_PATH`
  (default `data/approved_levels.xlsx`) by `ai.reference_levels()`, cached per
  file mtime — not the database, so a server with no imported levels still
  generates in the house style. To change the references, update the workbook.
- `generate_tree` and `regenerate_branch` send every reference level in the
  system message (`_reference_system()`, ~48k tokens for 190 levels, kept as a
  stable prefix so OpenAI's prompt cache applies; `cached=` in the log shows it).
  Words may repeat across levels, branches may not: `_copied_branches()` flags a
  branch whose children are 3/4 the same as an approved branch of the same word
  (2+ shared, so LOCK > KEY pairs are fine), retries once, then returns `copied`
  for the UI to warn about. Suggest and tree edit stay on the short `SYSTEM_PROMPT`.
- Level size comes from its number: `ai.size_for_level(n)` is the median word
  count and depth of the 9 approved levels nearest to `n`. The AI modal prefills
  from it and the batch uses it per level.
- Batch generation (`✨ Batch` in the Levels pane) runs one
  `POST /api/ai/generate-level {topic, number}` per topic, sequentially from the
  browser: each request stays inside gunicorn's 120s timeout and numbers can't
  race. Results are saved as drafts; `409` means the number is taken.
  `POST /api/ai/suggest-topics` proposes themes that aren't in the references or
  the database yet.
- `generate_tree` takes a target word count and a max depth, not a breadth; the
  schema only bounds depth and `_cap_words()` trims breadth-first at
  `ai.MAX_GENERATED_WORDS`. The JS mirrors that cap as `MAX_GENERATED_WORDS`.
