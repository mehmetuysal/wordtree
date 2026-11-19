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

    @app.cli.command("import-xlsx")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--mode", type=click.Choice(["skip", "overwrite"]), default="skip",
                  help="What to do when a level number already exists.")
    @click.option("--status", type=click.Choice(["draft", "published"]), default="published")
    @click.option("--dry-run", is_flag=True, help="Only print the report, write nothing.")
    def import_xlsx(path, mode, status, dry_run):
        """Import the approved-levels workbook (one tab per level)."""
        from .blueprints.levels import normalize_tree
        from .services.xlsx_levels import load_levels, stats

        levels = load_levels(path)
        taken = {n for n in db.session.execute(db.select(Level.number)).scalars()}
        wanted = {lv["number"] for lv in levels if lv["number"] and not lv["skip"]}
        next_free = max(taken | wanted | {0}) + 1
        claimed, created, updated, skipped = set(), [], [], []

        for lv in levels:
            label = f"{lv['tab']!r}" + (f" -> {lv['name']}" if lv["name"] != lv["tab"] else "")
            if lv["skip"]:
                click.echo(f"SKIP  {label}: {lv['skip']}")
                continue
            number = lv["number"] if lv["number"] not in claimed else None
            # tabs without a usable number are matched by name, so a rerun is a no-op
            existing = db.session.execute(
                db.select(Level).filter_by(number=number) if number else
                db.select(Level).filter_by(name=lv["name"][:120]).limit(1)
            ).scalar_one_or_none()
            if not number:
                number = existing.number if existing else next_free
                next_free += 0 if existing else 1
                lv["issues"].append(f"no level number of its own — level {number}")
            claimed.add(number)
            info = stats(lv["tree"])
            for issue in lv["issues"]:
                click.echo(f"WARN  {label}: {issue}")

            if existing and mode == "skip":
                skipped.append(number)
                continue
            target = existing or Level(number=number)
            target.name = lv["name"][:120]
            target.status = status
            target.tree = normalize_tree(lv["tree"])
            if not existing:
                target.moves, target.coins = 30, 100
                db.session.add(target)
            (updated if existing else created).append(number)
            click.echo(f"{'UPD ' if existing else 'ADD '}  #{number} {lv['name']} "
                       f"({info['words']} words, depth {info['depth']})")

        if dry_run:
            db.session.rollback()
            click.echo(f"dry run: would create {len(created)}, update {len(updated)}, skip {len(skipped)}")
            return
        db.session.commit()
        click.echo(f"created {len(created)}, updated {len(updated)}, skipped {len(skipped)} existing")

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
