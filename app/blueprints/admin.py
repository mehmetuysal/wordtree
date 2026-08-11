from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import ROLES, User

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


@bp.get("/users")
@admin_required
def users():
    rows = db.session.execute(db.select(User).order_by(User.username)).scalars().all()
    return render_template("admin/users.html", users=rows, roles=ROLES)


@bp.post("/users")
@admin_required
def create_user():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    email = (request.form.get("email") or "").strip() or None
    role = request.form.get("role") if request.form.get("role") in ROLES else "editor"

    if not username or not password:
        flash("Username and password are required.", "error")
    elif db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none():
        flash(f"User '{username}' already exists.", "error")
    else:
        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f"User '{username}' created.", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>")
@admin_required
def update_user(user_id):
    user = db.session.get(User, user_id) or abort(404)
    action = request.form.get("action")

    if action == "role":
        role = request.form.get("role")
        if role not in ROLES:
            abort(400)
        if user.id == current_user.id and role != "admin":
            flash("You cannot remove your own admin role.", "error")
        else:
            user.role = role
            db.session.commit()
    elif action == "toggle":
        if user.id == current_user.id:
            flash("You cannot disable your own account.", "error")
        else:
            user.active = not user.active
            db.session.commit()
    elif action == "password":
        password = request.form.get("password") or ""
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            user.set_password(password)
            db.session.commit()
            flash(f"Password updated for '{user.username}'.", "success")
    elif action == "delete":
        if user.id == current_user.id:
            flash("You cannot delete your own account.", "error")
        else:
            db.session.delete(user)
            db.session.commit()
            flash("User deleted.", "success")
    else:
        abort(400)

    return redirect(url_for("admin.users"))
