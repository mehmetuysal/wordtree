from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Level
from ..services import ai
from .levels import next_level_number, normalize_tree

bp = Blueprint("ai", __name__, url_prefix="/api/ai")


@bp.errorhandler(ai.AIUnavailable)
def handle_unavailable(err):
    return jsonify(error=str(err)), 503


@bp.errorhandler(ai.AIFailed)
def handle_failed(err):
    # JSON, not an HTML traceback — the front end parses every reply as JSON
    return jsonify(error=str(err)), 502


@bp.get("/status")
@login_required
def status():
    if not ai.is_configured():
        return jsonify(configured=False, model="")
    return jsonify(configured=True, model=ai.active_model())


@bp.post("/edit-tree")
@login_required
def edit_tree():
    data = request.get_json(silent=True) or {}
    instruction = (data.get("instruction") or "").strip()
    tree = data.get("tree")
    if not instruction:
        return jsonify(error="Tell the AI what to change."), 400
    if not isinstance(tree, dict):
        return jsonify(error="A tree is required."), 400

    updated, duplicates = ai.edit_tree(
        instruction,
        tree,
        model=data.get("model"),
        topic=(data.get("topic") or "").strip(),
    )
    return jsonify(tree=updated, duplicates=duplicates)


@bp.post("/generate-tree")
@login_required
def generate_tree():
    data = request.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify(error="A topic is required."), 400

    words = int(data.get("words") or 40)
    if not 5 <= words <= ai.MAX_GENERATED_WORDS:
        return jsonify(error=f"Ask for 5 to {ai.MAX_GENERATED_WORDS} words."), 400
    tree, copied = ai.generate_tree(
        topic,
        words=words,
        depth=min(ai.MAX_DEPTH, max(2, int(data.get("depth") or 4))),
        hide_from_depth=max(0, int(data.get("hideFromDepth") or 1)),
        model=data.get("model"),
    )
    return jsonify(tree=tree, copied=copied)


@bp.get("/level-size")
@login_required
def level_size():
    """The word count and depth the approved levels use around this number."""
    words, depth = ai.size_for_level(max(1, request.args.get("number", type=int) or 1))
    return jsonify(words=words, depth=depth, next_free=next_level_number())


@bp.post("/suggest-topics")
@login_required
def suggest_topics():
    data = request.get_json(silent=True) or {}
    count = min(30, max(1, int(data.get("count") or 10)))
    names = db.session.execute(db.select(Level.name)).scalars().all()
    avoid = [*names, *(data.get("avoid") or [])]
    return jsonify(topics=ai.suggest_topics(count, avoid=avoid))


@bp.post("/generate-level")
@login_required
def generate_level():
    """Generate one level and save it as a draft.

    `number` is where the level goes and what it's sized for — the approved
    levels aren't in every database, so the batch says which number it's at.
    Without one it takes the next free number. The batch runs one request per
    topic from the browser, so no request outlives the gunicorn timeout.
    """
    data = request.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify(error="A topic is required."), 400
    number = int(data.get("number") or 0) or next_level_number()
    if number < 1:
        return jsonify(error="Level number must be >= 1."), 400

    def taken():
        return db.session.execute(db.select(Level.id).filter_by(number=number)).first()

    if taken():
        return jsonify(error=f"Level {number} already exists."), 409
    words, depth = ai.size_for_level(number)
    tree, copied = ai.generate_tree(topic, words=words, depth=depth)
    if taken():   # someone saved that number while the AI was busy
        return jsonify(error=f"Level {number} was taken while generating."), 409
    level = Level(
        number=number,
        name=topic.title()[:120],
        status="draft",
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    level.tree = normalize_tree(tree)
    db.session.add(level)
    db.session.commit()
    return jsonify(level=level.to_summary(), copied=copied), 201


@bp.post("/regenerate-branch")
@login_required
def regenerate_branch():
    data = request.get_json(silent=True) or {}
    word = (data.get("word") or "").strip()
    shape = data.get("shape")
    path = data.get("path") or []
    topic = (data.get("topic") or "").strip()
    if not isinstance(shape, dict):
        return jsonify(error="A shape is required."), 400
    # an empty word is fine as long as there is something to go on
    if not word and len(path) < 2 and not topic:
        return jsonify(error="Name the root word or the level first."), 400

    node, copied = ai.regenerate_branch(
        word,
        shape,
        path=path,
        avoid=data.get("avoid") or [],
        keep_word=bool(data.get("keepWord")),
        topic=topic,
        model=data.get("model"),
    )
    return jsonify(node=node, copied=copied)


@bp.post("/suggest-children")
@login_required
def suggest_children():
    data = request.get_json(silent=True) or {}
    word = (data.get("word") or "").strip()
    if not word:
        return jsonify(error="A word is required."), 400

    words = ai.suggest_children(
        word,
        path=data.get("path") or [],
        count=min(8, max(1, int(data.get("count") or 4))),
        avoid=data.get("avoid") or [],
        model=data.get("model"),
    )
    return jsonify(words=words)
