import logging
import os
import re

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

    app.logger.setLevel(logging.INFO)   # otherwise the startup diagnostics never print
    _log_database(app)

    with app.app_context():
        db.create_all()
        _bootstrap_admin(app)

    return app


def _log_database(app):
    """Say out loud where the data goes — and shout if it won't survive a deploy.

    On a container host (Railway) the filesystem is rebuilt on every deploy, so
    a SQLite file that isn't on a mounted volume silently loses every level and
    user each time you push.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:"):
        safe = re.sub(r"//[^@/]*@", "//***@", uri)   # never log the password
        app.logger.info("Database: %s", safe)
        return

    path = uri.split("sqlite:///")[-1]
    on_railway = bool(os.environ.get("RAILWAY_SERVICE_ID") or os.environ.get("RAILWAY_ENVIRONMENT_NAME"))
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "")
    if not on_railway:
        app.logger.info("Database: sqlite at %s", path)
    elif not mount:
        app.logger.warning(
            "Database: sqlite at %s with no volume mounted — this file is WIPED on "
            "every deploy. Attach a volume mounted at %s, or set DATABASE_URL to a "
            "Postgres URL.", path, os.path.dirname(path) or "/app/instance",
        )
    elif not os.path.abspath(path).startswith(os.path.abspath(mount)):
        app.logger.warning(
            "Database: sqlite at %s but the volume is mounted at %s — the database "
            "is outside the volume and will be WIPED on every deploy.", path, mount,
        )
    else:
        app.logger.info("Database: sqlite at %s (on the volume, persists)", path)


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
