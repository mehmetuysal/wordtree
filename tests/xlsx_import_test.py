"""The approved-levels workbook parser and `flask import-xlsx`, against a tiny
synthetic workbook that has every irregularity the real sheet has."""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SECRET_KEY"] = "test"
for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "ADMIN_USERNAME", "ADMIN_PASSWORD"):
    os.environ[name] = ""
db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Level  # noqa: E402
from app.services import ai  # noqa: E402
from app.services.xlsx_levels import load_levels  # noqa: E402

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def cell(col, row, value, style=0):
    ref = f"{chr(65 + col)}{row}"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def write_workbook(path, tabs):
    """tabs: {name: [(col, word), ...]} — one row per entry, in order."""
    with zipfile.ZipFile(path, "w") as z:
        sheets, rels = [], []
        for i, (name, rows) in enumerate(tabs.items(), 1):
            body = "".join(f'<row r="{r}">{cell(c, r, *v) if isinstance(v, tuple) else cell(c, r, v)}</row>'
                           for r, (c, v) in enumerate(rows, 1))
            z.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{M}"><sheetData>{body}</sheetData></worksheet>')
            sheets.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>{"".join(sheets)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships>{"".join(rels)}</Relationships>')
        z.writestr("xl/styles.xml", f'<styleSheet xmlns="{M}"><cellXfs><xf numFmtId="0"/><xf numFmtId="9"/></cellXfs></styleSheet>')


FOOD = [(0, "FOOD"), (1, "FRUIT"), (2, "APPLE"), (1, "Ice Cream"), (2, "VANILLA")]
tabs = {
    "Copy of LEVEL 1-FOOD": FOOD,                                  # identical copy, listed first
    "LEVEL 1-FOOD": FOOD,
    "LEVEL 2-TIP": [(0, "TIP"), (1, "MainCourse"), (1, (0.2, 1)), (1, (911.0,)), (1, "TIP")],
    "ID 7-FORCE": [(0, "FORCE"), (1, "PUSH"), (2, "DOOR"), (0, "PULL"), (1, "ROPE")],
    "PAIRS": [(0, "PAIRS"), (7, "PAIRS"), (1, "HOT"), (8, "LOCK"), (2, "COLD"), (9, "KEY")],
    "Copy of ID 7-FORCE": [(0, "FORCE"), (1, "MAGNET")],
}
xlsx = tempfile.mktemp(suffix=".xlsx")
write_workbook(xlsx, tabs)

levels = load_levels(xlsx)
by = {(lv["tab"], lv["name"]): lv for lv in levels}

food = by[("LEVEL 1-FOOD", "Food")]
assert not food["skip"] and food["number"] == 1, food
assert by[("Copy of LEVEL 1-FOOD", "Food")]["skip"] == "identical to 'LEVEL 1-FOOD'"
assert food["tree"] == {"word": "FOOD", "hidden": False, "children": [
    {"word": "FRUIT", "hidden": True, "children": [{"word": "APPLE", "hidden": True}]},
    {"word": "ICE CREAM", "hidden": True, "children": [{"word": "VANILLA", "hidden": True}]},
]}, food["tree"]

tip = by[("LEVEL 2-TIP", "Tip")]
assert [c["word"] for c in tip["tree"]["children"]] == ["MAIN COURSE", "20%", "911", "TIP"], tip["tree"]
assert any("camelCase" in i for i in tip["issues"]) and any("duplicate words: TIP" in i for i in tip["issues"])

force = by[("ID 7-FORCE", "Force")]
assert [c["word"] for c in force["tree"]["children"]] == ["PUSH", "PULL"], force["tree"]
assert force["tree"]["children"][1]["children"][0]["word"] == "ROPE"
assert any("root column" in i for i in force["issues"])
assert by[("Copy of ID 7-FORCE", "Force (2)")]["number"] == 7, "the copy keeps its number but is flagged"

a, b = by[("PAIRS", "Pairs (A)")], by[("PAIRS", "Pairs (B)")]
assert a["tree"]["children"][0]["children"][0]["word"] == "COLD"
assert b["tree"]["children"][0]["children"][0]["word"] == "KEY"

# ---- the CLI import is idempotent and keeps numbers stable


class T(Config):
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]


app = create_app(T)
runner = app.test_cli_runner()
out = runner.invoke(args=["import-xlsx", xlsx, "--dry-run"])
assert out.exit_code == 0 and "would be: created 6," in out.output, out.output
with app.app_context():
    assert db.session.execute(db.select(db.func.count(Level.id))).scalar() == 0, "dry run wrote"

with app.app_context():
    # a tab without a number that was imported before locking existed is adopted,
    # not added a second time
    from app.services.xlsx_levels import load_levels as _ll
    pairs_a = next(lv for lv in _ll(xlsx) if lv["name"] == "Pairs (A)")
    db.session.add(Level(number=40, name="Pairs (A)", tree_json=json.dumps(pairs_a["tree"])))
    # an editable level already sitting on an approved number

    db.session.add(Level(number=2, name="Mine", tree_json='{"word": "MINE"}'))
    db.session.commit()
out = runner.invoke(args=["import-xlsx", xlsx])
assert "created 4, updated 1, unchanged 0, skipped 1" in out.output, out.output
assert "KEEP  #2 is an editable level ('Mine')" in out.output, out.output
out = runner.invoke(args=["import-xlsx", xlsx, "--quiet"])
assert "created 0, updated 0, unchanged 5, skipped 1" in out.output, out.output
assert "WARN" not in out.output, "--quiet is for deploy logs"
out = runner.invoke(args=["import-xlsx", xlsx, "--mode", "overwrite"])
assert "created 0, updated 1, unchanged 5, skipped 0" in out.output, out.output
assert "REPLACE  #2 editable 'Mine' with approved 'Tip'; old tree: {\"word\": \"MINE\"" in out.output, out.output
with app.app_context():
    rows = {lv.number: lv for lv in db.session.execute(db.select(Level)).scalars()}
    assert all(lv.locked for lv in rows.values()), "approved levels are locked"
    assert rows[1].name == "Food" and rows[1].status == "published"
    assert rows[2].name == "Tip", "overwrite replaced the editable level"
    assert rows[40].name == "Pairs (A)" and len(rows) == 6, sorted(rows)
    assert rows[7].name == "Force" and {lv.name for lv in rows.values()} >= {"Force (2)", "Pairs (A)", "Pairs (B)"}

# ---- the workbook is the AI's reference, wherever the database is
app.config["APPROVED_LEVELS_PATH"] = xlsx
with app.app_context():
    refs = ai.reference_levels()
    assert [r[0] for r in refs] == [1, 2, 7, 8, 9, 10], [r[:2] for r in refs]
    assert ai.size_for_level(1)[0] >= 5
    system = ai._reference_system(refs)
    assert system.startswith(ai.SYSTEM_PROMPT) and "# Level 1\nFOOD\n  FRUIT\n    APPLE" in system
    index = ai._branch_index(refs)
    copy = {"word": "MENU", "children": [{"word": "FOOD", "children": [
        {"word": "fruit"}, {"word": "ICE CREAM"}, {"word": "PIZZA"}]}]}
    assert ai._copied_branches(copy, index) == ["FOOD > FRUIT, ICE CREAM (level 1)"], ai._copied_branches(copy, index)
    fresh = {"word": "FOOD", "children": [{"word": "FRUIT"}, {"word": "BREAD"}, {"word": "SOUP"}]}
    assert ai._copied_branches(fresh, index) == [], "reusing one word is fine"
    pair = {"word": "HOT", "children": [{"word": "COLD"}]}
    assert ai._copied_branches(pair, index) == [], "a single child is a pair, not a copy"

# ---- AI-side helpers that don't need a key
assert ai._clean_word("light_truck") == "LIGHT TRUCK"
assert ai._clean_word(" four-leaf  clover! ") == "FOUR-LEAF CLOVER"
assert ai._clean_word("rabbit's foot") == "RABBIT'S FOOT"
wide = {"word": "R", "children": [{"word": str(i), "children": [{"word": f"{i}x"}]} for i in range(5)]}
capped = ai._cap_words(wide, 7)
assert [c["word"] for c in capped["children"]] == ["0", "1", "2", "3", "4"]
assert sum(len(c["children"]) for c in capped["children"]) == 1, "the deepest level is cut first"

os.unlink(xlsx)
os.close(db_fd)
os.unlink(db_path)
print("all good")
