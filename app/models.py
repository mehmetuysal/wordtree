import json
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager

ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLES = (ROLE_ADMIN, ROLE_EDITOR)


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default=ROLE_EDITOR)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_active(self):
        return self.active

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "active": self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Level(db.Model):
    __tablename__ = "levels"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="")
    moves = db.Column(db.Integer, nullable=False, default=30)
    coins = db.Column(db.Integer, nullable=False, default=100)
    status = db.Column(db.String(16), nullable=False, default="draft")  # draft|published
    tree_json = db.Column(db.Text, nullable=False, default="{}")

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @property
    def tree(self):
        try:
            return json.loads(self.tree_json)
        except (TypeError, ValueError):
            return {}

    @tree.setter
    def tree(self, value):
        self.tree_json = json.dumps(value, ensure_ascii=False)

    def hidden_count(self):
        def walk(node):
            if not isinstance(node, dict):
                return 0
            n = 1 if node.get("hidden") else 0
            for child in node.get("children") or []:
                n += walk(child)
            return n

        return walk(self.tree)

    def word_count(self):
        def walk(node):
            if not isinstance(node, dict):
                return 0
            return 1 + sum(walk(c) for c in node.get("children") or [])

        tree = self.tree
        return walk(tree) if tree else 0

    def to_summary(self):
        return {
            "id": self.id,
            "number": self.number,
            "name": self.name,
            "moves": self.moves,
            "coins": self.coins,
            "status": self.status,
            "words": self.word_count(),
            "hidden": self.hidden_count(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_dict(self):
        data = self.to_summary()
        data["tree"] = self.tree
        data["updated_by"] = self.updated_by.username if self.updated_by else None
        return data

    def to_export(self):
        """The portable level format — same shape the standalone designer emits."""
        return {
            "level": self.number,
            "name": self.name,
            "moves": self.moves,
            "coins": self.coins,
            "status": self.status,
            "hiddenCount": self.hidden_count(),
            "tree": self.tree,
        }
