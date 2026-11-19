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
                  help="When an editable level holds an approved number: leave it (skip) "
                       "or replace it with the approved one (overwrite).")
    @click.option("--dry-run", is_flag=True, help="Only print the report, write nothing.")
    @click.option("--quiet", is_flag=True, help="Only print conflicts and the totals (for deploys).")
    def import_xlsx(path, mode, dry_run, quiet):
        """Sync the approved-levels workbook into the database as locked levels.

        Approved levels are read-only in the app; this command is the only way to
        change them, so it is safe to run on every deploy: changed tabs are
        updated, unchanged ones left alone.
        """
        from .blueprints.levels import normalize_tree
        from .services.xlsx_levels import load_levels, stats

        say = (lambda *_: None) if quiet else click.echo
        levels = load_levels(path)
        taken = {n for n in db.session.execute(db.select(Level.number)).scalars()}
        wanted = {lv["number"] for lv in levels if lv["number"] and not lv["skip"]}
        next_free = max(taken | wanted | {0}) + 1
        claimed, counts = set(), {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}

        for lv in levels:
            label = f"{lv['tab']!r}" + (f" -> {lv['name']}" if lv["name"] != lv["tab"] else "")
            if lv["skip"]:
                say(f"SKIP  {label}: {lv['skip']}")
                continue
            number = lv["number"] if lv["number"] not in claimed else None
            tree = normalize_tree(lv["tree"])
            if number:
                existing = db.session.execute(db.select(Level).filter_by(number=number)).scalar_one_or_none()
            else:
                # tabs without a usable number are found again by name, so a rerun
                # doesn't add them twice: an approved level, or an editable one that
                # is an untouched copy of this tab (imported before locking existed)
                same_name = db.session.execute(
                    db.select(Level).filter_by(name=lv["name"][:120]).order_by(Level.locked.desc())
                ).scalars().all()
                existing = next((x for x in same_name if x.locked or x.tree == tree), None)
                number = existing.number if existing else next_free
                next_free += 0 if existing else 1
                lv["issues"].append(f"no level number of its own — level {number}")
            claimed.add(number)
            for issue in lv["issues"]:
                say(f"WARN  {label}: {issue}")

            # an editable level with exactly this tree is the approved one, imported
            # before locking existed: adopt it. Only real edits are kept.
            if existing and not existing.locked and existing.tree != tree and mode == "skip":
                click.echo(f"KEEP  #{number} is an editable level ({existing.name!r}); "
                           f"approved {lv['name']!r} not imported. Use --mode overwrite to replace it.")
                counts["skipped"] += 1
                continue
            if existing and existing.locked and existing.tree == tree and existing.name == lv["name"][:120]:
                counts["unchanged"] += 1
                continue

            target = existing or Level(number=number, moves=30, coins=100)
            target.name = lv["name"][:120]
            target.status = "published"
            target.locked = True
            target.tree = tree
            if not existing:
                db.session.add(target)
            counts["updated" if existing else "created"] += 1
            info = stats(lv["tree"])
            say(f"{'UPD ' if existing else 'ADD '}  #{number} {lv['name']} "
                f"({info['words']} words, depth {info['depth']})")

        totals = ", ".join(f"{k} {v}" for k, v in counts.items())
        if dry_run:
            db.session.rollback()
            click.echo(f"dry run, would be: {totals}")
            return
        db.session.commit()
        click.echo(f"approved levels: {totals}")

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
