import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Heroku/Railway-style URLs use the legacy scheme SQLAlchemy 2 rejects
        return url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url

    # No explicit URL. If a Railway volume is attached, keep SQLite on it —
    # anywhere else on a container host the file dies with the next deploy.
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume:
        return f"sqlite:///{Path(volume) / 'wordtree.db'}"
    return f"sqlite:///{BASE_DIR / 'instance' / 'wordtree.db'}"


def _engine_options(uri):
    # hosted Postgres drops idle connections — check one before handing it out
    options = {"pool_pre_ping": True}
    if uri.startswith("sqlite"):
        # concurrent writers wait for the lock instead of failing immediately
        options["connect_args"] = {"timeout": 15}
    return options


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(SQLALCHEMY_DATABASE_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_TIME_LIMIT = None

    # First admin for a fresh deployment — see _bootstrap_admin() in app/__init__.py
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    # OpenAI — used by app/services/ai.py
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", 45))
    # GPT-5 models only: "none" | "low" | "medium" | "high". Anything above low
    # makes the flagship spend minutes thinking about a one-word edit.
    OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "low")
