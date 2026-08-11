from flask import Blueprint, jsonify, request
from flask_login import login_required

from ..services import ai

bp = Blueprint("ai", __name__, url_prefix="/api/ai")


@bp.errorhandler(ai.AIUnavailable)
def handle_unavailable(err):
    return jsonify(error=str(err)), 503


@bp.get("/status")
@login_required
def status():
    return jsonify(configured=ai.is_configured())


@bp.post("/generate-tree")
@login_required
def generate_tree():
    data = request.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify(error="A topic is required."), 400

    tree = ai.generate_tree(
        topic,
        breadth=min(6, max(2, int(data.get("breadth") or 3))),
        depth=min(5, max(2, int(data.get("depth") or 3))),
        hide_from_depth=max(0, int(data.get("hideFromDepth") or 1)),
    )
    return jsonify(tree=tree)


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

    node = ai.regenerate_branch(
        word,
        shape,
        path=path,
        avoid=data.get("avoid") or [],
        keep_word=bool(data.get("keepWord")),
        topic=topic,
    )
    return jsonify(node=node)


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
    )
    return jsonify(words=words)
