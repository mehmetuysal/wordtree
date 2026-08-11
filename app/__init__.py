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

    return app
