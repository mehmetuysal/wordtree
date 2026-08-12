"""OpenAI-backed helpers for the level designer.

Everything the app needs from OpenAI goes through this module so the rest of
the code never touches the SDK directly. Without OPENAI_API_KEY configured the
functions raise AIUnavailable and the UI degrades gracefully.
"""

import json
import re
import time

from flask import current_app

SYSTEM_PROMPT = (
    "You design levels for a word association puzzle game called Word Tree. "
    "A level is a tree: the root is a broad category and every child is a more "
    "specific member or part of its parent. Words are single English words in "
    "UPPERCASE, no spaces, no punctuation. Keep them common and unambiguous."
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

MAX_DEPTH = 6

# Models offered in the UI, newest generation first. Sol is the flagship, but
# measured on this workload it times out far more often than it answers, so the
# default is Luna: same generation, ~3s every time, and plenty for tree edits.
MODELS = [
    {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna", "note": "fast and reliable"},
    {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra", "note": "balanced, slower"},
    {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol", "note": "flagship, often times out"},
    {"id": "gpt-5.5", "label": "GPT-5.5", "note": "previous flagship"},
    {"id": "gpt-5.4", "label": "GPT-5.4", "note": ""},
    {"id": "gpt-4o-mini", "label": "GPT-4o mini", "note": "cheapest legacy"},
]
DEFAULT_MODEL = MODELS[0]["id"]


def models():
    """The picker's options — whatever OPENAI_MODEL is set to is always offered."""
    configured = current_app.config.get("OPENAI_MODEL", "")
    out = list(MODELS)
    if configured and not any(m["id"] == configured for m in out):
        out.append({"id": configured, "label": configured, "note": "from OPENAI_MODEL"})
    return out


def _pick_model(requested):
    """Trust only models we offer.

    The fallback is DEFAULT_MODEL rather than OPENAI_MODEL on purpose: a stale
    OPENAI_MODEL left in a .env silently downgrades every call, and a weak model
    asked to rewrite a whole tree will happily hand back half of it.
    """
    if requested and any(m["id"] == requested for m in models()):
        return requested
    return DEFAULT_MODEL


def _tree_schema(depth):
    """A strict json_schema for a tree exactly `depth` levels deep."""
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
    """One plain uppercase word — models occasionally answer LIGHT_TRUCK."""
    parts = re.findall(r"[A-Za-z]+", word or "")
    return parts[0].upper() if parts else ""


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
        raise AIFailed(f"{model_id}: {getattr(err, 'message', None) or err}")

    usage = getattr(resp, "usage", None)
    current_app.logger.info(
        "ai %s model=%s effort=%s %.1fs tokens=%s/%s",
        schema_name, model_id, kwargs.get("reasoning_effort", "-"),
        time.perf_counter() - started,
        getattr(usage, "completion_tokens", "?"), getattr(usage, "total_tokens", "?"),
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


def generate_tree(topic, *, breadth=3, depth=3, hide_from_depth=1, model=None):
    """Generate a full level tree for a topic."""
    prompt = (
        f"Build a word tree for the topic '{topic}'. "
        f"The root word is the topic itself. Give the root {breadth} children, "
        f"and give each of those {breadth} children of their own. "
        f"The tree must be exactly {depth} levels deep. "
        "Every slot must hold exactly one plain word — no underscores, hyphens or spaces. "
        + UNIQUE_RULE
    )

    def attempt(extra_banned):
        text = prompt
        if extra_banned:
            text += (
                " Your previous answer reused these words; do not use any of them: "
                + ", ".join(sorted(extra_banned)) + "."
            )
        raw = _decorate(_complete(text, _tree_schema(depth), "word_tree", model=model),
                        hide_from_depth=hide_from_depth)
        return raw, _blank_duplicates(raw, set())

    tree, rejected = attempt(set())
    if rejected:
        retry, retry_rejected = attempt(set(rejected))
        if len(retry_rejected) < len(rejected):
            tree = retry
    return tree


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
    lines.append("Every child must be a more specific member or part of its parent.")
    lines.append(UNIQUE_RULE)
    lines.append("Each slot holds exactly one plain word — no underscores, hyphens or spaces.")

    def attempt(extra_banned):
        prompt = list(lines)
        blocked = sorted(banned | extra_banned)
        if blocked:
            prompt.append(
                "These words are already used elsewhere in the level — none of them "
                "may appear in your answer: " + ", ".join(blocked) + "."
            )
        node = _fit_shape(
            _complete(" ".join(prompt), _tree_schema(depth), "word_branch", model=model), shape)
        taken = set(banned)
        if not keep_word:
            return node, _blank_duplicates(node, taken)
        node["word"] = word.upper()                  # the root word is off limits
        taken.add(node["word"])
        rejected = []
        for child in node.get("children") or []:
            rejected += _blank_duplicates(child, taken)
        return node, rejected

    out, rejected = attempt(set())
    if rejected:   # one retry with the clashes spelled out, then leave slots empty
        retry, retry_rejected = attempt(set(rejected))
        if len(retry_rejected) < len(rejected):
            out = retry
    return out


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
        "Each word is one plain word: no underscores, hyphens or spaces.",
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
