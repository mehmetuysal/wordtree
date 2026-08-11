import click

from .extensions import db
from .models import ROLES, Level, User


def _n(word, hidden, *children):
    return {"word": word, "hidden": hidden, "children": list(children)}


DEMO_TREE = _n(
    "VEHICLE", True,
    _n("LAND", False,
       _n("CAR", True, _n("SUV", True), _n("SEDAN", False)),
       _n("TRUCK", False),
       _n("BUS", True),
       _n("BIKE", True, _n("CHAIN", True), _n("HANDLEBAR", True), _n("PEDAL", False))),
    _n("AIR", True, _n("PLANE", True), _n("JET", False), _n("HELICOPTER", False)),
    _n("SEA", True, _n("BOAT", True), _n("SHIP", True), _n("FERRY", False)),
)


def register(app):
    @app.cli.command("init-db")
    def init_db():
        """Create all database tables."""
        db.create_all()
        click.echo("Database ready.")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--email", default=None)
    @click.option("--role", type=click.Choice(ROLES), default="editor")
    def create_user(username, password, email, role):
        """Create a user. The first user is always an admin."""
        if db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none():
            raise click.ClickException(f"User '{username}' already exists.")
        if db.session.execute(db.select(db.func.count(User.id))).scalar() == 0:
            role = "admin"
        user = User(username=username, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created {role} '{username}'.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Insert the VEHICLE sample level from the original prototype."""
        if db.session.execute(db.select(Level).filter_by(number=1)).scalar_one_or_none():
            raise click.ClickException("Level 1 already exists.")
        level = Level(number=1, name="Vehicles", moves=30, coins=100, status="draft")
        level.tree = DEMO_TREE
        db.session.add(level)
        db.session.commit()
        click.echo("Seeded level 1 'Vehicles'.")

    @app.cli.command("reset-password")
    @click.argument("username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def reset_password(username, password):
        """Reset a user's password."""
        user = db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none()
        if not user:
            raise click.ClickException(f"No such user: {username}")
        user.set_password(password)
        db.session.commit()
        click.echo(f"Password updated for '{username}'.")
