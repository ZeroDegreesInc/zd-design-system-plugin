"""Write an approved token proposal into Figma (or, when asked, the markdown first), for /port-tokens.

  python3 -I port.py plan [--root DIR] [--from FILE] [--to markdown]
      Checks the proposal and shows every change, old → new, plus what follows from it. Writes nothing.
  python3 -I port.py prepare [--root DIR] [--from FILE] [--to markdown]
      Default: writes .sync/port.js. Run it with use_figma.
      --to markdown: writes tokens.json and regenerates the foundation sections instead.
  python3 -I port.py finish [--root DIR]
      (figma only) Finds the result of .sync/port.js and records what Figma accepted.

The proposal defaults to ROOT/intake/proposed-tokens.json. Port changes values, and adds tokens and
styles listed under `$additions`, set up like their nearest existing neighbour. It never touches the
snapshot: /ds-sync carries the change from where it landed to the other places.
"""
import argparse
import copy
import datetime
import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC = HERE.parents[1] / "ds-sync" / "scripts"     # port shares /ds-sync's projections and Figma writer
sys.path.insert(0, str(SYNC))
import synclib as L
import check as C
from figma_read import find_result

WEIGHT_NAME = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
               600: "Semibold", 700: "Bold", 800: "ExtraBold", 900: "Black"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ── reading the proposal ─────────────────────────────────────────────

def proposal_leaves(prop):
    """(dotted path, entry) for every token in the proposal."""
    out = []

    def walk(o, path):
        for k, v in o.items():
            if k.startswith("$") or not isinstance(v, dict):
                continue
            if "$value" in v:
                out.append((".".join(path + [k]), v))
            else:
                walk(v, path + [k])
    walk(prop, [])
    return out


def normalise(t, v, path):
    """The proposal's value in tokens.json's shape. Accepts hex strings for colors and plain numbers."""
    if isinstance(v, str) and v.startswith("{"):
        return v
    if t == "color":
        if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            h = v.lower()
            return {"colorSpace": "srgb", "components": [round(int(h[i:i + 2], 16) / 255, 4) for i in (1, 3, 5)], "hex": h}
        if isinstance(v, dict) and re.fullmatch(r"#[0-9a-fA-F]{6}", str(v.get("hex", ""))):
            return v
        raise ValueError(f"`{path}`: a color is a hex like \"#5b2bd5\" or a reference like \"{{primitive-color.brand.brand-300}}\".")
    if t in ("dimension", "duration"):
        if isinstance(v, (int, float)):
            return {"value": v, "unit": "ms" if t == "duration" else "px"}
        if isinstance(v, dict) and isinstance(v.get("value"), (int, float)):
            return v
        raise ValueError(f"`{path}`: a {t} is a number or {{\"value\": n, \"unit\": \"…\"}}.")
    if t == "fontWeight":
        if v in WEIGHT_NAME:
            return v
        raise ValueError(f"`{path}`: a font weight is one of {', '.join(str(w) for w in WEIGHT_NAME)}.")
    if t == "fontFamily":
        if isinstance(v, str) and v.strip():
            return v.strip()
        raise ValueError(f"`{path}`: a font family is its name, like \"Lato\".")
    if t == "number" and isinstance(v, (int, float)):
        return v
    if t == "cubicBezier" and isinstance(v, list) and len(v) == 4:
        return v
    if isinstance(v, type(None)):
        raise ValueError(f"`{path}`: no value.")
    return v


STYLE_GROUPS = {"text-styles": "text", "effect-styles": "effect", "grid-styles": "grid"}
KEBAB = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def refs_in(v):
    """Every {reference} inside a value, however deeply nested."""
    if isinstance(v, str):
        return [v.strip("{}")] if v.startswith("{") else []
    if isinstance(v, dict):
        return [r for x in v.values() for r in refs_in(x)]
    if isinstance(v, list):
        return [r for x in v for r in refs_in(x)]
    return []


def neighbour(path, candidates):
    """The existing token an addition should copy its settings from: the longest shared path,
    then the same depth, then the same step suffix (brand-100 for accent-100)."""
    segs, best, score = path.split("."), None, None
    for name, (_, p, node) in candidates.items():
        ps = p.split(".")
        common = 0
        for a, b in zip(segs, ps):
            if a != b:
                break
            common += 1
        s = (common, len(ps) == len(segs), segs[-1].rsplit("-", 1)[-1] == ps[-1].rsplit("-", 1)[-1])
        if score is None or s > score:
            best, score = name, s
    return best, score


def add_token(tokens, nodes, path, entry, errors, notes):
    """Put one addition into tokens (in place), shaped like its nearest neighbour.
    Returns (name, group, template) or None."""
    segs = path.split(".")
    top = segs[0]
    if top not in tokens or top.startswith("$"):
        errors.append(f"`{path}`: there's no `{top}` group. Port adds tokens to the existing collections and style groups; "
                      "a new collection is a structural change to make by hand.")
        return None
    in_group = {n: e for n, e in nodes.items() if e[0] == top}
    kind = STYLE_GROUPS.get(top, "var")
    want_type = entry.get("$type")
    cands = {n: e for n, e in in_group.items() if not want_type or e[2]["$type"] == want_type}
    if not cands:
        errors.append(f"`{path}`: nothing in `{top}` is a {want_type}, so there's nothing for it to match.")
        return None
    tname, score = neighbour(path, cands)
    _, tpath, tnode = nodes[tname]
    if not score[1]:
        errors.append(f"`{path}` doesn't match how `{top}` is laid out. Its tokens look like `{tpath}`.")
        return None
    if kind == "var":
        if all(KEBAB.fullmatch(x) for _, p, _ in in_group.values() for x in p.split(".")[1:]):
            bad = [x for x in segs[1:] if not KEBAB.fullmatch(x)]
            if bad:
                errors.append(f"`{path}`: names in `{top}` are lowercase words joined by hyphens; `{bad[0]}` isn't.")
                return None
        tp = tpath.split(".")
        if len(tp) > 2 and tp[-1].startswith(tp[-2] + "-") and not segs[-1].startswith(segs[-2] + "-"):
            errors.append(f"`{path}`: steps in this group repeat their scale's name, like `{tp[-1]}`. "
                          f"Call it `{segs[-2]}-{segs[-1].rsplit('-', 1)[-1]}`.")
            return None
    t = tnode["$type"]
    described = sum(1 for e in in_group.values() if e[2].get("$description"))
    if described >= 0.8 * len(in_group) and not entry.get("$description"):
        errors.append(f"`{path}`: every token in `{top}` explains when to use it. Add a `$description`.")
        return None
    try:
        value = normalise(t, entry["$value"], path) if kind == "var" else entry["$value"]
    except ValueError as e:
        errors.append(str(e))
        return None
    efig = (entry.get("$extensions") or {}).get("figma") or {}
    fig = {k: copy.deepcopy(v) for k, v in tnode.get("$extensions", {}).get("figma", {}).items()
           if k not in ("id", "colorFrom", "opacityPercent", "fontStyle", "modes", "styleName")}
    if t == "fontWeight":
        fig["fontStyle"] = WEIGHT_NAME[value]
    if kind == "var":
        name = "/".join(segs[1:])
        gfig = tokens[top].get("$extensions", {}).get("figma", {})
        others = [m for m in gfig.get("modes") or [] if m != gfig.get("defaultMode")]
        modes_in = efig.get("modes") or {}
        unknown = [m for m in modes_in if m not in others]
        if unknown:
            errors.append(f"`{path}`: its collection has no mode {', '.join(unknown)}.")
            return None
        if others:
            try:
                fig["modes"] = {m: normalise(t, modes_in[m], f"{path} ({m})") if m in modes_in else value for m in others}
            except ValueError as e:
                errors.append(str(e))
                return None
            missing = [m for m in others if m not in modes_in]
            if missing:
                notes.append(f"`{name}`: no {', '.join(missing)} value given, so it uses the default value there too.")
    else:
        name = efig.get("styleName")
        if not name:
            errors.append(f"`{path}`: a new style needs its Figma name in `$extensions.figma.styleName`, like `{tnode['$extensions']['figma']['styleName']}`.")
            return None
        if any(e[2].get("$extensions", {}).get("figma", {}).get("styleName") == name for e in nodes.values()):
            errors.append(f"`{path}`: there's already a style called `{name}`.")
            return None
        fig["styleName"] = name
        for k, v in efig.items():
            if k not in ("styleName", "id"):
                fig[k] = v
        if kind == "text":
            value = {**{"letterSpacing": tnode["$value"].get("letterSpacing")}, **value}
            lacking = [k for k in ("fontFamily", "fontWeight", "fontSize", "lineHeight") if k not in value]
            if lacking:
                errors.append(f"`{path}`: a text style needs {', '.join(lacking)}, as references to typography tokens.")
                return None
    if name in nodes:
        errors.append(f"`{path}`: `{name}` already exists. Put it outside `$additions` to change its value.")
        return None
    node = {"$type": t, "$value": value}
    if entry.get("$description"):
        node["$description"] = entry["$description"]
    node["$extensions"] = {"figma": fig}
    L.set_at(tokens, path, node)
    return name, top, tname


def build(root, prop_path):
    """The proposed tokens.json, what it changes and what it adds. Raises SystemExit on a bad proposal."""
    tokens = json.loads((root / "tokens.json").read_text())
    prop = json.loads(prop_path.read_text())
    nodes = L.token_nodes(tokens)
    by_path = {path: name for name, (_, path, _) in nodes.items()}
    new_tokens = copy.deepcopy(tokens)
    errors, notes, touched, adds = [], [], [], []

    for path, entry in proposal_leaves(prop.get("$additions") or {}):
        if path in by_path:
            errors.append(f"`{path}` already exists. Put it outside `$additions` to change its value.")
            continue
        got = add_token(new_tokens, nodes, path, entry, errors, notes)
        if got:
            adds.append({"name": got[0], "group": got[1], "template": got[2], "path": path})
    known = {p for _, p, _ in L.token_nodes(new_tokens).values()}
    for a in adds:
        for r in refs_in(L.token_nodes(new_tokens)[a["name"]][2]["$value"]):
            if r not in known:
                errors.append(f"`{a['path']}` points to `{{{r}}}`, which isn't a token.")

    new_nodes = L.token_nodes(new_tokens)
    for path, entry in proposal_leaves(prop):
        name = by_path.get(path)
        if not name:
            errors.append(f"`{path}` isn't a token in tokens.json. To add it, put it under `$additions`.")
            continue
        group, _, node = new_nodes[name]
        if group in STYLE_GROUPS:
            errors.append(f"`{path}` is a style. Styles are built from tokens; change the tokens it uses instead.")
            continue
        t = node["$type"]
        if entry.get("$type") and entry["$type"] != t:
            errors.append(f"`{path}` is a {t} token, but the proposal says {entry['$type']}.")
            continue
        extra = [k for k in entry if k not in ("$value", "$type", "$extensions", "$description")]
        if extra or "$description" in entry:
            notes.append(f"`{name}`: only the value is ported; ignored {', '.join(extra + (['$description'] if '$description' in entry else []))}.")
        try:
            value = normalise(t, entry["$value"], path)
            modes_in = ((entry.get("$extensions") or {}).get("figma") or {}).get("modes")
            modes_in = {m: normalise(t, mv, f"{path} ({m})") for m, mv in (modes_in or {}).items()}
        except ValueError as e:
            errors.append(str(e))
            continue
        if isinstance(value, str) and value.startswith("{") and value.strip("{}") not in known:
            errors.append(f"`{path}` points to `{value}`, which isn't a token.")
            continue
        fig = node.setdefault("$extensions", {}).setdefault("figma", {})
        old_value, old_modes = node["$value"], dict(fig.get("modes") or {})
        node["$value"] = value
        fig.pop("colorFrom", None); fig.pop("opacityPercent", None)
        if t == "fontWeight":
            fig["fontStyle"] = WEIGHT_NAME[value]
        if old_modes:
            unknown = [m for m in modes_in if m not in old_modes]
            if unknown:
                errors.append(f"`{path}`: its collection has no mode {', '.join(unknown)}.")
                continue
            new_modes = {}
            for m, mv in old_modes.items():
                if m in modes_in:
                    new_modes[m] = modes_in[m]
                elif L.canon(mv) == L.canon(old_value):
                    new_modes[m] = value             # a mode that matched the default follows it
                else:
                    new_modes[m] = mv
                    notes.append(f"`{name}`: its {m} mode keeps its own value, since the proposal didn't give one.")
            fig["modes"] = new_modes
        elif modes_in:
            errors.append(f"`{path}`: its collection has one mode, so it takes no mode values.")
            continue
        touched.append(name)
    if not errors:
        amap = L.alias_map(L.token_nodes(new_tokens))
        for a in adds:
            try:
                L.project(a["name"], L.token_nodes(new_tokens)[a["name"]], amap)
            except Exception as e:      # a style value in the wrong shape
                errors.append(f"`{a['path']}`: its value isn't in the shape `{a['template']}` uses ({type(e).__name__}).")
    if errors:
        raise SystemExit("The proposal can't be ported yet:\n" + "\n".join(f"- {e}" for e in errors))
    return tokens, new_tokens, touched, adds, notes


# ── accessibility of additions ───────────────────────────────────────

# What each semantic color role is measured against. "x" is the new token. Roles are the first part
# of the name; a system with other roles adds them here. Pairings named in a token's description are
# checked too, so a well-described token is covered whatever its role.
ROLE_PAIRS = {
    "content": [("x", "surfaces/primary", 4.5)],
    "surfaces": [("content/headline", "x", 4.5), ("content/body", "x", 4.5)],
    "actions": [("x", "surfaces/primary", 4.5), ("content/light", "x", 4.5)],
    "borders": [("x", "surfaces/primary", 3.0)],
}
MIN_TEXT_PX = 12


def is_primitive_group(group, nodes):
    vals = [e[2]["$value"] for e in nodes.values() if e[0] == group]
    return sum(1 for v in vals if isinstance(v, str) and v.startswith("{")) < len(vals) / 2


def addition_checks(adds, after, new_tokens):
    """Per addition: lines to show, failures, warnings."""
    lines, fails, warns = [], [], []
    nodes = L.token_nodes(new_tokens)
    colors = {n for n, p in after.items() if p.get("type") == "color"}
    page = L.PAGE
    by_scale = {}
    for a in adds:
        n, p = a["name"], after[a["name"]]
        if p.get("type") == "color":
            if is_primitive_group(a["group"], nodes):
                on_page = L.pair_ratio(n, page, after)
                under = L.pair_ratio("content/light", n, after) if "content/light" in after else None
                uses = [u for u, ok in (("text on the page", on_page >= 4.5), ("icons and borders on the page", 3 <= on_page < 4.5),
                                        ("a fill under `content/light` text", under is not None and under >= 4.5)) if ok]
                lines.append(f"`{n}`: on `{page}` {on_page:.2f}:1" + (f", `content/light` on it {under:.2f}:1" if under else "")
                             + (f". Fine for {', '.join(uses)}." if uses else ". Too light or too mid-tone for text either way; use as a tint or decoration."))
                parent, step = n.rsplit("/", 1) if "/" in n else ("", n)
                num = re.search(r"(\d+)$", step)
                if num:
                    by_scale.setdefault(parent, []).append((int(num.group(1)), n))
            else:
                pairs = [(fg.replace("x", n) if fg == "x" else fg, bg.replace("x", n) if bg == "x" else bg, need)
                         for fg, bg, need in ROLE_PAIRS.get(n.split("/")[0], [])]
                for other in re.findall(r"[a-z]+/[a-z0-9-]+", p.get("description") or ""):
                    if other in colors and other != n and not any(other in (f, b) for f, b, _ in pairs):
                        fg, bg = (n, other) if other.startswith("surfaces/") else (other, n)
                        pairs.append((fg, bg, 4.5 if fg.split("/")[0] in ("content", "actions") else 3.0))
                if not pairs:
                    warns.append(f"`{n}`: no role or named pairing to check its contrast against. Say in its description what it pairs with.")
                for fg, bg, need in pairs:
                    if fg not in after or bg not in after:
                        continue
                    r = L.pair_ratio(fg, bg, after)
                    ok = r >= need
                    lines.append(f"`{fg}` on `{bg}`: {r:.2f}:1, needs {need:g}:1, {'passes' if ok else '**fails**'}.")
                    if not ok:
                        fails.append(f"`{fg}` on `{bg}`: {r:.2f}:1, needs {need:g}:1 (new token `{n}`).")
        elif p.get("type") == "dimension" and n.startswith("font-size/"):
            small = min([p["value"]] + [v for v in p["modes"].values() if isinstance(v, (int, float))])
            if small < MIN_TEXT_PX:
                warns.append(f"`{n}` is {small}px in at least one mode. Text below {MIN_TEXT_PX}px is hard to read.")
        elif p.get("kind") == "text":
            ref = p.get("fontSize")
            size = L._num(ref, after, min) if isinstance(ref, str) else ref
            if isinstance(size, (int, float)) and size < MIN_TEXT_PX:
                warns.append(f"`{n}` sets text at {size}px in at least one mode. Below {MIN_TEXT_PX}px is hard to read.")
    # an addition that repeats a value already in its group is usually a mistake
    for a in adds:
        n, p = a["name"], after[a["name"]]
        if p.get("kind") != "var":
            continue
        mine = L._rgb(n, after) if p.get("type") == "color" else (p.get("value"), L.canon(p.get("modes")))
        for m, (g, _, _) in nodes.items():
            if g != a["group"] or m == n or m not in after or after[m].get("kind") != "var":
                continue
            theirs = L._rgb(m, after) if after[m].get("type") == "color" else (after[m].get("value"), L.canon(after[m].get("modes")))
            if mine is not None and mine == theirs and not (isinstance(p["value"], str) and p["value"] == "@" + m):
                warns.append(f"`{n}` has the same value as `{m}`. Use `{m}`, or give it a value of its own.")
                break
    # a new color scale should run light to dark like the others, with the same steps
    old_scales = {}
    for n, p in after.items():
        if p.get("type") == "color" and "/" in n and not any(a["name"] == n for a in adds):
            m = re.search(r"(\d+)$", n)
            if m:
                old_scales.setdefault(n.rsplit("/", 1)[0], set()).add(int(m.group(1)))
    for parent, steps in by_scale.items():
        steps.sort()
        lums = [L._lum(L._rgb(n, after)) for _, n in steps]
        if any(b >= a for a, b in zip(lums, lums[1:])):
            warns.append(f"`{parent}` doesn't get darker step by step. In this system the lowest number is the lightest.")
        mine = {s for s, _ in steps}
        if parent not in old_scales and old_scales and mine not in old_scales.values():
            usual = sorted(max(old_scales.values(), key=len))
            warns.append(f"`{parent}` has steps {', '.join(map(str, sorted(mine)))}; the other scales use {', '.join(map(str, usual))}.")
    return lines, fails, warns


def resolved_colors(proj):
    out = {}
    for n, p in proj.items():
        if p.get("type") == "color":
            c = L._rgb(n, proj)
            if c:
                out[n] = c
    return out


def show(v):
    if isinstance(v, str) and v.startswith("@"):
        return f"`{v[1:]}`"
    return f"`{v}`"


def required_fonts(proj, tokens):
    """Every family and weight the text styles use, in every typography mode."""
    tfig = tokens.get("typography", {}).get("$extensions", {}).get("figma", {})
    modes = tfig.get("modes") or [None]
    default = tfig.get("defaultMode")

    def at(ref, mode):
        for _ in range(10):
            if not (isinstance(ref, str) and ref.startswith("@")):
                return ref
            p = proj.get(ref[1:]) or {}
            ref = p.get("value") if mode == default or mode not in (p.get("modes") or {}) else p["modes"][mode]
        return ref
    need = set()
    for p in proj.values():
        if p.get("kind") == "text":
            for m in modes:
                fam, w = at(p["fontFamily"], m), at(p["fontWeight"], m)
                if isinstance(fam, str) and isinstance(w, (int, float)):
                    need.add((fam, int(w)))
    return [{"family": f, "weight": w} for f, w in sorted(need)]


def leftovers(root, A, adding):
    """Tokens an earlier port created in Figma that this proposal no longer adds, and that were never synced."""
    made = set()
    for rec in sorted((root / ".sync").glob("port-*.json")):
        made |= set(json.loads(rec.read_text()).get("created") or [])
    return sorted(n for n in made if n not in adding and A["tokens"].get(n, {}).get("figma")
                  and A["tokens"][n]["base"] is None and A["tokens"][n]["figma_new"] is not None)


def plan(root, prop_path, offline=False, to="figma"):
    """to: "figma" (the default) or "markdown", only when someone asks for the markdown first.
    offline: check the proposal against the markdown alone, without a Figma read (for /brand-intake)."""
    cfg = L.load_config(root)
    start = to
    A = C.analyse(root, no_figma=(start != "figma" or offline))
    if A["problems"] and not offline:
        raise SystemExit("Fix the check's problems first (run /ds-sync):\n" + "\n".join(f"- {p}" for p in A["problems"]))
    if start == "figma" and not A["fig"] and not offline:
        raise SystemExit("Port to Figma needs a Figma read from this session. Run /ds-sync's read steps first.")
    tokens, new_tokens, touched, adds, notes = build(root, prop_path)
    before = L.projections(tokens, cfg)
    after = L.projections(new_tokens, cfg)
    # A change still waiting in the place port writes to is simply overwritten (say, a second try
    # at a proposal before syncing). One waiting in the other place would become a conflict.
    other = "markdown" if start == "figma" else "figma"
    pending = [n for n in touched + [a["name"] for a in adds] if A["tokens"].get(n, {}).get(other)]
    if pending:
        raise SystemExit(f"These tokens have {other} changes that haven't been synced yet. Run /ds-sync first, then port:\n"
                         + "\n".join(f"- `{n}`" for n in pending))
    if start == "figma":
        for n, t in A["tokens"].items():          # Figma's current values, where they've moved on
            if t["figma"] and t["figma_new"] is not None and n in before:
                before[n] = t["figma_new"]
                if n in touched:
                    after[n] = {**t["figma_new"], "value": after[n]["value"], "modes": after[n]["modes"]}
    changes = [n for n in touched if L.canon(before[n]) != L.canon(after[n])]
    same = [n for n in touched if n not in changes]
    add_names = [a["name"] for a in adds]
    again = [n for n in add_names if start == "figma" and A["tokens"].get(n, {}).get("figma")]
    removes = leftovers(root, A, set(add_names)) if start == "figma" and not offline else []

    lines = []
    for n in changes:
        b, a = before[n], after[n]
        row = f"`{n}`: {show(b['value'])} → {show(a['value'])}"
        moved = [f"{m} {show(b['modes'].get(m))} → {show(v)}" for m, v in a["modes"].items() if L.canon(v) != L.canon(b["modes"].get(m))]
        if moved:
            row += f" ({'; '.join(moved)})"
        lines.append(row)
    nodes = L.token_nodes(new_tokens)
    add_lines = []
    for a in adds:
        pj = after[a["name"]]
        where = (new_tokens[a["group"]].get("$extensions", {}).get("figma", {}).get("collection")
                 or {"text": "text styles", "effect": "effect styles", "grid": "grid styles"}[pj["kind"]])
        val = show(pj["value"]) if pj["kind"] == "var" else "a " + pj["kind"] + " style"
        modes = f" ({'; '.join(f'{m} {show(v)}' for m, v in pj['modes'].items())})" if pj.get("modes") else ""
        add_lines.append(f"`{a['name']}` = {val}{modes}, in {where}, set up like `{a['template']}`"
                         + (" (already in Figma from an earlier try; updated)" if a["name"] in again else ""))
    c0, c1 = resolved_colors(before), resolved_colors(after)
    hexs = lambda c: "#" + "".join(f"{round(x * 255):02x}" for x in c[:3])
    follows = [f"`{n}` → {hexs(c1[n])}" for n in sorted(c1) if n not in changes and n not in add_names and c0.get(n) != c1[n]]
    with_alpha = lambda c: hexs(c) + (f" at {round(c[3] * 100)}%" if c[3] < 0.999 else "")
    follow_rows = [{"token": n, "now": with_alpha(c0[n]) if n in c0 else None, "proposed": with_alpha(c1[n]),
                    "from": re.sub(r"/[0-9.]+$", "", after[n]["value"][1:]) if isinstance(after[n].get("value"), str) and after[n]["value"].startswith("@") else None}
                   for n in sorted(c1) if n not in changes and n not in add_names and c0.get(n) != c1[n]]

    comps = A["snap"]["components"]
    def fail(pairs):
        out = {}
        for x in pairs:
            if x["ratio"] < x["need"]:
                f = out.setdefault((x["fg"], x["bg"], x["kind"]), {"ratio": x["ratio"], "need": x["need"], "used": set()})
                f["ratio"] = min(f["ratio"], x["ratio"])
                f["used"].add(x["where"].split(" › ")[0].split(" / ")[0])
        return out
    f0, f1 = fail(L.contrast_pairs(comps, before)), fail(L.contrast_pairs(comps, after))
    new_fails = [f"`{fg}` on {bg} ({kind}): {f['ratio']:.2f}:1, needs {f['need']:g}:1. Used by {', '.join(sorted(f['used']))}"
                 for (fg, bg, kind), f in f1.items() if (fg, bg, kind) not in f0]
    fixed = [f"`{fg}` on {bg} ({kind})" for fg, bg, kind in f0 if (fg, bg, kind) not in f1]
    add_checks, add_fails, add_warns = addition_checks(adds, after, new_tokens)

    fonts_changed = any(before[n].get("type") in ("fontFamily", "fontWeight") for n in changes) \
        or any(after[n].get("kind") == "text" for n in add_names)
    fonts = required_fonts(after, new_tokens) if fonts_changed else []
    ops = [{"op": "update", "name": n, "kind": "var", "proj": after[n], "collection": None} for n in changes]
    ops += [{"op": "create", "name": a["name"], "kind": after[a["name"]]["kind"], "proj": after[a["name"]],
             "collection": new_tokens[a["group"]].get("$extensions", {}).get("figma", {}).get("collection")} for a in adds]
    # tokens that reference another removal go first, so nothing is left pointing at a deleted variable
    refs_removed = lambda n: any(f"@{m}" in json.dumps(A["tokens"][n]["figma_new"]) for m in removes if m != n)
    ops += [{"op": "delete", "name": n, "kind": A["tokens"][n]["figma_new"]["kind"]}
            for n in sorted(removes, key=lambda n: not refs_removed(n))]
    return {"start": start, "changes": changes, "same": same, "lines": lines, "follows": follows, "follow_rows": follow_rows,
            "adds": add_names, "add_lines": add_lines, "add_checks": add_checks, "add_fails": add_fails, "add_warns": add_warns,
            "removes": removes, "new_fails": new_fails, "fixed": fixed, "notes": notes, "fonts": fonts,
            "ops": ops, "new_tokens": new_tokens, "A": A,
            "before": {n: before[n] for n in changes}, "after": {n: after[n] for n in changes + add_names}}


def describe(p, prop_path, root):
    out = [f"Proposal: `{prop_path.relative_to(root) if prop_path.is_relative_to(root) else prop_path}`. Writes to: {p['start']}."]
    if not p["ops"]:
        return "\n".join(out + ["", "Nothing to port: every value in the proposal is already in place."])
    if p["lines"]:
        out += ["", f"Changes ({len(p['lines'])}):"] + [f"- {l}" for l in p["lines"]]
    if p["add_lines"]:
        out += ["", f"Additions ({len(p['add_lines'])}):"] + [f"- {l}" for l in p["add_lines"]]
    if p["removes"]:
        out += ["", "Remove from Figma (added by an earlier port, not in this proposal, never synced): "
                + ", ".join(f"`{n}`" for n in p["removes"])]
    if p["follows"]:
        out += ["", f"Colors that follow through references ({len(p['follows'])}):", "- " + ", ".join(p["follows"])]
    if p["fonts"]:
        out += ["", "Fonts the text styles will need (checked in Figma before anything is written): "
                + ", ".join(f"{f['family']} {f['weight']}" for f in p["fonts"])]
    if p["add_checks"]:
        out += ["", "Accessibility of the additions:"] + [f"- {l}" for l in p["add_checks"]]
    fails = p["new_fails"] + p["add_fails"]
    if fails:
        out += ["", "**New contrast failures:**"] + [f"- {l}" for l in fails]
    else:
        out += ["", "Contrast: no new failures."]
    if p["fixed"]:
        out += ["Contrast failures this fixes: " + ", ".join(p["fixed"])]
    if p["add_warns"]:
        out += ["", "Warnings:"] + [f"- {w}" for w in p["add_warns"]]
    if p["same"]:
        out += ["", "Already the same, so left alone: " + ", ".join(f"`{n}`" for n in p["same"])]
    if p["notes"]:
        out += ["", "Notes:"] + [f"- {n}" for n in p["notes"]]
    return "\n".join(out)


def prepare(root, p, prop_path):
    if p["start"] == "markdown":
        (root / "tokens.json").write_text(json.dumps(p["new_tokens"], indent=2, ensure_ascii=False) + "\n")
        render = root / "_scripts" / "render.py"
        if render.exists():
            subprocess.run([sys.executable, "-I", str(render), str(root)], check=True)
        print(f"\nWrote {len(p['changes'])} change(s) and {len(p['adds'])} addition(s) into tokens.json and regenerated "
              "the foundation sections. Run /ds-sync to carry them into Figma.")
        return
    nonce = secrets.token_hex(6)
    js = (SYNC / "figma_write.template.js").read_text().replace(
        "__INPUT__", json.dumps({"nonce": nonce, "ops": p["ops"], "require_fonts": p["fonts"]}, separators=(",", ":"), ensure_ascii=False))
    (root / ".sync" / "port.js").write_text(js)
    (root / ".sync" / "port-tag.json").write_text(json.dumps(
        {"nonce": nonce, "proposal": str(prop_path), "started": now(), "lines": p["lines"] + p["add_lines"],
         "ops": [{k: v for k, v in o.items() if k != "proj"} for o in p["ops"]]}, indent=1, ensure_ascii=False) + "\n")
    (root / ".sync" / "figma-write.json").unlink(missing_ok=True)
    print(f"\nWrote .sync/port.js ({len(js) // 1024} KB, tag {nonce}). Run it with use_figma for file "
          f"{(L.load_config(root).get('figma') or {}).get('file_key')}, then run `port.py finish`.")


def finish(root):
    tag_path = root / ".sync" / "port-tag.json"
    if not tag_path.exists():
        raise SystemExit("No .sync/port-tag.json. Run `port.py prepare` first.")
    tag = json.loads(tag_path.read_text())
    got = find_result(root, "write", tag["nonce"])
    done, errors = got["done"], got["errors"]
    tag.update({"finished": now(), "done": sorted(done), "errors": errors,
                "created": [o["name"] for o in tag["ops"] if o["op"] == "create" and o["name"] in done]})
    stamp = tag["finished"].replace(":", "")
    (root / ".sync" / f"port-{stamp}.json").write_text(json.dumps(tag, indent=1, ensure_ascii=False) + "\n")
    for f in ("port-tag.json", "port.js", "figma-write.json"):
        (root / ".sync" / f).unlink(missing_ok=True)
    print(f"Figma accepted {len(done)} of {len(tag['ops'])} item(s). Record: .sync/port-{stamp}.json.")
    if errors:
        print("Figma refused these:\n" + "\n".join(f"- `{e['name']}`: {e['error']}" for e in errors))
    if done:
        print("Next: look at the components in Figma, adjust anything there, then run /ds-sync to carry the result into the markdown.")
    else:
        print("Nothing was written. Fix the proposal (or install the fonts in Figma) and run `port.py plan` again.")
    sys.exit(2 if errors else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["plan", "prepare", "finish"])
    ap.add_argument("--root")
    ap.add_argument("--from", dest="src")
    ap.add_argument("--to", choices=["figma", "markdown"], default="figma",
                    help="where to write; markdown only when someone asks for it first")
    a = ap.parse_args()
    root = L.find_root(a.root)
    if a.command == "finish":
        return finish(root)
    prop_path = Path(a.src).resolve() if a.src else root / "intake" / "proposed-tokens.json"
    if not prop_path.exists():
        raise SystemExit(f"No proposal at {prop_path}. /brand-intake writes one, or write it by hand (see FORMATS.md).")
    p = plan(root, prop_path, to=a.to)
    print(describe(p, prop_path, root))
    if a.command == "prepare" and p["ops"]:
        prepare(root, p, prop_path)


if __name__ == "__main__":
    main()
