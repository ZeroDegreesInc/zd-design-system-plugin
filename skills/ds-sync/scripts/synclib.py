"""Shared helpers for /ds-sync: canonical JSON, fingerprints, token projections,
and markdown parsing.

The Figma script (figma_read.template.js) implements canon() and fnv() the same
way, byte for byte. If you change one, change the other, or every item will
report as changed.
"""
import fnmatch
import hashlib
import json
import re
from pathlib import Path

# ── canonical JSON and fingerprints ──────────────────────────────────

def norm(x):
    """Round floats to 4 places and write whole numbers as ints, as the JS side does."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        r = round(float(x), 4)
        return int(r) if r.is_integer() else r
    if isinstance(x, list):
        return [norm(v) for v in x]
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    return x


def canon(x):
    return json.dumps(norm(x), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fnv(s):
    """FNV-1a 32-bit over UTF-16 code units, as hex. Matches the JS fnv()."""
    h = 0x811C9DC5
    b = s.encode("utf-16-le")
    for i in range(0, len(b), 2):
        h ^= b[i] | (b[i + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, "08x")


def fp(x):
    return fnv(canon(x))


def sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ── project files ────────────────────────────────────────────────────

# The project formats these scripts read and write. A project names its own in
# design-system.config.yaml (`format_version`); one without it predates the field
# and is format 1. Raise this, and keep the old number while it's still readable,
# when a release changes a file format in a way older scripts would misread.
FORMAT_VERSIONS = {1}


def load_config(root):
    """A tiny YAML reader for design-system.config.yaml (flat keys, one level of nesting, inline lists).

    Stops if the project's format_version isn't one these scripts support, so a
    skill never half-writes a project in a format it doesn't know.
    """
    cfg = _read_config(root)
    v = cfg.get("format_version") or "1"
    if not str(v).isdigit() or int(v) not in FORMAT_VERSIONS:
        known = ", ".join(str(n) for n in sorted(FORMAT_VERSIONS))
        newest = max(FORMAT_VERSIONS)
        hint = ("Update the zd-design-system plugin from the organization's plugin library."
                if str(v).isdigit() and int(v) > newest else
                "Bring the project up to date with the starter kit's FORMATS.md, then set its format_version.")
        raise SystemExit(f"This project's design-system.config.yaml says format_version {v}, "
                         f"but this version of the skills reads format {known}. {hint}")
    return cfg


def _read_config(root):
    cfg, section = {}, None
    for raw in (root / "design-system.config.yaml").read_text().splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and section:
            cfg.setdefault(section, []).append(_scalar(line[4:]))
            continue
        m = re.match(r"^(\s*)([\w-]+):\s*(.*)$", line)
        if not m:
            continue
        indent, key, val = m.groups()
        if indent:
            if isinstance(cfg.get(section), dict):
                cfg[section][key] = _scalar(val)
            continue
        section = key
        cfg[key] = _scalar(val) if val else {}
        if val == "" and key == "exclude":
            cfg[key] = []
    return cfg


def _scalar(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    if v in ("null", "~", ""):
        return None
    if v in ("true", "false"):
        return v == "true"
    return v.strip("\"'")


def excluded(name, cfg):
    return any(fnmatch.fnmatch(name, pat) for pat in (cfg.get("exclude") or []))


def markdown_files(root):
    """Every content file the markdown place is made of, in a stable order."""
    keep = {".md", ".json", ".yaml", ".svg"}  # content only; tooling such as _scripts/ is not part of the place
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and ".sync" not in p.parts and "__pycache__" not in p.parts
        and "intake" not in p.parts      # proposals from /brand-intake, not part of the system yet
        and (p.suffix in keep or p.name == "LICENSE")
    )


SECTION = re.compile(r"<!-- sync:begin (\S+) [^>]*-->\n(.*?)<!-- sync:end \1 -->", re.S)


def file_record(path):
    """Whole-file hash plus a hash per synced section, so a check can tell hand-written edits from generated ones."""
    text = path.read_bytes()
    rec = {"hash": sha(text)}
    if path.suffix == ".md":
        secs = {k: fnv(body) for k, body in SECTION.findall(text.decode())}
        if secs:
            rec["sections"] = secs
    return rec


def sections_beyond_renames(text, base_sections, renames):
    """Generated sections that differ from the snapshot even after undoing token renames ({old: new})."""
    def undo(body):
        for old, new in renames.items():
            body = body.replace(f"`{new}`", f"`{old}`")
        return body
    now = {k: fnv(undo(body)) for k, body in SECTION.findall(text)}
    return sorted(k for k in set(now) | set(base_sections or {}) if now.get(k) != (base_sections or {}).get(k))


def frontmatter(md_text):
    """A small reader for the frontmatter shapes FORMATS.md allows: scalars, inline lists,
    block lists, and one level of nested keys (whose own block lists are kept too)."""
    m = re.match(r"---\n(.*?)\n---\n", md_text, re.S)
    if not m:
        return {}
    fm, cur, sub = {}, None, None
    for line in m.group(1).splitlines():
        line = re.sub(r"\s+#.*$", "", line)
        if not line.strip():
            continue
        top = re.match(r"^([\w-]+):\s*(.*)$", line)
        if top:
            cur, val = top.groups()
            sub = None
            fm[cur] = _fm_value(val) if val else {}
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item:
            target = fm.get(cur)
            if isinstance(target, dict) and sub:
                if not isinstance(target.get(sub), list):
                    target[sub] = []
                target[sub].append(item.group(1).strip())
            else:
                if not isinstance(target, list):
                    fm[cur] = []
                fm[cur].append(item.group(1).strip())
            continue
        nested = re.match(r"^\s+([\w-]+):\s*(.*)$", line)
        if nested and isinstance(fm.get(cur), dict):
            sub, val = nested.groups()
            fm[cur][sub] = _fm_value(val) if val else None
    return fm


def _fm_value(val):
    val = val.strip()
    if val.startswith("["):
        return [x.strip().strip("\"'") for x in val.strip("[]").split(",") if x.strip()]
    return None if val == "null" else val.strip("\"'")


def new_component_files(root, snap):
    """{name: {"node", "file"}} for component files whose Figma component the snapshot doesn't track yet:
    a file written for a new component (/build-component). Sync adopts it on the next run."""
    known_nodes = {e.get("figma_node") for e in snap["components"].values()}
    out = {}
    for p in sorted((root / "components").glob("*.md")):
        fm = frontmatter(p.read_text())
        node = (fm.get("figma") or {}).get("node")
        name = fm.get("name")
        if name and node and node != "0:0" and name not in snap["components"] and node not in known_nodes:
            out[name] = {"node": node, "file": str(p.relative_to(root))}
    return out


def parse_internal(md_text):
    """`internal` entries look like `{ name: .ButtonMaster, node: "63:5244", role: wrapper }`."""
    return [
        {"name": n, "node": node, "role": role}
        for n, node, role in re.findall(r"\{\s*name:\s*([^,]+?),\s*node:\s*\"([^\"]+)\",\s*role:\s*(\w+)\s*\}", md_text)
    ]


# ── tokens: name map and projection ──────────────────────────────────

STYLE_GROUPS = {"text-styles", "effect-styles", "grid-styles"}


def token_nodes(tokens):
    """{Figma name: (group, path, node)} for every token and style in tokens.json."""
    out = {}

    def walk(o, group, path):
        for k, v in o.items():
            if k.startswith("$") or not isinstance(v, dict):
                continue
            if "$value" in v:
                fig = v.get("$extensions", {}).get("figma", {})
                name = fig.get("styleName") or "/".join(path + [k])
                out[name] = (group, ".".join([group] + path + [k]), v)
            else:
                walk(v, group, path + [k])

    for g, o in tokens.items():
        if not g.startswith("$"):
            walk(o, g, [])
    return out


def alias_map(nodes):
    """'{semantic-color.content.body}' → 'content/body'."""
    return {"{" + dotted + "}": name for name, (_, dotted, _) in nodes.items()}


def _ref(v, amap):
    if isinstance(v, str) and v.startswith("{"):
        return "@" + amap.get(v, v)
    return v


def _color(c):
    s = c["hex"].lower()
    a = c.get("alpha")
    return s if a is None or a >= 1 else s + "/" + str(norm(round(a, 2)))


def _val(t, v, amap, fig):
    if isinstance(v, str) and v.startswith("{"):
        return "@" + amap.get(v, v)
    if t == "color":
        if fig.get("colorFrom"):
            return "@" + amap.get(fig["colorFrom"], fig["colorFrom"]) + "/" + str(norm(fig["opacityPercent"] / 100))
        return _color(v)
    if t in ("dimension", "duration"):
        return v["value"]
    return v


def project(name, entry, amap):
    """The comparable shape of one token or style. The Figma script builds the same shape from live data."""
    group, _, node = entry
    t, v = node["$type"], node["$value"]
    fig = node.get("$extensions", {}).get("figma", {})
    d = node.get("$description") or ""
    if t == "typography":
        return {
            "kind": "text", "description": d,
            "fontFamily": _ref(v["fontFamily"], amap), "fontWeight": _ref(v["fontWeight"], amap),
            "fontSize": _ref(v["fontSize"], amap), "lineHeight": _ref(v["lineHeight"], amap),
            "letterSpacing": f'{norm(v["letterSpacing"]["value"])}{v["letterSpacing"]["unit"]}',
            "paragraphSpacing": _ref(fig.get("paragraphSpacing"), amap),
            "textDecoration": fig.get("textDecoration", "none"), "textCase": fig.get("textCase", "original"),
        }
    if t == "shadow":
        return {"kind": "effect", "description": d, "layers": [
            {"color": _ref(l["color"], amap) if isinstance(l["color"], str) else _color(l["color"]),
             "x": l["offsetX"]["value"], "y": l["offsetY"]["value"], "blur": l["blur"]["value"], "spread": l["spread"]["value"]}
            for l in v]}
    if t == "grid":
        cw = v.get("columnWidth")
        return {"kind": "grid", "description": d, "columns": v["columns"],
                "columnWidth": cw["value"] if isinstance(cw, dict) else cw,
                "gutter": _ref(v.get("gutter"), amap), "margin": _ref(v.get("margin"), amap),
                "alignment": v["alignment"]}
    modes = {m: _val(t, mv, amap, {}) for m, mv in (fig.get("modes") or {}).items()}
    return {"kind": "var", "type": t, "description": d, "value": _val(t, v, amap, fig), "modes": modes,
            "scopes": sorted(fig.get("scopes") or []), "hidden": bool(fig.get("hiddenFromPublishing"))}


def projections(tokens, cfg=None):
    nodes = token_nodes(tokens)
    amap = alias_map(nodes)
    return {n: project(n, e, amap) for n, e in nodes.items() if not (cfg and excluded(n, cfg))}


def rename_refs(proj, renames):
    """A projection with its @references renamed: {old: new}."""
    if isinstance(proj, dict):
        return {k: rename_refs(v, renames) for k, v in proj.items()}
    if isinstance(proj, list):
        return [rename_refs(v, renames) for v in proj]
    if isinstance(proj, str) and proj.startswith("@"):
        for old, new in renames.items():
            if proj == "@" + old or proj.startswith("@" + old + "/"):
                return "@" + new + proj[len(old) + 1:]
    return proj


def rename_in_bindings(components, renames):
    """Component bindings name tokens and styles; make them follow renames ({old: new}). In place."""
    for e in components.values():
        for b in (e.get("bindings") or {}).values():
            for k, v in b.items():
                if isinstance(v, str) and any(part in renames for part in v.split("+")):
                    b[k] = "+".join(renames.get(part, part) for part in v.split("+"))


# ── contrast ─────────────────────────────────────────────────────────

PAGE = "surfaces/primary"     # what a component sits on when nothing in it has a fill


def _rgb(name, proj, seen=()):
    """(r, g, b, alpha) for a color token, following aliases in projection form."""
    if name in seen or name not in proj:
        return None
    v = proj[name].get("value")
    if not isinstance(v, str):
        return None
    alpha = 1.0
    if v.startswith("@"):
        ref, _, a = v[1:].rpartition("/") if re.fullmatch(r".*/[0-9.]+", v) else (v[1:], "", "")
        base = _rgb(ref, proj, seen + (name,))
        if base is None:
            return None
        return base[:3] + (base[3] * (float(a) if a else 1.0),)
    h, _, a = v.partition("/")
    if a:
        alpha = float(a)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)) + (alpha,)


def _over(top, under):
    a = top[3]
    return tuple(top[i] * a + under[i] * (1 - a) for i in range(3)) + (1.0,)


def _lum(c):
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c[:3]]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast_ratio(a, b):
    hi, lo = sorted([_lum(a), _lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _num(ref, proj, pick):
    """Smallest (pick=min) or largest value of a number token across its modes."""
    if not isinstance(ref, str) or not ref.startswith("@"):
        return ref
    p = proj.get(ref[1:]) or {}
    vals = [p.get("value")] + list((p.get("modes") or {}).values())
    vals = [v for v in vals if isinstance(v, (int, float))]
    return pick(vals) if vals else None


def _large_text(style, proj):
    """WCAG large text: 24px and up, or 18.66px and up when bold. Uses the smallest mode."""
    p = proj.get(style) or {}
    size, weight = _num(p.get("fontSize"), proj, min), _num(p.get("fontWeight"), proj, min)
    if size is None:
        return False
    return size >= 24 or (size >= 18.66 and (weight or 0) >= 700)


def _variant_props(key):
    return dict(x.split("=", 1) for x in key.split(", ") if "=" in x)


def _find_variant(entry, props):
    for v in entry.get("bindings", {}):
        have = _variant_props(v)
        if all(have.get(k) == x for k, x in props.items()):
            return v
    return None


def contrast_pairs(components, proj):
    """Every text and icon color in every variant, against the color under it.

    The color under a layer is the nearest fill above it (a fill with opacity is blended over
    what's beneath it), else the page surface. A nested component is checked against the color
    under it in its parent. Disabled variants and content/disabled are exempt, as WCAG allows.
    A layer hidden in this variant but shown in another is skipped; one hidden in every variant is
    switched on by a property, so it's checked.
    Yields dicts: fg, bg (token names, bg may be "a over b"), kind, need, ratio, where."""
    out = []

    def walk(comp, variant, under, under_label, trail, depth):
        entry = components.get(comp)
        if not entry or depth > 4:
            return
        b = entry["bindings"].get(variant, {})
        shown_elsewhere = {k.rpartition(".")[0] for bb in entry["bindings"].values() for k in bb
                           if k.endswith(".color") and not bb.get(k.rpartition(".")[0] + ".hidden")}
        layers = {}
        for k, v in b.items():
            path, _, field = k.rpartition(".")
            layers.setdefault(path, {})[field] = v
        def backdrop(path):
            parts = [] if path == "self" else path.split("/")
            chain = ["/".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)] + ["self"]
            fills = [(p, layers[p]["fill"]) for p in chain if isinstance(layers.get(p, {}).get("fill"), str)]
            color, names = under, [under_label]
            for _, f in reversed(fills):           # outermost first
                c = _rgb(f, proj)
                if c is None:
                    continue
                if c[3] >= 0.999:
                    color, names = c, [f"`{f}`"]
                elif c[3] > 0.02:                  # near-invisible fills (like a focus variant's) don't change the color
                    color, names = _over(c, color), [f"`{f}`"] + names
            return color, " over ".join(names)
        disabled = "disabled" in variant.lower()
        here = trail + [f"{comp} / {variant}" if variant != comp else comp]
        for path, f in layers.items():
            if "component" in f and isinstance(f["component"], str):
                name, _, js = f["component"].partition(" ")
                try:
                    props = json.loads(js) if js else {}
                except ValueError:
                    props = {}
                sub = components.get(name)
                v = sub and _find_variant(sub, props)
                if v:
                    c, n = backdrop(path)
                    walk(name, v, c, n, here, depth + 1)
                continue
            fg = f.get("color")
            if not isinstance(fg, str) or disabled or fg == "content/disabled":
                continue
            if f.get("hidden") and path in shown_elsewhere:
                continue                    # hidden by this variant's state (an unchecked check), not by a property
            c = _rgb(fg, proj)
            if c is None:
                continue
            bgc, bgn = backdrop(path)
            if c[3] < 0.999:
                c = _over(c, bgc)
            if "text-style" in f:
                kind, need = "text", (3.0 if _large_text(f["text-style"], proj) else 4.5)
            elif "icon" in f:
                kind, need = "icon", 3.0
            else:
                continue
            out.append({"fg": fg, "bg": bgn, "kind": kind, "need": need,
                        "ratio": contrast_ratio(c, bgc), "where": " › ".join(here + [path]), "hidden": bool(f.get("hidden"))})

    page = _rgb(PAGE, proj) or (1.0, 1.0, 1.0, 1.0)
    for comp, entry in components.items():
        if comp.startswith("."):
            continue                    # private parts are checked inside the components that use them
        for variant in entry.get("bindings", {}):
            walk(comp, variant, page, f"`{PAGE}`", [], 0)
    return out


def pair_ratio(fg, bg, proj):
    """Contrast of two color tokens, blending any opacity over the page surface."""
    page = _rgb(PAGE, proj) or (1.0, 1.0, 1.0, 1.0)
    a, b = _rgb(fg, proj), _rgb(bg, proj)
    if a is None or b is None:
        return None
    if b[3] < 0.999:
        b = _over(b, page)
    if a[3] < 0.999:
        a = _over(a, b)
    return contrast_ratio(a, b)


FIGURE = re.compile(r"(\d+\.\d\d):1\b")     # measured figures; "needs 3:1" is a requirement, not a figure
COMPARE = re.compile(r"\b(match\w*|same|equal\w*|identical|larger|smaller|bigger|twice|half|than)\b", re.I)


def resolved(name, proj, mode=None, seen=()):
    """A non-color token's value, following aliases; a mode's value where it has one."""
    p = proj.get(name)
    if p is None or name in seen or p.get("kind") != "var":
        return None
    v = (p.get("modes") or {}).get(mode, p.get("value")) if mode else p.get("value")
    if isinstance(v, str) and v.startswith("@"):
        return resolved(v[1:], proj, mode, seen + (name,))
    return v


def _values(name, proj):
    """{"": default, mode: value, ...} for a non-color token."""
    p = proj.get(name) or {}
    out = {"": norm(resolved(name, proj))}
    for m in p.get("modes") or {}:
        out[m] = norm(resolved(name, proj, m))
    return out


def changed_values(old, new):
    """{token: ("old → new" per mode, {old values})} for non-color variables whose resolved value moved."""
    out = {}
    for n in set(old) & set(new):
        if old[n].get("kind") != "var" or old[n].get("type") == "color":
            continue
        a, b = _values(n, old), _values(n, new)
        # a mode that's new and only copies the default value says nothing new about the token
        diffs = [f"{m or 'default'} {a.get(m)} → {b.get(m)}" for m in dict.fromkeys(list(a) + list(b))
                 if a.get(m) != b.get(m) and not (m and m not in a and b.get(m) == b.get(""))]
        if diffs:
            out[n] = (", ".join(diffs), {float(v) for v in a.values() if isinstance(v, (int, float))})
    return out


def _mentioned(word, names):
    """Tokens a written name refers to: the full name, or a name ending in /word (`text-m` → font-size/text-m …)."""
    return [n for n in names if n == word or n.endswith("/" + word)]


def value_mentions(line, moved):
    """Reasons a hand-written line may be wrong because a non-color token it names changed value.
    Only lines that compare it ("matching `text-m`") or write one of its old values are flagged."""
    if not moved:
        return []
    bare = re.sub(r"`[^`]*`|/\*.*?\*/", "", line)
    compares = bool(COMPARE.search(bare))
    numbers = {float(x) for x in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)", bare)}
    words = re.findall(r"`([^`\n]+)`", line) + re.findall(r"/\*\s*([\w/.-]+)\s*\*/", line)
    out = []
    for w in dict.fromkeys(words):
        hits = [n for n in _mentioned(w, moved) if compares or numbers & moved[n][1]]
        if len(hits) == 1 and hits[0] == w:
            out.append(f"`{w}` changed ({moved[w][0]}); check what the line says about it")
        elif hits:
            out.append(f"`{w}` changed ({'; '.join(f'{n}: {moved[n][0]}' for n in sorted(hits))}); check what the line says about it")
    return out


def stale_text(texts, proj):
    """Hand-written lines that disagree with tokens.json as it stands, whatever changed it.

    Two kinds, checked on every run: a contrast figure written beside the two colors it measures
    (`a` on `b` (5.64:1), or a table row naming both), and a value written beside a token's name
    (1024px … /* breakpoint-md */, or `space-m` (24px)). A figure is only checked when its pairing
    is clear from the text, so a vague line is skipped rather than flagged.
    Returns {rel: [(line number, line, reason)]}."""
    colors = [n for n, p in proj.items() if p.get("type") == "color"]
    numeric = [n for n, p in proj.items() if p.get("kind") == "var" and p.get("type") != "color"]
    out = {}
    for rel, text in texts.items():
        found = []
        for i, line in hand_written_lines(text):
            reasons = []
            # contrast figures
            prev, start = None, 0
            for m in FIGURE.finditer(line):
                seg = line[start:m.start()]
                start = m.end()
                toks = [t for t in re.findall(r"`([^`\n]+)`", seg) if t in colors]
                if len(toks) >= 2:
                    cands = [(toks[-2], toks[-1])]
                elif len(toks) == 1 and prev:
                    cands = [(prev[0], toks[0]), (toks[0], prev[1])]
                else:
                    if not toks:
                        prev = None
                    continue
                ratios = [(a, b, pair_ratio(a, b, proj)) for a, b in cands]
                ratios = [x for x in ratios if x[2] is not None]
                hit = next((x for x in ratios if f"{x[2]:.2f}" == m.group(1)), None)
                if hit:
                    prev = hit[:2]
                elif len(cands) == 1 and ratios:
                    a, b, r = ratios[0]
                    reasons.append(f"says {m.group(1)}:1 for `{a}` on `{b}`, which is {r:.2f}:1")
                    prev = (a, b)
                else:
                    prev = None
            # values written beside a token name
            for m in re.finditer(r"(\d+(?:\.\d+)?)(px|ms)?\b[^\n]{0,40}?/\*\s*([\w/.-]+)\s*\*/"
                                 r"|`([\w/.-]+)`\s*\((\d+(?:\.\d+)?)(px|ms)?\)", line):
                num, name = (m.group(1), m.group(3)) if m.group(3) else (m.group(5), m.group(4))
                for n in _mentioned(name, numeric)[:1]:
                    vals = {v for v in _values(n, proj).values() if isinstance(v, (int, float))}
                    if vals and float(num) not in {float(v) for v in vals}:
                        reasons.append(f"writes {num} beside `{name}`, which is {' / '.join(f'{v:g}' for v in sorted(vals))}")
            if reasons:
                found.append((i, line.strip(), "; ".join(reasons)))
        if found:
            out[rel] = found
    return out


def hand_written_lines(text):
    """(line number, line) for every line outside the frontmatter and generated sections."""
    out, in_gen, in_fm = [], False, text.startswith("---\n")
    for i, line in enumerate(text.splitlines(), 1):
        if in_fm:
            if i > 1 and line == "---":
                in_fm = False
            continue
        if line.startswith("<!-- sync:begin"):
            in_gen = True
        elif line.startswith("<!-- sync:end"):
            in_gen = False
        elif not in_gen:
            out.append((i, line))
    return out


def prose_review(texts, comp_files, old, new):
    """Hand-written lines a token or binding change may have made wrong.

    texts: {rel: file text}. comp_files: {component: rel}. old and new: {"proj": token projections,
    "pairs": contrast_pairs(...) results}. Returns {rel: [(line number, line, reason)]}.
    Contrast figures are recomputed from the pairings the line names, and from the line's component's
    own pairings for the colors it names, so "on every tint, 11.05:1 or higher" is covered too."""
    colors = {n for P in (old["proj"], new["proj"]) for n, p in P.items() if p.get("type") == "color"}
    file_pairs = {}
    for key, P in (("old", old["pairs"]), ("new", new["pairs"])):
        for x in P:
            rel = comp_files.get(x["where"].split(" › ")[0].split(" / ")[0])
            bgs = re.findall(r"`([^`]+)`", x["bg"])
            if rel and len(bgs) == 1:
                file_pairs.setdefault(rel, {}).setdefault(key, set()).add((x["fg"], bgs[0]))
    moved_vals = changed_values(old["proj"], new["proj"])
    out = {}
    for rel, text in texts.items():
        fp_old = file_pairs.get(rel, {}).get("old", set())
        fp_new = file_pairs.get(rel, {}).get("new", set())
        found = []
        for i, line in hand_written_lines(text):
            explicit, wild = set(), set()
            for m in re.finditer(r"`([^`\n]+)`", line):
                t = m.group(1)
                if t.endswith("/*"):
                    wild |= {c for c in colors if c.startswith(t[:-1])}
                elif t in colors:
                    explicit.add(t)
            named = explicit | wild
            figures = FIGURE.findall(line)
            reasons = []
            if figures and named:
                # the component's own pairings last, so their fg/bg order wins
                cands = [(a, b) for a in explicit for b in explicit if a != b]
                cands += sorted((a, b) for a, b in fp_old | fp_new if a in named or b in named)
                moved = {}          # old figure -> every pairing that had it and has moved
                for a, b in dict.fromkeys(cands):
                    r0, r1 = pair_ratio(a, b, old["proj"]), pair_ratio(a, b, new["proj"])
                    if r0 is not None and r1 is not None and f"{r0:.2f}" != f"{r1:.2f}":
                        moved.setdefault(f"{r0:.2f}", []).append((a, b, f"{r1:.2f}"))
                # each written figure gets its own pairing, so two pairings that shared a figure
                # (7.51:1 twice in one line) are both reported. Prefer pairings written in the line,
                # foreground first, used by the component, and nearest the figure; never reuse one.
                taken = set()
                for m in FIGURE.finditer(line):
                    f, at = m.group(1), m.start()
                    best = None
                    for a, b, r1 in moved.get(f"{float(f):.2f}", []):
                        if (a, b) in taken:
                            continue
                        ia = line.rfind(f"`{a}`", 0, at)
                        ib = line.rfind(f"`{b}`", 0, at)
                        near = 1 / (1 + at - ib) if ib >= 0 else 0
                        near += 1 / (1 + ib - ia) if 0 <= ia < ib else 0
                        score = (ia >= 0) + (ib >= 0) + (0 <= ia < ib) + ((a, b) in fp_old | fp_new) + near
                        if best is None or score > best[0]:
                            best = (score, a, b, r1)
                    if best:
                        _, a, b, r1 = best
                        taken.add((a, b))
                        reasons.append(f"{f}:1 is now {r1}:1 (`{a}` on `{b}`)")
                changed = {n for n in explicit if _rgb(n, old["proj"]) != _rgb(n, new["proj"])}
                if not reasons and changed:
                    reasons.append("quotes contrast for " + ", ".join(f"`{n}`" for n in sorted(changed)) + ", whose color changed")
            gone = {(a, b) for a, b in fp_old - fp_new if a in named and b in named}
            for a, b in sorted(gone):
                reasons.append(f"`{a}` on `{b}` no longer occurs in this component")
            reasons += value_mentions(line, moved_vals)
            if reasons:
                found.append((i, line.strip(), "; ".join(dict.fromkeys(reasons))))
        # pairings this component newly uses that the file never mentions
        for a, b in sorted(fp_new - fp_old):
            if not re.search(rf"`{re.escape(a)}`[^\n]*`{re.escape(b)}`|`{re.escape(b)}`[^\n]*`{re.escape(a)}`", text):
                r = pair_ratio(a, b, new["proj"])
                found.append((0, "", f"new pairing `{a}` on `{b}` ({r:.2f}:1) isn't mentioned; add it where the file lists contrast" if r else
                              f"new pairing `{a}` on `{b}` isn't mentioned"))
        if found:
            out[rel] = found
    return out


# ── design.md, from Figma's Introduction page ────────────────────────

DESIGN_FILE = "design.md"
DESIGN_HEAD = """# Design

What this system is for and how its screens should feel. The foundation and component files say how to build; this file says what a good result looks like for this client. Read it before designing with the system, and treat a section marked "Not written yet" as undecided: ask, don't invent.

The sections below come from the Introduction page in Figma. Edit them there; /ds-sync rewrites this block from it.

"""


def _blank(body):
    return body.strip().upper().startswith("TODO")


def render_intro(intro):
    """The generated block's body: one ## section per Figma section, in page order.
    A body still starting "TODO" is unwritten, so it shows the page's prompt. Several lines become a list."""
    out = []
    for s in intro["sections"]:
        body = s["body"].strip()
        out.append(f"## {s['heading'].strip()}\n")
        if _blank(body):
            prompt = re.sub(r"^TODO:?\s*", "", body, flags=re.I)
            out.append(f"*Not written yet.* The Introduction page asks for: {prompt}\n")
            continue
        for para in re.split(r"\n\s*\n", body):
            lines = [l.strip() for l in para.splitlines() if l.strip()]
            if len(lines) > 1:
                out.append("\n".join("- " + re.sub(r"^[-•*]\s*", "", l) for l in lines) + "\n")
            elif lines:
                out.append(lines[0] + "\n")
    return "\n".join(out)


def write_design(root, intro):
    """design.md with its block rewritten from the Introduction page; the hand-written head is kept."""
    path = root / DESIGN_FILE
    block = f"<!-- sync:begin introduction source=figma -->\n{render_intro(intro)}<!-- sync:end introduction -->\n"
    text = path.read_text() if path.exists() else DESIGN_HEAD
    pat = re.compile(r"<!-- sync:begin introduction [^>]*-->\n.*?<!-- sync:end introduction -->\n?", re.S)
    text = pat.sub(lambda m: block, text, count=1) if pat.search(text) else text.rstrip() + "\n\n" + block
    path.write_text(text)
    return text


def design_block(text):
    m = re.search(r"<!-- sync:begin introduction [^>]*-->\n(.*?)<!-- sync:end introduction -->", text, re.S)
    return m.group(1) if m else None


def intro_diff(base, now):
    """What changed on the Introduction page since the snapshot, one line per section."""
    old = {s["key"]: s for s in (base or {}).get("sections", [])}
    new = {s["key"]: s for s in now.get("sections", [])}
    lines = []
    for k, s in new.items():
        o = old.get(k)
        name = s["heading"].strip()
        if o is None:
            lines.append(f"{name}: new section" + (", not written yet" if _blank(s["body"]) else ""))
            continue
        if o["heading"].strip() != name:
            lines.append(f"{o['heading'].strip()}: renamed to {name}")
        if o["body"].strip() != s["body"].strip():
            was, isnow = _blank(o["body"]), _blank(s["body"])
            lines.append(f"{name}: " + ("written" if was and not isnow else "cleared" if isnow and not was
                                        else "prompt edited" if was else "edited"))
    for k, o in old.items():
        if k not in new:
            lines.append(f"{o['heading'].strip()}: section removed")
    if [k for k in new if k in old] != [k for k in old if k in new]:
        lines.append("sections reordered")
    return lines


# ── components ───────────────────────────────────────────────────────

def component_parts(entry):
    """The fingerprinted parts of a snapshot component: description, properties, default variant, one per variant."""
    parts = {
        "description": entry.get("description") or "",
        "properties": entry.get("properties") or {},
        "default_variant": entry.get("default_variant"),
    }
    for v, b in (entry.get("bindings") or {}).items():
        parts["variant:" + v] = b
    return parts


def snapshot_projections(snap_tokens, cfg=None):
    """Projections from the snapshot's stored token entries (same shape as projections(tokens.json))."""
    amap = {"{" + e["path"] + "}": n for n, e in snap_tokens.items()}
    return {n: project(n, (e["group"], e["path"], e["node"]), amap)
            for n, e in snap_tokens.items() if not (cfg and excluded(n, cfg))}


def find_root(start=None):
    """The design-system folder: the one holding design-system.config.yaml."""
    p = Path(start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "design-system.config.yaml").exists():
            return cand
        if (cand / "design-system" / "design-system.config.yaml").exists():
            return cand / "design-system"
    raise SystemExit("No design-system.config.yaml found here or above. Run from the project, or pass --root.")


# ── projection → tokens.json entry (the writer's direction) ──────────

STYLE_KIND_GROUP = {"text": "text-styles", "effect": "effect-styles", "grid": "grid-styles"}
STYLE_COLLECTION_GROUP = {"Text styles": "text-styles", "Effect styles": "effect-styles", "Grid styles": "grid-styles"}
WEIGHT_NAME = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "Semibold", 700: "Bold", 800: "ExtraBold", 900: "Black"}


def collection_groups(tokens):
    """Figma collection name → tokens.json group, read from each group's $extensions."""
    out = dict(STYLE_COLLECTION_GROUP)
    for g, o in tokens.items():
        col = (o.get("$extensions", {}) if isinstance(o, dict) else {}).get("figma", {}).get("collection")
        if col:
            out[col] = g
    return out


def new_path(name, group, kind):
    parts = name.split("/")
    if kind in STYLE_KIND_GROUP:
        parts = [p.lower().replace(" ", "-") for p in parts]
    return ".".join([group] + parts)


def _hex_color(s):
    h, _, a = s.partition("/")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = {"colorSpace": "srgb", "components": [norm(round(x, 4)) for x in rgb]}
    if a and float(a) < 1:
        c["alpha"] = norm(float(a))
    c["hex"] = h.lower()
    return c


def _split_opacity(ref):
    """'@neutral/black/0.12' → ('neutral/black', 0.12); a plain alias → (name, None)."""
    body = ref[1:]
    head, _, tail = body.rpartition("/")
    try:
        return head, float(tail)
    except ValueError:
        return body, None


class Converter:
    """Turns projections back into tokens.json entries, using the current tokens.json for names and resolution."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.nodes = token_nodes(tokens)          # name → (group, path, node)
        self.name2path = {n: p for n, (_, p, _) in self.nodes.items()}

    def ref(self, name):
        if name not in self.name2path:
            raise KeyError(f"no token named {name}")
        return "{" + self.name2path[name] + "}"

    def resolve_color(self, name):
        node = self.nodes[name][2]
        v = node["$value"]
        seen = 0
        while isinstance(v, str) and seen < 20:
            v = self.nodes[_alias_name(v, self.name2path)][2]["$value"]
            seen += 1
        return v

    def val(self, t, x):
        """Projection value → $value (and any $extensions it implies)."""
        ext = {}
        if isinstance(x, str) and x.startswith("@"):
            if t == "color":
                base, op = _split_opacity(x)
                if op is not None and base in self.name2path:
                    c = self.resolve_color(base)
                    out = {"colorSpace": "srgb", "components": c["components"], "alpha": norm(op), "hex": c["hex"]}
                    return out, {"colorFrom": self.ref(base), "opacityPercent": norm(op * 100)}
            return self.ref(x[1:]), ext
        if t == "color":
            return _hex_color(x), ext
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            x = norm(x)             # Figma stores 32-bit floats: 1.2 comes back as 1.2000000476837158
        if t in ("dimension",):
            return {"value": x, "unit": "px"}, ext
        if t == "duration":
            return {"value": x, "unit": "ms"}, ext
        return x, ext

    def node(self, name, p, meta=None, existing=None):
        """Build the full tokens.json entry for projection p. Existing entries keep any extension keys this doesn't manage."""
        meta = meta or {}
        old_ext = (existing or {}).get("$extensions", {}).get("figma", {})
        fid = meta.get("id") or old_ext.get("id")
        kind = p["kind"]
        if kind == "var":
            t = p["type"]
            value, vext = self.val(t, p["value"])
            old_scopes = old_ext.get("scopes")
            scopes = old_scopes if old_scopes is not None and sorted(old_scopes) == list(p["scopes"]) else list(p["scopes"])
            ext = {"id": fid, "scopes": scopes}
            if p.get("hidden"):
                ext["hiddenFromPublishing"] = True
            if t == "fontWeight":
                keep = old_ext.get("fontStyle")
                same = existing and norm(existing.get("$value")) == norm(p["value"])
                ext["fontStyle"] = keep if (keep and same) else WEIGHT_NAME.get(p["value"], str(p["value"]))
            ext.update(vext)
            if t == "cubicBezier":
                ext["easingType"] = old_ext.get("easingType", "CUSTOM_CUBIC_BEZIER")
            if p.get("modes"):
                ext["modes"] = {m: self.val(t, mv)[0] for m, mv in p["modes"].items()}
            out = {"$type": t, "$value": value}
        elif kind == "text":
            def dim(x):
                return self.ref(x[1:]) if isinstance(x, str) and x.startswith("@") else ({"value": x, "unit": "px"} if isinstance(x, (int, float)) else x)
            ls = re.match(r"(-?[\d.]+)(%|px)", p["letterSpacing"])
            value = {"fontFamily": dim(p["fontFamily"]), "fontWeight": dim(p["fontWeight"]), "fontSize": dim(p["fontSize"]),
                     "lineHeight": dim(p["lineHeight"]), "letterSpacing": {"value": norm(float(ls.group(1))), "unit": ls.group(2)}}
            ext = {"id": fid, "styleName": name, "paragraphSpacing": dim(p["paragraphSpacing"])}
            if p["textDecoration"] != "none":
                ext["textDecoration"] = p["textDecoration"]
            if p["textCase"] != "original":
                ext["textCase"] = p["textCase"]
            out = {"$type": "typography", "$value": value}
        elif kind == "effect":
            def color(x):
                return self.ref(x[1:]) if x.startswith("@") else _hex_color(x)
            px = lambda v: {"value": v, "unit": "px"}
            value = [{"color": color(l["color"]), "offsetX": px(l["x"]), "offsetY": px(l["y"]), "blur": px(l["blur"]), "spread": px(l["spread"])} for l in p["layers"]]
            ext = {"id": fid, "styleName": name}
            out = {"$type": "shadow", "$value": value}
        else:  # grid
            def dimn(x):
                if x is None:
                    return None
                return self.ref(x[1:]) if isinstance(x, str) and x.startswith("@") else {"value": x, "unit": "px"}
            value = {"columns": p["columns"], "columnWidth": dimn(p["columnWidth"]), "gutter": dimn(p["gutter"]),
                     "margin": dimn(p["margin"]), "alignment": p["alignment"]}
            ext = {"id": fid, "styleName": name, "layoutGrids": old_ext.get("layoutGrids", 1)}
            out = {"$type": "grid", "$value": value}
        if p.get("description"):
            out["$description"] = p["description"]
        for k, v in old_ext.items():          # keep anything this converter doesn't manage
            ext.setdefault(k, v)
        out["$extensions"] = {"figma": ext}
        return out


def stale_derived(tokens):
    """Colors stored as another color at an opacity (`colorFrom`, such as shadows/*) whose resolved
    $value no longer matches their source. Their projection compares only the link, so a change to the
    source leaves the stored value behind unless something rewrites it. Returns [(name, source, had, now)]."""
    conv = Converter(tokens)
    out = []
    for name, (_, _, node) in conv.nodes.items():
        src = ((node.get("$extensions") or {}).get("figma") or {}).get("colorFrom")
        if not src or node.get("$type") != "color" or not isinstance(node.get("$value"), dict):
            continue
        base = _alias_name(src, conv.name2path)
        c = conv.resolve_color(base)
        v = node["$value"]
        if str(v.get("hex", "")).lower() != str(c["hex"]).lower() or norm(v.get("components")) != norm(c["components"]):
            out.append((name, base, v.get("hex"), c["hex"]))
    return out


def refresh_derived(tokens):
    """Rewrite each stale `colorFrom` color's $value from its source, keeping its own alpha. Returns the names."""
    conv = Converter(tokens)
    fixed = []
    for name, base, _, _ in stale_derived(tokens):
        c = conv.resolve_color(base)
        v = conv.nodes[name][2]["$value"]
        v["components"], v["hex"] = c["components"], c["hex"]
        fixed.append(name)
    return fixed


def _alias_name(ref, name2path):
    path = ref.strip("{}")
    for n, p in name2path.items():
        if p == path:
            return n
    raise KeyError(ref)


def sync_group_modes(tokens):
    """Add to each collection group's `modes` list any mode its tokens now carry (a mode added in Figma).
    Returns the groups changed. render.py and the docs read the group list, not the tokens."""
    changed = []
    for g, body in tokens.items():
        ext = (body.get("$extensions") or {}).get("figma") if isinstance(body, dict) else None
        if not ext or "modes" not in ext:
            continue
        seen = []
        for name, (grp, _, node) in token_nodes(tokens).items():
            if grp == g:
                seen += [m for m in ((node.get("$extensions") or {}).get("figma") or {}).get("modes", {}) if m not in seen]
        new = [m for m in seen if m not in ext["modes"]]
        if new:
            ext["modes"] = ext["modes"] + new
            changed.append(g)
    return changed


def set_at(tokens, dotted, node):
    parts = dotted.split(".")
    o = tokens
    for p in parts[:-1]:
        o = o.setdefault(p, {})
    o[parts[-1]] = node


def delete_at(tokens, dotted):
    parts = dotted.split(".")
    chain = [tokens]
    for p in parts[:-1]:
        chain.append(chain[-1][p])
    del chain[-1][parts[-1]]
    for i in range(len(parts) - 2, 0, -1):     # drop groups left empty (but never top-level groups)
        if not [k for k in chain[i] if not k.startswith("$")]:
            del chain[i - 1][parts[i - 1]]
        else:
            break
