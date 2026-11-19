"""OpenAI-backed helpers for the level designer.

Everything the app needs from OpenAI goes through this module so the rest of
the code never touches the SDK directly. Without OPENAI_API_KEY configured the
functions raise AIUnavailable and the UI degrades gracefully.
"""

import json
import re
import time

from flask import current_app

# Trimmed from the approved levels in data/approved_levels.xlsx. They show the
# house style better than any rule: uneven branches, mixed link types, concrete
# leaves under abstract themes, and short phrases where that's the natural name.
STYLE_EXAMPLES = """\
STORES
  PHARMACY
    MEDICINE
    BANDAGE
    VITAMIN
  FURNITURE STORE
    SOFA
    CLOSET
  BOOKSTORE
    MAGAZINE
    NOVEL
    COMIC

LUCK
  GOOD LUCK
    FOUR-LEAF CLOVER
    HORSESHOE
    LUCKY NUMBER
      SEVEN
  BAD LUCK
    BLACK CAT
    BROKEN MIRROR
    FRIDAY THE 13TH
  CHANCE GAMES
    LOTTERY
      TICKET
      DRAW
    DICE
      ROLL
        SNAKE EYES
    COIN FLIP
      HEADS
      TAILS

EMOTIONS
  SADNESS
    CRYING
      TEARS
        TISSUE
    FUNERAL
      CASKET
      HEARSE
  FEAR
    NIGHTMARE
    SPIDER
    HAUNTED HOUSE
  SURPRISE
    RAISED EYEBROWS
    PLOT TWIST

PAIRS
  OPPOSITES
    HOT
      COLD
    DAY
      NIGHT
  MATCHING OBJECTS
    LOCK
      KEY
    NEEDLE
      THREAD
  PEOPLE
    BRIDE
      GROOM
    DOCTOR
      PATIENT

FLOW
  WATER
    RIVERS
      AMAZON
      NILE
  TRAFFIC
    CONGESTION
      GRIDLOCK
    TUNNEL
  CASH
    INCOME
      SALARY
    BILLS"""

SYSTEM_PROMPT = (
    "You design levels for a word association puzzle game called Word Tree. "
    "A level is a tree. The root is the level's theme; its children are the main "
    "branches of that theme, and every word below them belongs to its own parent. "
    "A child can be a kind of its parent (FRUIT > APPLE), an example or a part "
    "(BOOKSTORE > NOVEL, DICE > ROLL), the thing it's famous for or a symbol of it "
    "(BAD LUCK > BLACK CAT), what you see at that scene (FUNERAL > HEARSE), or its "
    "natural partner (LOCK > KEY, HOT > COLD). Abstract themes and branches always "
    "come down to concrete, picturable words at the leaves. "
    "Branches are uneven on purpose: some words have one child, some five or six, "
    "and many have none — leaves sit at different depths. "
    "Words are UPPERCASE English. Prefer a single word, but use a short phrase of "
    "two or three words when that is the thing's natural name (ICE CREAM, "
    "FOUR-LEAF CLOVER, HAUNTED HOUSE); never glue words together. A child must not "
    "repeat its parent's word (not RUNWAY > RUNWAY LIGHTS) — that gives the answer "
    "away. Proper nouns — "
    "places, famous people, characters, well-known brands — are fine when the "
    "theme calls for them. Everything must be something a casual adult player "
    "recognises instantly: no jargon, abbreviations or codes.\n\n"
    "Approved levels, for style:\n" + STYLE_EXAMPLES
)

# The player has to place hidden words back into the tree, so a word that would
# also fit under a different parent makes the puzzle unsolvable. Every prompt
# repeats this; _blank_duplicates() then enforces the exact-duplicate half of it.
UNIQUE_RULE = (
    "Uniqueness is critical: the player has to place each word back into the "
    "tree, so a word may appear only once in the whole level, and each word "
    "must belong to exactly one parent. Never pick a word that would also be a "
    "sensible child of another word in this level — if a word fits two parents "
    "or two branches, choose a different, more specific word."
)

# Reusing a word from the approved levels is fine; reproducing one of their
# branches is not. generate_tree and regenerate_branch send every published
# level along and _copied_branches() checks the answer against them.
COPY_RULE = (
    "Words from the approved levels may be reused, but never reproduce one of their "
    "branches: when a word appears there with children, give it a clearly different "
    "set of children here (COFFEE can come back, COFFEE > ESPRESSO, LATTE, MOCHA cannot)."
)
# ~35k tokens for the first 190 levels. Past this budget only the newest-numbered
# levels are dropped from the prompt; the copy check still sees all of them.
REFERENCE_CHAR_BUDGET = 400_000

MAX_DEPTH = 6
# Past this the tree doesn't fit on screen and the model can't keep that many
# words unambiguous. Approved levels run 15 words early on, 50-60 later.
MAX_GENERATED_WORDS = 80

# One model for everything, no picker. Luna is the cheap tier of the current
# generation: $0.20 / $1.20 per 1M tokens, roughly $0.0006 per tree edit, and it
# answered in ~3s on every test run. gpt-4o-mini is about $0.0002 cheaper per
# edit but handed back 6 words of a 19-word tree once, which is not a trade
# worth making. Override with OPENAI_MODEL if you disagree.
DEFAULT_MODEL = "gpt-5.6-luna"

# Anything a request may ask for. GPT-5.6: sol is the flagship but times out on
# this workload far more often than it answers; terra is the balanced tier.
KNOWN_MODELS = (
    "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
    "gpt-5.5", "gpt-5.4", "gpt-4o-mini",
)


def active_model():
    configured = (current_app.config.get("OPENAI_MODEL") or "").strip()
    return configured or DEFAULT_MODEL


def _pick_model(requested):
    """Callers may name a model, but only one we know about."""
    if requested and requested in KNOWN_MODELS:
        return requested
    return active_model()


def _tree_schema(depth):
    """A strict json_schema for a tree at most `depth` levels deep."""
    schema = {
        "type": "object",
        "properties": {"word": {"type": "string"}},
        "required": ["word"],
        "additionalProperties": False,
    }
    if depth > 1:
        schema["properties"]["children"] = {"type": "array", "items": _tree_schema(depth - 1)}
        schema["required"].append("children")
    return schema


def _clean_word(word):
    """An uppercase word or short phrase: LIGHT_TRUCK -> LIGHT TRUCK, stray marks dropped."""
    word = re.sub(r"[_\s]+", " ", word or "")
    word = re.sub(r"[^A-Za-z0-9 '&\-]", "", word)
    return re.sub(r"\s+", " ", word).strip(" -'").upper()


def _blank_duplicates(node, taken):
    """Empty every word already in `taken` (case-insensitive) and collect them.

    `taken` is updated with the words that survive, so a word repeated twice
    inside the answer itself is caught too. Returns the rejected words.
    """
    rejected = []
    word = node.get("word") or ""
    if word:
        if word.upper() in taken:
            node["word"] = ""
            rejected.append(word.upper())
        else:
            taken.add(word.upper())
    for child in node.get("children") or []:
        rejected += _blank_duplicates(child, taken)
    return rejected


def _published_levels():
    from ..extensions import db
    from ..models import Level

    rows = db.session.execute(
        db.select(Level).filter_by(status="published").order_by(Level.number)
    ).scalars()
    return [(lv.number, lv.name, lv.tree) for lv in rows if (lv.tree or {}).get("word")]


def _outline_words(node, depth=0, out=None):
    out = [] if out is None else out
    out.append("  " * depth + (node.get("word") or "").strip().upper())
    for child in node.get("children") or []:
        _outline_words(child, depth + 1, out)
    return out


def _reference_system(levels):
    """SYSTEM_PROMPT plus every approved level. The level text only changes when
    a level is published or edited, so OpenAI's prompt cache keeps it cheap."""
    if not levels:
        return SYSTEM_PROMPT
    blocks, used = [], 0
    for number, name, tree in levels:
        block = f"# Level {number}\n" + "\n".join(_outline_words(tree))
        used += len(block)
        if used > REFERENCE_CHAR_BUDGET:
            current_app.logger.warning("ai references cut at level %s (char budget)", number)
            break
        blocks.append(block)
    return (
        SYSTEM_PROMPT + "\n\nThese are all the approved levels of the game. Match their "
        "style, size and difficulty — the further a level is in the game, the bigger and "
        "harder it is. " + COPY_RULE + "\n\n" + "\n\n".join(blocks)
    )


def _branch_index(levels):
    """word -> [(level number, its children's words)] for every approved branch."""
    index = {}
    for number, _, tree in levels:
        stack = [tree]
        while stack:
            node = stack.pop()
            kids = node.get("children") or []
            words = frozenset((k.get("word") or "").strip().upper() for k in kids) - {""}
            if len(words) >= 2:
                index.setdefault((node.get("word") or "").strip().upper(), []).append((number, words))
            stack += kids
    return index


def _copied_branches(node, index):
    """Branches whose children mostly repeat an approved branch of the same word.

    Two shared children are the floor (LOCK > KEY is a pair, not a copy), and
    three quarters of the smaller set have to match.
    """
    found = []
    word = (node.get("word") or "").strip().upper()
    kids = frozenset((k.get("word") or "").strip().upper() for k in node.get("children") or []) - {""}
    for number, ref in index.get(word, ()) if len(kids) >= 2 else ():
        shared = kids & ref
        if len(shared) >= 2 and len(shared) >= 0.75 * min(len(kids), len(ref)):
            found.append(f"{word} > {', '.join(sorted(shared))} (level {number})")
            break
    for child in node.get("children") or []:
        found += _copied_branches(child, index)
    return found


class AIUnavailable(RuntimeError):
    """No API key configured."""


class AIFailed(RuntimeError):
    """The model call itself failed — timeout, rate limit, refusal…"""


def is_configured():
    return bool(current_app.config.get("OPENAI_API_KEY"))


def _client():
    if not is_configured():
        raise AIUnavailable("OPENAI_API_KEY is not configured on the server.")
    from openai import OpenAI

    # the SDK defaults to a 10 minute timeout and 2 retries, which shows up in
    # the UI as a button stuck on "Applying…" forever
    # no retries either: retrying a timeout just doubles the time the user
    # stares at a spinner before finding out it failed
    return OpenAI(
        api_key=current_app.config["OPENAI_API_KEY"],
        timeout=current_app.config.get("OPENAI_TIMEOUT", 45),
        max_retries=0,
    )


def _complete(user_prompt, schema, schema_name, *, model=None, system=SYSTEM_PROMPT):
    import openai

    client = _client()
    model_id = _pick_model(model)
    kwargs = {}
    # GPT-5 models reason before answering, and on the flagship the default
    # effort turns a two-second edit into a multi-minute one. These are small
    # structured tasks, so keep the thinking short.
    if model_id.startswith("gpt-5"):
        kwargs["reasoning_effort"] = current_app.config.get("OPENAI_REASONING_EFFORT", "low")

    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
            **kwargs,
        )
    except openai.APITimeoutError:
        took = time.perf_counter() - started
        current_app.logger.warning("ai %s model=%s TIMEOUT after %.1fs", schema_name, model_id, took)
        raise AIFailed(f"{model_id} did not answer within {took:.0f}s. Try a faster model.")
    except openai.APIError as err:
        took = time.perf_counter() - started
        current_app.logger.warning("ai %s model=%s FAILED %.1fs %s", schema_name, model_id, took, err)
        if {getattr(err, "code", ""), getattr(err, "type", "")} & {
            "insufficient_quota", "credit_balance_exhausted"
        }:
            raise AIFailed("The OpenAI account is out of credits — top it up to use AI.")
        raise AIFailed(f"{model_id}: {getattr(err, 'message', None) or err}")

    usage = getattr(resp, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    current_app.logger.info(
        "ai %s model=%s effort=%s %.1fs tokens=%s/%s cached=%s",
        schema_name, model_id, kwargs.get("reasoning_effort", "-"),
        time.perf_counter() - started,
        getattr(usage, "completion_tokens", "?"), getattr(usage, "total_tokens", "?"),
        getattr(details, "cached_tokens", "?"),
    )
    return json.loads(resp.choices[0].message.content)


def _decorate(node, depth=0, hide_from_depth=1):
    """Turn a bare {word, children} tree into the designer's node format."""
    return {
        "word": _clean_word(node.get("word")),
        "hidden": depth >= hide_from_depth,
        "children": [
            _decorate(c, depth + 1, hide_from_depth) for c in node.get("children") or []
        ],
    }


def _cap_words(node, limit):
    """Keep the first `limit` words in breadth-first order, so a runaway answer
    loses leaves on the deepest level rather than whole branches."""
    kept, queue = 1, [node]
    while queue:
        current = queue.pop(0)
        kids = []
        for child in current.get("children") or []:
            if kept >= limit:
                break
            kids.append(child)
            kept += 1
        current["children"] = kids
        queue += kids
    return node


def generate_tree(topic, *, words=40, depth=4, hide_from_depth=1, model=None):
    """Generate a level tree for a topic, shaped like the approved levels."""
    words = max(5, min(MAX_GENERATED_WORDS, words))
    branches = "2 or 3" if words < 20 else "4 to 7"
    prompt = (
        f"Build a word tree for the topic '{topic}'. The root word is the topic itself. "
        f"Aim for about {words} words in total, counting the root, and never more than "
        f"{MAX_GENERATED_WORDS}. The tree is at most {depth} levels deep (the root is level 1). "
        f"Give the root {branches} branches that each look at the theme from a different "
        "angle. Keep the branches uneven, the way the approved levels are: one word gets "
        "five children, another one or none, and only some branches reach the deepest "
        "level. A word without children has an empty children list. "
        "Each child must belong to its own parent and must not fit its parent's siblings "
        "or any word higher up the tree. " + UNIQUE_RULE + " " + COPY_RULE
    )
    levels = _published_levels()
    system, index = _reference_system(levels), _branch_index(levels)

    def attempt(extra_banned=(), copied=()):
        text = prompt
        if extra_banned:
            text += (
                " Your previous answer reused these words; do not use any of them: "
                + ", ".join(sorted(extra_banned)) + "."
            )
        if copied:
            text += " Your previous answer copied these approved branches; change them: " + "; ".join(copied) + "."
        answer = _complete(text, _tree_schema(depth), "word_tree", model=model, system=system)
        raw = _decorate(_cap_words(answer, MAX_GENERATED_WORDS), hide_from_depth=hide_from_depth)
        return raw, _blank_duplicates(raw, set()), _copied_branches(raw, index)

    tree, rejected, copied = attempt()
    if rejected or copied:
        retry = attempt(set(rejected), copied)
        if len(retry[1]) + len(retry[2]) < len(rejected) + len(copied):
            tree, rejected, copied = retry
    return tree, copied


def _shape_depth(shape):
    kids = shape.get("children") or []
    return 1 + max((_shape_depth(k) for k in kids), default=0)


def _outline(shape, label="?", depth=0, out=None):
    """The requested shape as an indented list of slots the model must fill."""
    out = [] if out is None else out
    out.append("  " * depth + "- " + label)
    for k in shape.get("children") or []:
        _outline(k, "?", depth + 1, out)
    return "\n".join(out) if depth == 0 else out


def _fit_shape(raw, shape):
    """Force the model's answer onto the requested shape."""
    node = {"word": _clean_word(raw.get("word"))}
    kids = shape.get("children") or []
    if kids:
        raw_kids = raw.get("children") or []
        node["children"] = [
            _fit_shape(raw_kids[i] if i < len(raw_kids) else {}, k)
            for i, k in enumerate(kids)
        ]
    return node


def regenerate_branch(word, shape, *, path=None, avoid=None, keep_word=False, topic="", model=None):
    """Re-generate a node and its whole subtree, keeping the existing shape.

    `word` may be empty — an unnamed node the editor wants filled in. `shape` is
    the subtree stripped down to nesting only: {"children": [...]}. The result
    has exactly the same nesting, so the caller can map the new words onto the
    existing nodes in place and keep ids/flags/offsets.
    """
    word = (word or "").strip()
    keep_word = keep_word and bool(word)
    depth = min(MAX_DEPTH, _shape_depth(shape))
    banned = {w.upper() for w in (avoid or []) if w}
    if keep_word:
        banned.discard(word.upper())

    parents = [p for p in (path or [])[:-1] if p and p != "?"]
    subtree = " and its subtree" if shape.get("children") else ""

    lines = []
    if keep_word:
        lines.append(f"Keep the root word '{word}' exactly as it is and rebuild everything below it.")
    elif word:
        lines.append(
            f"Replace the word '{word}' with a different, better-fitting word, "
            "then rebuild its entire subtree around the new word."
        )
    elif parents:
        lines.append(
            f"This slot has no word yet. Choose the word that belongs here as a "
            f"child of '{parents[-1]}'{subtree}."
        )
    elif topic:
        lines.append(f"This slot has no word yet. Build it{subtree} for a level about '{topic}'.")
    else:
        lines.append(f"This slot has no word yet. Choose a broad category word for it{subtree}.")
    if path:
        lines.append("Its position in the tree, from the root down: " + " > ".join(path) + ".")
    lines.append(
        "The answer must match this outline exactly — one word per slot, "
        "no slot left empty, no extra nodes:\n" + _outline(shape, word if keep_word else "?") + "\n"
    )
    lines.append("Every child must belong to its own parent, in the ways the approved levels show.")
    lines.append(UNIQUE_RULE)
    lines.append(COPY_RULE)
    levels = _published_levels()
    system, index = _reference_system(levels), _branch_index(levels)

    def attempt(extra_banned=(), copied=()):
        prompt = list(lines)
        blocked = sorted(banned | set(extra_banned))
        if blocked:
            prompt.append(
                "These words are already used elsewhere in the level — none of them "
                "may appear in your answer: " + ", ".join(blocked) + "."
            )
        if copied:
            prompt.append("Your previous answer copied these approved branches; change them: "
                          + "; ".join(copied) + ".")
        node = _fit_shape(_complete(" ".join(prompt), _tree_schema(depth), "word_branch",
                                    model=model, system=system), shape)
        taken = set(banned)
        if not keep_word:
            return node, _blank_duplicates(node, taken), _copied_branches(node, index)
        node["word"] = word.upper()                  # the root word is off limits
        taken.add(node["word"])
        rejected = []
        for child in node.get("children") or []:
            rejected += _blank_duplicates(child, taken)
        return node, rejected, _copied_branches(node, index)

    # one retry with the clashes spelled out, then leave slots empty
    out, rejected, copied = attempt()
    if rejected or copied:
        retry = attempt(rejected, copied)
        if len(retry[1]) + len(retry[2]) < len(rejected) + len(copied):
            out, rejected, copied = retry
    return out, copied


EDIT_SYSTEM = (
    SYSTEM_PROMPT + " You edit an existing level tree on command. You never chat, "
    "explain or ask questions — you only return the updated tree. Apply exactly "
    "what the instruction asks for and leave everything else untouched: keep every "
    "other word spelled as it is, keep the existing hidden flags, and keep the "
    "existing order of children. Any word you add is hidden (true) unless the "
    "instruction says otherwise, so the player has to place it. " + UNIQUE_RULE
)


def _edit_schema(depth):
    """Tree schema including the hidden flag, so an edit can move words in and out
    of the word bank as well as rename or restructure them."""
    schema = {
        "type": "object",
        "properties": {"word": {"type": "string"}, "hidden": {"type": "boolean"}},
        "required": ["word", "hidden"],
        "additionalProperties": False,
    }
    if depth > 1:
        schema["properties"]["children"] = {"type": "array", "items": _edit_schema(depth - 1)}
        schema["required"].append("children")
    return schema


def _strip_for_prompt(node, depth=0):
    """The tree as the model should see it: words, flags, nesting. No ids or offsets."""
    out = {"word": (node.get("word") or "").strip().upper(), "hidden": bool(node.get("hidden"))}
    kids = [_strip_for_prompt(c, depth + 1) for c in node.get("children") or []]
    if kids:
        out["children"] = kids
    return out


def _clean_edited(node, depth=0):
    out = {
        "word": _clean_word(node.get("word")),
        "hidden": bool(node.get("hidden")),
        "children": [_clean_edited(c, depth + 1) for c in node.get("children") or []],
    }
    return out


def _find_duplicates(node, seen=None, dupes=None):
    seen = set() if seen is None else seen
    dupes = [] if dupes is None else dupes
    word = (node.get("word") or "").strip().upper()
    if word:
        if word in seen and word not in dupes:
            dupes.append(word)
        seen.add(word)
    for child in node.get("children") or []:
        _find_duplicates(child, seen, dupes)
    return dupes


def edit_tree(instruction, tree, *, model=None, topic=""):
    """Apply a plain-language instruction to a whole tree and return the new one.

    Nothing is auto-blanked here: this is the user's own tree, so a clash is
    reported back instead of quietly emptying a word they may have typed.
    """
    current = _strip_for_prompt(tree or {})
    prompt = [
        "Here is the current tree as JSON:", json.dumps(current, ensure_ascii=False),
    ]
    if topic:
        prompt.append(f"The level is called '{topic}'.")
    prompt += [
        "Apply this instruction to it:", instruction.strip(),
        "Return the complete updated tree — every node, not just the part you changed.",
        f"The tree must be at most {MAX_DEPTH} levels deep.",
    ]
    raw = _complete(
        "\n".join(prompt), _edit_schema(MAX_DEPTH), "tree_edit",
        model=model, system=EDIT_SYSTEM,
    )
    out = _clean_edited(raw)
    return out, _find_duplicates(out)


def suggest_children(word, *, path=None, count=4, avoid=None, model=None):
    """Suggest child words for a single node."""
    schema = {
        "type": "object",
        "properties": {"words": {"type": "array", "items": {"type": "string"}}},
        "required": ["words"],
        "additionalProperties": False,
    }
    banned = {w.upper() for w in (avoid or []) if w}
    lines = [f"Suggest {count} child words for the word '{word}'."]
    if path:
        lines.append("Its position in the tree, from the root down: " + " > ".join(path) + ".")
    lines.append(UNIQUE_RULE)

    def attempt(n, blocked):
        prompt = [f"Suggest {n} child words for the word '{word}'."] + lines[1:]
        if blocked:
            prompt.append(
                "None of these already-used words may be suggested: "
                + ", ".join(sorted(blocked)) + "."
            )
        result = _complete(" ".join(prompt), schema, "child_words", model=model)
        out = []
        for raw in result.get("words", []):
            w = _clean_word(raw)
            if w and w not in blocked and w not in out:
                out.append(w)
        return out

    words = attempt(count, banned)
    if len(words) < count:   # duplicates were dropped — ask once more for the rest
        words += attempt(count - len(words), banned | set(words))[: count - len(words)]
    return words[:count]
