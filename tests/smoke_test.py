"""Throwaway end-to-end check of the level API against a temp database."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["SECRET_KEY"] = "test"
db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402


class T(Config):
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]


app = create_app(T)
with app.app_context():
    u = User(username="tester", role="admin")
    u.set_password("pw")
    db.session.add(u)
    db.session.commit()

c = app.test_client()
assert c.get("/").status_code == 302, "anonymous must be redirected to login"
assert c.post("/login", data={"username": "tester", "password": "pw"}).status_code == 302
assert c.get("/").status_code == 200

tree = {"word": "vehicle", "hidden": False, "children": [
    {"word": "car", "hidden": True, "offset": {"x": 4, "y": -8}},
    {"word": "boat", "hidden": True},
]}

r = c.post("/api/levels", json={"name": "Vehicles", "moves": 12, "coins": 50, "tree": tree})
assert r.status_code == 201, r.get_json()
lv = r.get_json()
assert lv["number"] == 1 and lv["words"] == 3 and lv["hidden"] == 2, lv
assert lv["tree"]["word"] == "VEHICLE", "words must be normalised to uppercase"
assert lv["tree"]["children"][0]["offset"] == {"x": 4.0, "y": -8.0}, "offsets must round-trip"

r = c.put(f"/api/levels/{lv['id']}", json={"status": "published", "name": "Wheels"})
assert r.get_json()["status"] == "published" and r.get_json()["name"] == "Wheels"

r = c.post(f"/api/levels/duplicate/{lv['id']}")
assert r.status_code == 201 and r.get_json()["number"] == 2, r.get_json()

assert len(c.get("/api/levels").get_json()["levels"]) == 2

# clashing level number must be rejected
r = c.put(f"/api/levels/{lv['id']}", json={"number": 2})
assert r.status_code == 400 and "already used" in r.get_json()["error"], r.get_json()

# bad tree
r = c.post("/api/levels", json={"tree": {"nope": 1}})
assert r.status_code == 400, r.get_json()

# ---- batch export
r = c.get("/api/levels/export")
export = r.get_json()
assert r.headers["Content-Disposition"].startswith("attachment"), r.headers
assert len(export["levels"]) == 2 and export["format"] == "wordtree.levels.v1"

r = c.get(f"/api/levels/export?ids={lv['id']}")
assert len(r.get_json()["levels"]) == 1

# ---- batch import
payload = json.loads(json.dumps(export))
payload["levels"][0]["name"] = "Renamed"

res = c.post("/api/levels/import", json={"levels": payload["levels"], "mode": "skip"}).get_json()
assert res["skipped"] == [1, 2] and not res["created"], res

res = c.post("/api/levels/import", json={"levels": payload["levels"], "mode": "overwrite"}).get_json()
assert res["updated"] == [1, 2], res
assert c.get(f"/api/levels/{lv['id']}").get_json()["name"] == "Renamed"

res = c.post("/api/levels/import", json={"levels": payload["levels"], "mode": "renumber"}).get_json()
assert res["created"] == [3, 4], res

res = c.post("/api/levels/import", json={"levels": [{"tree": {"bad": True}}, {"level": 9, "tree": {"word": "X"}}]}).get_json()
assert res["created"] == [9] and len(res["errors"]) == 1, res

# ---- delete
assert c.delete("/api/levels/9999").status_code == 404
c.delete(f"/api/levels/{lv['id']}")
assert len(c.get("/api/levels").get_json()["levels"]) == 4

# ---- ai without a key
assert c.get("/api/ai/status").get_json() == {"configured": False, "model": ""}
r = c.post("/api/ai/generate-tree", json={"topic": "fruit"})
assert r.status_code == 503, (r.status_code, r.get_json())
# tree edits are validated before the key is even needed
assert c.post("/api/ai/edit-tree", json={"tree": {"word": "A"}}).status_code == 400
assert c.post("/api/ai/edit-tree", json={"instruction": "go"}).status_code == 400
r = c.post("/api/ai/edit-tree", json={"instruction": "go", "tree": {"word": "A"}})
assert r.status_code == 503, (r.status_code, r.get_json())

# ---- admin pages
assert c.get("/admin/users").status_code == 200
assert c.post("/admin/users", data={"username": "editor1", "password": "pw", "role": "editor"}).status_code == 302
with app.app_context():
    e = db.session.execute(db.select(User).filter_by(username="editor1")).scalar_one()
    assert e.role == "editor" and e.active

c.post("/logout")
c.post("/login", data={"username": "editor1", "password": "pw"})
assert c.get("/admin/users").status_code == 403, "editors must not reach user management"
assert c.get("/api/levels").status_code == 200

os.close(db_fd)
os.unlink(db_path)
print("all good")
