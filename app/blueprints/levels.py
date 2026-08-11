import json
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, jsonify, request
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Level

bp = Blueprint("levels", __name__, url_prefix="/api/levels")

MAX_DEPTH = 12


class TreeError(ValueError):
    pass


def normalize_tree(node, depth=0):
    """Validate + strip an incoming tree to the canonical persisted shape."""
    if depth > MAX_DEPTH:
        raise TreeError(f"Tree is deeper than {MAX_DEPTH} levels")
    if not isinstance(node, dict):
        raise TreeError("Each tree node must be an object")

    word = node.get("word")
    if word is None:
        raise TreeError("Each tree node needs a `word` field")

    out = {"word": str(word).strip().upper(), "hidden": bool(node.get("hidden"))}

    offset = node.get("offset")
    if isinstance(offset, dict):
        x, y = offset.get("x") or 0, offset.get("y") or 0
        if x or y:
            out["offset"] = {"x": float(x), "y": float(y)}

    children = node.get("children") or []
    if not isinstance(children, list):
        raise TreeError("`children` must be a list")
    if children:
        out["children"] = [normalize_tree(c, depth + 1) for c in children]
    return out


def next_level_number():
    highest = db.session.execute(db.select(db.func.max(Level.number))).scalar()
    return (highest or 0) + 1


def apply_payload(level, data, *, partial=False):
    if "number" in data:
        number = int(data["number"])
        if number < 1:
            raise TreeError("Level number must be >= 1")
        clash = db.session.execute(
            db.select(Level).filter(Level.number == number, Level.id != level.id)
        ).scalar_one_or_none()
        if clash:
            raise TreeError(f"Level number {number} is already used by '{clash.name or clash.id}'")
        level.number = number
    if "name" in data:
        level.name = str(data["name"]).strip()[:120]
    if "moves" in data:
        level.moves = max(1, int(data["moves"]))
    if "coins" in data:
        level.coins = max(0, int(data["coins"]))
    if "status" in data and data["status"] in ("draft", "published"):
        level.status = data["status"]
    if "tree" in data:
        level.tree = normalize_tree(data["tree"])
    elif not partial:
        raise TreeError("`tree` is required")
    level.updated_by_id = current_user.id


@bp.errorhandler(TreeError)
def handle_tree_error(err):
    return jsonify(error=str(err)), 400


@bp.get("")
@login_required
def list_levels():
    rows = db.session.execute(db.select(Level).order_by(Level.number)).scalars().all()
    return jsonify(levels=[lv.to_summary() for lv in rows])


@bp.get("/<int:level_id>")
@login_required
def get_level(level_id):
    level = db.session.get(Level, level_id) or abort(404)
    return jsonify(level.to_dict())


@bp.post("")
@login_required
def create_level():
    data = request.get_json(silent=True) or {}
    level = Level(
        number=data.get("number") or next_level_number(),
        created_by_id=current_user.id,
    )
    data.pop("number", None)
    apply_payload(level, {**data, "tree": data.get("tree") or {"word": "", "hidden": False}})
    if not level.name:
        level.name = f"Level {level.number}"
    db.session.add(level)
    db.session.commit()
    return jsonify(level.to_dict()), 201


@bp.put("/<int:level_id>")
@login_required
def update_level(level_id):
    level = db.session.get(Level, level_id) or abort(404)
    apply_payload(level, request.get_json(silent=True) or {}, partial=True)
    db.session.commit()
    return jsonify(level.to_dict())


@bp.delete("/<int:level_id>")
@login_required
def delete_level(level_id):
    level = db.session.get(Level, level_id) or abort(404)
    db.session.delete(level)
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/duplicate/<int:level_id>")
@login_required
def duplicate_level(level_id):
    src = db.session.get(Level, level_id) or abort(404)
    copy = Level(
        number=next_level_number(),
        name=f"{src.name} (copy)"[:120],
        moves=src.moves,
        coins=src.coins,
        status="draft",
        tree_json=src.tree_json,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.session.add(copy)
    db.session.commit()
    return jsonify(copy.to_dict()), 201


# ---------------------------------------------------------------- batch I/O


@bp.get("/export")
@login_required
def export_levels():
    """Batch export. `?ids=1,2,3` or all levels when omitted."""
    query = db.select(Level).order_by(Level.number)
    ids = request.args.get("ids")
    if ids:
        wanted = [int(i) for i in ids.split(",") if i.strip().isdigit()]
        query = query.filter(Level.id.in_(wanted))

    rows = db.session.execute(query).scalars().all()
    payload = {
        "format": "wordtree.levels.v1",
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "levels": [lv.to_export() for lv in rows],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="wordtree-levels-{stamp}.json"'},
    )


@bp.post("/import")
@login_required
def import_levels():
    """Batch import. Accepts a single level, a list, or {"levels":[...]}.

    `mode=skip` (default) keeps existing levels with the same number,
    `mode=overwrite` replaces them, `mode=renumber` appends them at the end.
    """
    data = request.get_json(silent=True)
    if data is None:
        abort(400, "Body must be JSON")

    if isinstance(data, dict) and "levels" in data:
        mode = data.get("mode") or request.args.get("mode") or "skip"
        items = data["levels"]
    elif isinstance(data, list):
        mode, items = request.args.get("mode", "skip"), data
    else:
        mode, items = request.args.get("mode", "skip"), [data]

    if mode not in ("skip", "overwrite", "renumber"):
        raise TreeError("mode must be one of: skip, overwrite, renumber")
    if not isinstance(items, list):
        raise TreeError("`levels` must be a list")

    created, updated, skipped, errors = [], [], [], []

    for i, raw in enumerate(items):
        try:
            if not isinstance(raw, dict):
                raise TreeError("Level entry must be an object")
            tree = normalize_tree(raw.get("tree") or {})
            number = int(raw.get("level") or raw.get("number") or 0)
            existing = (
                db.session.execute(db.select(Level).filter_by(number=number)).scalar_one_or_none()
                if number
                else None
            )

            if existing and mode == "skip":
                skipped.append(existing.number)
                continue
            if existing and mode == "overwrite":
                target, is_new = existing, False
            else:
                is_new = True
                if not number or existing:  # renumber, or no number supplied
                    number = next_level_number()
                target = Level(number=number, created_by_id=current_user.id)
                db.session.add(target)

            target.number = number
            target.name = (str(raw.get("name") or "").strip() or f"Level {number}")[:120]
            target.moves = max(1, int(raw.get("moves") or 30))
            target.coins = max(0, int(raw.get("coins") or 0))
            target.status = raw.get("status") if raw.get("status") in ("draft", "published") else "draft"
            target.tree = tree
            target.updated_by_id = current_user.id
            db.session.flush()
            (created if is_new else updated).append(number)
        except (TreeError, ValueError, TypeError) as err:
            errors.append({"index": i, "error": str(err)})

    db.session.commit()
    return jsonify(created=created, updated=updated, skipped=skipped, errors=errors)
