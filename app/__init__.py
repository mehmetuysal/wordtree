import os

from dotenv import load_dotenv
from flask import Flask

from .config import Config
from .extensions import csrf, db, login_manager


def create_app(config_object=Config):
    load_dotenv()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .blueprints.admin import bp as admin_bp
    from .blueprints.ai import bp as ai_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.editor import bp as editor_bp
    from .blueprints.levels import bp as levels_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(editor_bp)
    app.register_blueprint(levels_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(ai_bp)

    from . import cli

    cli.register(app)

    with app.app_context():
        db.create_all()
        _bootstrap_admin(app)

    return app


def _bootstrap_admin(app):
    """Create the very first admin from ADMIN_USERNAME / ADMIN_PASSWORD.

    On a deployed instance there is no other way in: the tables are created
    automatically but an empty users table just renders a login nobody can pass,
    with no error to explain it. Only ever runs when there are no users at all.
    """
    from sqlalchemy.exc import IntegrityError

    from .models import ROLE_ADMIN, User

    if db.session.execute(db.select(db.func.count(User.id))).scalar():
        return

    username = (app.config.get("ADMIN_USERNAME") or "").strip()
    password = app.config.get("ADMIN_PASSWORD") or ""
    if not username or not password:
        app.logger.warning(
            "No users exist yet, so nobody can sign in. Set ADMIN_USERNAME and "
            "ADMIN_PASSWORD and redeploy, or run `flask create-user NAME`."
        )
        return

    user = User(username=username, role=ROLE_ADMIN)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()   # another worker got there first
        return
    app.logger.warning(
        "Created admin '%s' from ADMIN_USERNAME/ADMIN_PASSWORD. Remove those two "
        "variables now — the password is sitting in the service environment.",
        username,
    )
