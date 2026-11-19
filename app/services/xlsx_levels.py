"""Read the approved-levels workbook (one tab per level) into level trees.

The sheet format is an outline: column A holds the root, column B its children,
column C theirs, and so on; row order is tree order. Only the standard library
is used — an .xlsx file is a zip of XML parts.

Every irregularity is reported as an issue on the level instead of being fixed
silently, so the sheet can be cleaned at the source.
"""

import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
TAB_RE = re.compile(r"^(?:(?P<copy>Copy of )\s*)?(?:(?:LEVEL|ID)\s+(?P<num>\d+)\s*-\s*)?(?P<topic>.*)$", re.I)
WORD_RE = re.compile(r"^[A-Z0-9][A-Z0-9 '&.%\-]*$")


def _col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _row_index(ref):
    return int(re.search(r"\d+", ref).group(0))


def _number(raw, is_percent):
    """Numeric cells come back as floats: 911.0, or 0.2 for a cell shown as 20%."""
    try:
        value = float(raw)
    except ValueError:
        return raw
    if is_percent:
        value *= 100
    text = str(int(value)) if value.is_integer() else str(value)
    return text + ("%" if is_percent else "")


def read_workbook(path):
    """[(tab name, [(row, col, text), ...]), ...] in tab order."""
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))

    percent = []
    if "xl/styles.xml" in names:
        xfs = ET.fromstring(z.read("xl/styles.xml")).find("m:cellXfs", NS)
        percent = [x.get("numFmtId") in ("9", "10") for x in xfs.findall("m:xf", NS)]

    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = []
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", NS):
        target = rels[sheet.get(f"{{{NS['r']}}}id")].lstrip("/")
        part = target if target.startswith("xl/") else "xl/" + target
        cells = []
        for c in ET.fromstring(z.read(part)).iter(f"{{{NS['m']}}}c"):
            kind, v = c.get("t"), c.find("m:v", NS)
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            elif v is None:
                continue
            elif kind == "s":
                text = shared[int(v.text)]
            else:
                text = _number(v.text, percent[int(c.get("s") or 0)] if percent else False)
            if text and text.strip():
                ref = c.get("r")
                cells.append((_row_index(ref), _col_index(ref), text.strip()))
        cells.sort()
        out.append((sheet.get("name"), cells))
    return out


def _normalize_word(raw, issues):
    word = raw.strip()
    if re.search(r"[a-z][A-Z]", word):           # MainCourse -> MAIN COURSE
        fixed = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", word)
        issues.append(f"camelCase '{word}' split into '{fixed.upper()}'")
        word = fixed
    word = re.sub(r"\s+", " ", word).upper()
    if not WORD_RE.match(word):
        issues.append(f"unusual characters in '{word}'")
    elif re.fullmatch(r"[\d.%]+", word):
        issues.append(f"number '{word}' used as a word")
    return word


def _split_blocks(cells):
    """Side-by-side trees: a column gap of 2+ starts a new tree."""
    cols = sorted({c for _, c, _ in cells})
    starts = [cols[0]] + [b for a, b in zip(cols, cols[1:]) if b - a > 1]
    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else None
        blocks.append([(r, c - start, t) for r, c, t in cells if c >= start and (end is None or c < end)])
    return blocks


def _build(cells, issues):
    roots = [i for i, (_, c, _) in enumerate(cells) if c == 0]
    shift = 0
    if len(roots) > 1:
        issues.append(
            f"{len(roots)} words in the root column ({', '.join(cells[i][2] for i in roots[1:])}) — "
            "treated as children of the first root"
        )
    if not roots or roots[0] != 0:
        issues.append("first word is not in column A")

    root, stack = None, []
    for i, (_, col, text) in enumerate(cells):
        if root is not None and col == 0:
            shift = 1
        depth = col + shift if root is not None else 0
        node = {"word": _normalize_word(text, issues), "hidden": depth > 0, "children": []}
        if root is None:
            root, stack = node, [node]
            continue
        if depth > len(stack):
            issues.append(f"'{node['word']}' skips a column — attached to the nearest parent")
            depth = len(stack)
        stack = stack[:depth]
        stack[-1]["children"].append(node)
        stack.append(node)
    return _prune(root)


def _prune(node):
    if not node["children"]:
        node.pop("children")
    else:
        for c in node["children"]:
            _prune(c)
    return node


def _walk(node, depth=0):
    yield node, depth
    for c in node.get("children") or []:
        yield from _walk(c, depth + 1)


def _check(tree, issues):
    words = Counter(n["word"] for n, _ in _walk(tree))
    dupes = sorted(w for w, k in words.items() if k > 1)
    if dupes:
        issues.append("duplicate words: " + ", ".join(dupes))
    near = sorted(w for w in words if w + "S" in words or w + "ES" in words)
    if near:
        issues.append("singular and plural both used: " + ", ".join(f"{w}/{w}S" for w in near))


def load_levels(path):
    """Parse the workbook into levels plus the per-level issues.

    Returns a list of dicts: number (may be None), tab, name, tree, issues, skip.
    `skip` holds a reason when the tab should not be imported (an exact copy).
    """
    levels = []
    for tab, cells in read_workbook(path):
        m = TAB_RE.match(tab.strip())
        topic = m.group("topic").strip(" -+")
        number = int(m.group("num")) if m.group("num") else None
        if not cells:
            levels.append({"tab": tab, "number": number, "name": topic.title(), "tree": None,
                           "issues": ["empty tab"], "skip": "empty"})
            continue

        blocks = _split_blocks(cells)
        for b, block in enumerate(blocks):
            issues = []
            if len(blocks) > 1:
                issues.append(f"tab holds {len(blocks)} side-by-side trees — this is tree {b + 1}")
            if m.group("copy"):
                issues.append("tab is a 'Copy of' another tab")
            tree = _build(block, issues)
            _check(tree, issues)
            if tree["word"].replace(" ", "") != topic.upper().replace(" ", ""):
                issues.append(f"root word '{tree['word']}' differs from the tab name '{topic}'")

            name = topic.title() + (f" ({chr(65 + b)})" if len(blocks) > 1 else "")
            levels.append({"tab": tab, "number": number if b == 0 else None, "name": name,
                           "tree": tree, "issues": issues, "skip": None})

    # originals before their "Copy of" tabs, so they keep the number and the plain name
    levels.sort(key=lambda lv: lv["tab"].lower().startswith("copy of"))
    seen, names = {}, Counter()
    for lv in levels:
        key = repr(lv["tree"])
        if lv["tree"] and key in seen:
            lv["skip"] = f"identical to '{seen[key]}'"
        seen.setdefault(key, lv["tab"])
        if lv["skip"]:
            continue
        names[lv["name"]] += 1
        if names[lv["name"]] > 1:     # a second version of a topic gets its own name
            lv["name"] = f"{lv['name']} ({names[lv['name']]})"

    counts = Counter(lv["number"] for lv in levels if lv["number"] and not lv["skip"])
    for lv in levels:
        if lv["number"] and counts[lv["number"]] > 1 and not lv["skip"]:
            lv["issues"].append(f"level number {lv['number']} is used by more than one tab")
    return levels


def stats(tree):
    nodes = list(_walk(tree))
    return {"words": len(nodes), "depth": 1 + max(d for _, d in nodes)}
