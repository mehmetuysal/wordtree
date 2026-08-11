"""OpenAI-backed helpers for the level designer.

Everything the app needs from OpenAI goes through this module so the rest of
the code never touches the SDK directly. Without OPENAI_API_KEY configured the
functions raise AIUnavailable and the UI degrades gracefully.
"""

import json
import re

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
    pass


def is_configured():
    return bool(current_app.config.get("OPENAI_API_KEY"))


def _client():
    if not is_configured():
        raise AIUnavailable("OPENAI_API_KEY is not configured on the server.")
    from openai import OpenAI

    return OpenAI(api_key=current_app.config["OPENAI_API_KEY"])


def _complete(user_prompt, schema, schema_name):
    client = _client()
    resp = client.chat.completions.create(
        model=current_app.config["OPENAI_MODEL"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        },
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


def generate_tree(topic, *, breadth=3, depth=3, hide_from_depth=1):
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
        raw = _decorate(_complete(text, _tree_schema(depth), "word_tree"), hide_from_depth=hide_from_depth)
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


def regenerate_branch(word, shape, *, path=None, avoid=None, keep_word=False, topic=""):
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
        node = _fit_shape(_complete(" ".join(prompt), _tree_schema(depth), "word_branch"), shape)
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


def suggest_children(word, *, path=None, count=4, avoid=None):
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
        result = _complete(" ".join(prompt), schema, "child_words")
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
