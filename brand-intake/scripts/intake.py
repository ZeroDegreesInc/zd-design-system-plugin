"""Measure a client's site and turn it into evidence for /brand-intake.

  python3 -I intake.py prepare --url URL [--root DIR] [--page URL ...] [--pages N] [--client NAME]
      Writes ROOT/intake/scan.js (and audit.md, proposal.md and backlog.md from the templates, if missing).
      Run scan.js in the browser on any page of the site.
  python3 -I intake.py collect [--root DIR]
      Finds the scan result (see find_result) and saves ROOT/intake/scan.json.
  python3 -I intake.py analyse [--root DIR]
      Writes ROOT/intake/analysis.json and prints the findings and suggestions to write from.
  python3 -I intake.py check [--root DIR]
      Checks ROOT/intake/proposed-tokens.json the way /port-tokens will, without Figma.
  python3 -I intake.py render [--root DIR]
      Fills the generated sections of ROOT/intake/audit.md (from the analysis) and proposal.md (from the proposal).
  python3 -I intake.py export [--root DIR]
      Writes clean copies of the three files to ROOT/intake/publish/, ready to put in the doc's tabs.
"""
import argparse
import colorsys
import datetime
import io
import json
import math
import os
import re
import secrets
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
sys.path.insert(0, str(SKILLS / "ds-sync" / "scripts"))
sys.path.insert(0, str(SKILLS / "port-tokens" / "scripts"))
import synclib as L

KEY = "brand_intake_scan"

# What the scan's pattern names correspond to in a starter-kit system. Anything missing here is a
# candidate new component; the skill decides which are worth building.
KIT_EQUIVALENT = {
    "card": "Card", "badge or tag": "Badge", "alert or banner": "Alert", "select": "Select",
    "checkbox": "Checkbox", "radio": "Radio", "search": "Input", "form": "Input",
}
NOT_COMPONENTS = {"image", "icon", "form", "social links", "skip link"}   # evidence, not backlog items


# ── finding the browser's result ─────────────────────────────────────

def find_result(root, nonce):
    """The scan result: from intake/scan-raw.json if saved by hand, otherwise from the session log.
    Browser tool results are written to the Claude Code session's .jsonl log; the nonce in the
    script ties the result to this scan."""
    saved = root / "intake" / "scan-raw.json"
    if saved.exists():
        got = json.loads(saved.read_text())[KEY]
        if got.get("nonce") != nonce:
            raise SystemExit("scan-raw.json is from a different scan. Delete it, or run the current scan.js again.")
        return got
    logs = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
    recent = sorted((p for p in logs.glob("*/*.jsonl") if time.time() - p.stat().st_mtime < 6 * 3600),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    dec = json.JSONDecoder()
    for log in recent:
        # split on newlines only: splitlines() also breaks on U+2028, which page text can contain
        for line in reversed(log.read_text(errors="ignore").split("\n")):
            if KEY not in line or nonce not in line:
                continue
            for c in (json.loads(line).get("message", {}).get("content") or []):
                if not isinstance(c, dict) or c.get("type") != "tool_result":
                    continue
                parts = c.get("content")
                parts = [parts] if isinstance(parts, str) else parts or []
                for x in parts:
                    t = x if isinstance(x, str) else x.get("text", "") if isinstance(x, dict) else ""
                    t = t.strip()
                    if t.startswith('"'):                      # some tools return the string JSON-encoded
                        try:
                            t = dec.raw_decode(t)[0]
                        except ValueError:
                            continue
                    if not t.startswith('{"' + KEY):
                        continue
                    got = dec.raw_decode(t)[0][KEY]
                    if got.get("nonce") == nonce:
                        return got
    raise SystemExit(f"Couldn't find the scan result (tag {nonce}). Save the browser's result as intake/scan-raw.json and run this again.")


# ── color helpers ────────────────────────────────────────────────────

def rgb(h):
    h = h.split("/")[0]
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def lum(h):
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb(h)]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    x, y = sorted([lum(a), lum(b)], reverse=True)
    return (x + 0.05) / (y + 0.05)


def lab(h):
    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    r, g, b = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb(h)]
    X, Y, Z = (r * .4124 + g * .3576 + b * .1805) / .95047, r * .2126 + g * .7152 + b * .0722, (r * .0193 + g * .1192 + b * .9505) / 1.08883
    return 116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))


def delta(a, b):
    return math.dist(lab(a), lab(b))


def chroma(h):
    _, a, b = lab(h)
    return math.hypot(a, b)


def hue_name(h):
    # pale tints carry little chroma, so near white a smaller amount still reads as a color (#e7f4fc is blue)
    if chroma(h) < (5 if lab(h)[0] > 88 else 12):
        return "neutral"
    hue = colorsys.rgb_to_hls(*rgb(h))[0] * 360
    for limit, name in ((15, "red"), (40, "orange"), (65, "yellow"), (165, "green"), (195, "teal"), (255, "blue"), (290, "purple"), (335, "pink"), (361, "red")):
        if hue < limit:
            return name


def is_hex(v):
    return isinstance(v, str) and re.fullmatch(r"#[0-9a-f]{6}(/[\d.]+)?", v) is not None


def large_text(size, weight):
    return size >= 24 or (size >= 18.66 and weight >= 700)


def from_lab(L_, a, b):
    def finv(t):
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787
    fy = (L_ + 16) / 116
    X, Y, Z = finv(fy + a / 500) * .95047, finv(fy), finv(fy - b / 200) * 1.08883
    lin = (X * 3.2406 - Y * 1.5372 - Z * .4986, -X * .9689 + Y * 1.8758 + Z * .0415, X * .0557 - Y * .2040 + Z * 1.0570)
    srgb = [12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - .055 for c in lin]
    return "#" + "".join(f"{round(min(1, max(0, c)) * 255):02x}" for c in srgb)


# ── the kit side ─────────────────────────────────────────────────────

def kit(root):
    """The system's current values, resolved, for side-by-side comparison."""
    tokens = json.loads((root / "tokens.json").read_text())
    cfg = L.load_config(root)
    proj = L.projections(tokens, cfg)
    nodes = L.token_nodes(tokens)

    def val(n):
        v = proj[n].get("value")
        for _ in range(10):
            if isinstance(v, str) and v.startswith("@"):
                v = proj.get(v[1:], {}).get("value")
        return v
    colors = {n: "#" + "".join(f"{round(x * 255):02x}" for x in L._rgb(n, proj)[:3]) for n, p in proj.items() if p.get("type") == "color" and L._rgb(n, proj)}
    groups = {}
    for n, (g, _, _) in nodes.items():
        groups.setdefault(g, []).append(n)
    num = lambda g: {n: val(n) for n in groups.get(g, []) if isinstance(val(n), (int, float))}
    sizes = {n: (proj[n]["value"], proj[n]["modes"].get("Mobile")) for n in groups.get("typography", []) if n.startswith("font-size/")}
    comps = []
    for p in sorted((root / "components").glob("*.md")):
        fm = L.frontmatter(p.read_text())
        if fm.get("name"):
            comps.append(fm["name"])
    return {"colors": colors, "primitives": [n for n in groups.get("primitive-color", [])], "semantic": groups.get("semantic-color", []),
            "spacing": num("spacing"), "radius": num("border-radius"), "border": num("border-width"), "breakpoints": num("breakpoints"),
            "font_sizes": sizes, "families": {n: val(n) for n in groups.get("typography", []) if n.startswith("font-family/")},
            "weights": {n: val(n) for n in groups.get("typography", []) if n.startswith("font-weight/")}, "components": comps, "proj": proj}


# ── analysis ─────────────────────────────────────────────────────────

def cluster(colors, limit=4.0):
    """Group near-identical colors. colors: {hex: weight}. Returns [{hex, weight, members}] heaviest first."""
    out = []
    for h, w in sorted(colors.items(), key=lambda kv: -kv[1]):
        for c in out:
            if delta(h, c["hex"]) < limit:
                c["weight"] += w
                c["members"].append(h)
                break
        else:
            out.append({"hex": h, "weight": w, "members": [h]})
    return out


def weighted_median(pairs):
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return None


def scale_for(base, kit_steps):
    """A 4-step scale around a brand color, following the lightness of the kit's own brand scale.
    The base lands on the step nearest its own lightness."""
    L0, a0, b0 = lab(base)
    Ls = [lab(h)[0] for h in kit_steps]
    at = min(range(len(Ls)), key=lambda i: abs(Ls[i] - L0))
    out = []
    for i, Lk in enumerate(Ls):
        if i == at:
            out.append(base)
            continue
        k = max(0.12, min(1.0, (100 - Lk) / max(1, 100 - L0))) if Lk > L0 else 1.0     # tints lose chroma
        out.append(from_lab(Lk, a0 * k, b0 * k))
    return out, at


def analyse(root):
    scan = json.loads((root / "intake" / "scan.json").read_text())
    K = kit(root)
    widths = sorted(scan["widths"], key=lambda w: -int(w))
    D, M = scan["widths"][widths[0]], scan["widths"].get(widths[-1]) if len(widths) > 1 else None
    pages = scan["pages"]
    tag = json.loads((root / "intake" / "scan-tag.json").read_text())
    A = {"site": scan["origin"], "scanned_at": scan["scanned_at"], "pages": pages, "desktop": int(widths[0]), "not_covered": tag.get("not_covered"),
         "mobile": int(widths[-1]) if M else None}

    # colors
    text = {h: e["n"] for h, e in D["colors_text"].items() if is_hex(h) and "/" not in h}
    bg = {h: e["n"] for h, e in D["colors_bg"].items() if is_hex(h) and "/" not in h}
    border = {h: e["n"] for h, e in D["colors_border"].items() if is_hex(h) and "/" not in h}
    btn = {}
    for sig, e in D["buttons"].items():
        b = sig.split("|")[0]
        if is_hex(b) and "/" not in b:
            btn[b] = btn.get(b, 0) + e["n"]
    tw, bw = sum(text.values()) or 1, sum(bg.values()) or 1
    every = {}
    for src, w in ((text, 1 / tw), (bg, 1 / bw), (border, 1 / (sum(border.values()) or 1)), (btn, 1 / (sum(btn.values()) or 1))):
        for h, n in src.items():
            every[h] = every.get(h, 0) + n * w
    palette = cluster(every)
    for c in palette:
        c["roles"] = [r for r, src in (("text", text), ("background", bg), ("border", border), ("button", btn)) if any(m in src for m in c["members"])]
        c["family"] = hue_name(c["hex"])
        c["on_white"] = round(ratio(c["hex"], "#ffffff"), 2)
        c["pages"] = sorted({p for src in (D["colors_text"], D["colors_bg"], D["colors_border"]) for m in c["members"] for p in src.get(m, {}).get("pages", [])})
    A["palette_rare"] = len([c for c in palette if c["weight"] < 0.002])
    palette = [c for c in palette if c["weight"] >= 0.002]
    A["palette"] = palette
    # the scan keeps only the most-used items of each kind; when a list is full, the tables are a sample
    caps = {"colors_text": 40, "colors_bg": 40, "colors_border": 30, "type": 45, "patterns": 40}
    A["sampled"] = {k: len(D.get(k, {})) >= n for k, n in caps.items()}
    chromatic = [c for c in palette if c["family"] != "neutral"]
    brand = sorted(chromatic, key=lambda c: -(c["weight"] + 2 * ("button" in c["roles"]) + ("background" in c["roles"])))
    A["brand_candidates"] = [{"hex": c["hex"], "family": c["family"], "roles": c["roles"], "on_white": c["on_white"]} for c in brand[:4]]
    near = []
    for i, a in enumerate(palette):
        for b in palette[i + 1:]:
            d = delta(a["hex"], b["hex"])
            if d < 10 and a["family"] == b["family"]:
                near.append({"a": a["hex"], "b": b["hex"], "delta": round(d, 1)})
    A["near_duplicates"] = near
    kit_brand = [K["colors"][n] for n in K["primitives"] if n.startswith("brand/")]
    if brand and len(kit_brand) >= 2:
        steps, at = scale_for(brand[0]["hex"], kit_brand)
        A["brand_scale_suggestion"] = {"base": brand[0]["hex"], "base_step": at, "steps": steps,
                                       "on_white": [round(ratio(s, "#ffffff"), 2) for s in steps]}
    neutrals = sorted([c for c in palette if c["family"] == "neutral"], key=lambda c: -lab(c["hex"])[0])
    A["neutrals"] = [{"hex": c["hex"], "roles": c["roles"], "on_white": c["on_white"]} for c in neutrals]

    # contrast as the site uses it
    fails, total, on_image = [], 0, 0
    for key, e in D["text_pairs"].items():
        fg, back, size, weight = key.split("|")
        size, weight = float(size or 0), int(float(weight or 400))
        total += e["n"]
        if back == "image":
            on_image += e["n"]
            continue
        if not (is_hex(fg) and is_hex(back)):
            continue
        need = 3.0 if large_text(size, weight) else 4.5
        fgh = fg.split("/")[0]
        if fgh == back:                 # text can't really sit on its own color: it's over something the scan can't see
            on_image += e["n"]
            continue
        r = ratio(fgh, back)
        if r < need:
            fails.append({"fg": fg, "bg": back, "size": size, "weight": weight, "ratio": round(r, 2), "need": need,
                          "chars": e["n"], "pages": e["pages"], "example": e.get("ex")})
    fails.sort(key=lambda f: -f["chars"])
    A["contrast"] = {"total_chars": total, "fails": fails[:20], "failing_share": round(sum(f["chars"] for f in fails) / (total or 1), 3),
                     "on_image_share": round(on_image / (total or 1), 3), "pages_checked": len([p for p in pages if p and "error" not in p])}

    # typography
    fam = {}
    for key, e in D["type"].items():
        f = key.split("|")[0]
        fam[f] = fam.get(f, 0) + e["n"]
    tot = sum(fam.values()) or 1
    A["families"] = [{"family": f, "share": round(n / tot, 3)} for f, n in sorted(fam.items(), key=lambda kv: -kv[1])]
    def roles(W):
        out = {}
        for key, e in (W or {}).get("type_by_role", {}).items():
            role, size, weight, family = key.split("|")
            out.setdefault(role, []).append((float(size), int(float(weight)), family, e["n"], e.get("ex")))
        res = {}
        for role, items in out.items():
            res[role] = {"size": weighted_median([(s, n) for s, _, _, n, _ in items]),
                         "weight": weighted_median([(w, n) for _, w, _, n, _ in items]),
                         "family": max(items, key=lambda x: x[3])[2], "variants": len({(s, w) for s, w, _, _, _ in items}),
                         "example": max(items, key=lambda x: x[3])[4]}
        return res
    A["type_roles"] = {"desktop": roles(D), "mobile": roles(M)}
    # h1–h6 always, as the site defines them: the browser's reading of the site's heading styles (the
    # scan's probe), then heading sizes the site sets as CSS variables, then what was measured on the pages.
    # A measured median mixes every size a tag is used at, so it comes last.
    css = {}
    for k, val in scan["rules"].get("root_vars", {}).items():
        m = re.search(r"\bh([1-6])\b", k.replace("-", " "))
        if m and "size" in k and re.fullmatch(r"[\d.]+px", val.strip()):
            css.setdefault("mobile" if "mobile" in k else "desktop", {})["h" + m.group(1)] = float(val.strip()[:-2])
    for width, W in (("desktop", D), ("mobile", M)):
        if not W:
            continue
        roles_w = A["type_roles"][width]
        for n in range(1, 7):
            h = f"h{n}"
            seen = roles_w.get(h)
            probe = (W.get("heading_probe") or {}).get(h)
            if probe:
                roles_w[h] = {"size": probe[0], "weight": probe[1], "family": probe[2], "source": "site styles"}
            elif h in css.get(width, {}):
                roles_w[h] = {"size": css[width][h], "weight": seen and seen["weight"], "family": seen and seen["family"], "source": "CSS variable"}
            elif seen:
                seen["source"] = "measured"
    sizes = {}
    for key, e in D["type"].items():
        s = float(key.split("|")[1] or 0)
        sizes[s] = sizes.get(s, 0) + e["n"]
    A["distinct_sizes"] = len(sizes)
    A["type_samples"] = [dict(zip(("family", "size", "weight", "line_height", "letter_spacing", "transform"), k.split("|")), chars=e["n"], tag=e.get("ex"))
                         for k, e in list(D["type"].items())[:15]]
    A["kit_type"] = {"families": K["families"], "weights": K["weights"], "sizes": K["font_sizes"]}

    # spacing, shape, depth, layout
    def hist(m):
        return sorted(((float(k) if k != "full" else k, e["n"]) for k, e in m.items()), key=lambda kv: -kv[1])
    sp = hist(D["spacing"])
    kit_sp = sorted(set(K["spacing"].values()))
    on_scale = sum(n for v, n in sp if isinstance(v, float) and any(abs(v - k) < 0.5 for k in kit_sp))
    A["spacing"] = {"top": sp[:16], "on_kit_scale": round(on_scale / (sum(n for _, n in sp) or 1), 2), "kit": K["spacing"],
                    "on_4px_grid": round(sum(n for v, n in sp if isinstance(v, float) and v % 4 == 0) / (sum(n for _, n in sp) or 1), 2)}
    A["radius"] = {"top": [(k, e["n"], e.get("ex")) for k, e in list(D["radii"].items())[:10]], "kit": K["radius"]}
    A["borders"] = {"widths": [(k, e["n"]) for k, e in D["border_widths"].items()], "kit": K["border"]}
    A["shadows"] = [(k, e["n"]) for k, e in list(D["shadows"].items())[:8]]
    bps = scan["rules"]["breakpoints"]
    A["breakpoints"] = {"site": sorted(((k, n) for k, n in bps.items()), key=lambda kv: -kv[1])[:8], "kit": K["breakpoints"]}

    # components
    A["buttons"] = []
    for sig, e in D["buttons"].items():
        p = sig.split("|")
        kind = "filled" if is_hex(p[0]) else "outlined" if not p[2].startswith("0 ") else "text only"
        A["buttons"].append({"kind": kind, "bg": p[0], "text": p[1], "border": p[2], "radius": p[3], "padding": p[4], "size": p[5], "weight": p[6],
                             "family": p[7], "transform": p[8], "letter_spacing": p[9], "height": p[10] if len(p) > 10 else None,
                             "count": e["n"], "pages": e["pages"], "example": e.get("ex"),
                             "contrast": round(ratio(p[1].split("/")[0], p[0]), 2) if is_hex(p[0]) and is_hex(p[1]) else None})
    A["buttons"].sort(key=lambda b: (b["kind"] == "text only", -b["count"]))
    A["inputs"] = [dict(zip(("bg", "text", "border_top", "border_bottom", "radius", "height", "size", "placeholder"), s.split("|")),
                        count=e["n"], example=e.get("ex"), pages=e["pages"]) for s, e in D["inputs"].items()]
    A["inline_links"] = [dict(zip(("color", "decoration", "weight"), s.split("|")), count=e["n"], example=e.get("ex")) for s, e in D["inline_links"].items()]
    pats = []
    for name, e in D["patterns"].items():
        pats.append({"pattern": name, "count": e["n"], "pages": e["pages"], "example": e.get("ex"),
                     "kit": KIT_EQUIVALENT.get(name), "candidate": name not in KIT_EQUIVALENT and name not in NOT_COMPONENTS})
    A["patterns"] = sorted(pats, key=lambda p: (-len(p["pages"]), -p["count"]))
    A["kit_components"] = K["components"]

    # accessibility beyond contrast
    issues = [{"issue": k, "count": e["n"], "pages": e["pages"], "note": e.get("ex")} for k, e in D["issues"].items()]
    small = [r for r, v in A["type_roles"]["mobile"].items() if v["size"] and v["size"] < 12] if M else []
    if small:
        issues.append({"issue": "text under 12px on mobile", "count": len(small), "pages": [], "note": ", ".join(small)})
    A["issues"] = issues
    A["fonts_loaded"] = scan.get("loaded_fonts", [])
    A["font_faces"] = scan["rules"].get("font_faces", [])
    A["css_variables"] = {k: v for k, v in scan["rules"].get("root_vars", {}).items()
                          if re.search(r"#[0-9a-f]{3,8}\b|rgb|font|size|space|radius|gap|shadow", (k + v).lower())}
    A["css_variables_framework"] = scan["rules"].get("root_vars_skipped", 0)
    # color scales the site already defines as variables (--color-red-400 and so on): the best raw
    # material for the proposal, because the brand's own team chose them
    vars_ = scan["rules"].get("root_vars", {})
    def resolve_var(v, depth=0):
        m = re.fullmatch(r"var\((--[\w-]+)\)", v.strip())
        return resolve_var(vars_.get(m.group(1), ""), depth + 1) if m and depth < 5 else v.strip().lower()
    scales = {}
    for k, v in vars_.items():
        m = re.fullmatch(r"--(.+?)-(\d{2,3})", k)
        hv = resolve_var(v)
        if m and re.fullmatch(r"#[0-9a-f]{3}|#[0-9a-f]{6}", hv):
            hv = hv if len(hv) == 7 else "#" + "".join(c * 2 for c in hv[1:])
            scales.setdefault(m.group(1), []).append((int(m.group(2)), hv))
    A["css_scales"] = {k: sorted(v) for k, v in scales.items() if len(v) >= 2}
    A["css_scale_order"] = {}
    for k, v in A["css_scales"].items():
        ls = [lum(h) for _, h in v]
        if not (all(a >= b for a, b in zip(ls, ls[1:])) or all(a <= b for a, b in zip(ls, ls[1:]))):
            A["css_scale_order"][k] = "steps don't run in order of lightness"
    A["css_roles"] = {k: resolve_var(v) for k, v in vars_.items()
                      if re.search(r"brand|primary|secondary|accent|status|success|danger|error|warning|info|link|heading|paragraph|text", k)
                      and re.fullmatch(r"#[0-9a-f]{3,6}", resolve_var(v))}
    A["logo"] = scan.get("logo")
    return A


# ── guidelines only ──────────────────────────────────────────────────
# When there's no site to scan, intake/guidelines.json holds what the brand guidelines define, written by Claude
# from reading them (see FORMATS.md). The analysis measures that palette instead of measured use.

def verdict(r):
    return "AA" if r >= 4.5 else "large text only" if r >= 3 else "fails"


def analyse_guidelines(root):
    G = json.loads((root / "intake" / "guidelines.json").read_text())
    K = kit(root)
    colors = []
    for c in G["colors"]:
        h = c["hex"].lower()
        colors.append({**c, "hex": h, "family": hue_name(h), "with_white": round(ratio(h, "#ffffff"), 2)})
    # the brand's own darkest neutral stands in for black, as the text color it would actually use
    dark = min((c["hex"] for c in colors if c["family"] == "neutral"), key=lum, default="#000000")
    for c in colors:
        c["with_dark"] = round(ratio(c["hex"], dark), 2)
    pairs = [{**q, "ratio": round(ratio(q["fg"].lower(), q["bg"].lower()), 2)} for q in G.get("pairs", [])]
    A = {"mode": "guidelines", "client": G.get("client"), "date": G.get("date") or datetime.date.today().isoformat(),
         "sources": G.get("sources", []), "colors": colors, "dark": dark, "pairs": pairs, "type": G.get("type", []),
         "sizes": G.get("sizes", []), "not_covered": G.get("not_covered"),
         "kit_families": K["families"], "kit_weights": K["weights"]}
    primary = next((c for c in colors if c.get("primary")), None)
    kit_brand = [K["colors"][n] for n in K["primitives"] if n.startswith("brand/")]
    if primary and len(kit_brand) >= 2:
        steps, at = scale_for(primary["hex"], kit_brand)
        A["brand_scale_suggestion"] = {"base": primary["hex"], "base_step": at, "steps": steps,
                                       "on_white": [round(ratio(x, "#ffffff"), 2) for x in steps]}
    return A


def summary_guidelines(A):
    o = [f"# Analysis of the {A['client']} guidelines", f"{len(A['colors'])} colors, {len(A['type'])} typefaces, {len(A['pairs'])} stated pairings.", "",
         f"## Palette (contrast with white, and with the darkest neutral {A['dark']})"]
    for c in A["colors"]:
        o.append(f"- {c['hex']} {c['name']} ({c.get('group') or '–'}, {c['family']}): {c['with_white']}:1 with white ({verdict(c['with_white'])}), "
                 f"{c['with_dark']}:1 with {A['dark']}" + (f"; use: {c['use']}" if c.get("use") else ""))
    if A["pairs"]:
        o += ["", "## Stated pairings"] + [f"- {q['fg']} on {q['bg']}: {q['ratio']}:1 ({verdict(q['ratio'])}); {q.get('use') or ''}" for q in A["pairs"]]
    o += ["", "## Type"] + [f"- {t['family']} ({t.get('role') or '–'}): " + ", ".join(f"{w['weight']} {w.get('name') or ''} for {w.get('use') or '–'}" for w in t.get("weights", [])) for t in A["type"]]
    o.append("Kit families now: " + ", ".join(f"{k.split('/')[-1]} {v}" for k, v in A["kit_families"].items()) + "; weights: " + ", ".join(f"{k.split('/')[-1]} {v}" for k, v in A["kit_weights"].items()))
    if A.get("brand_scale_suggestion"):
        sc = A["brand_scale_suggestion"]
        o.append(f"Suggested brand scale (kit lightness, base at step {sc['base_step'] + 1}): " + ", ".join(f"{h} ({r}:1)" for h, r in zip(sc["steps"], sc["on_white"])))
    return "\n".join(o)


def render_guidelines(root, A, text):
    owner = A["client"] + ("'" if A["client"].endswith("s") else "'s")
    src = [f"Read from {owner} brand guidelines on {A['date']}. There was no site to measure, so the audit covers what the guidelines define. "
           "Numbers in brackets in the audit and the backlog refer to these sources.", "",
           "| # | Source | Covers |", "| --- | --- | --- |"]
    for i, g in enumerate(A["sources"]):
        title = g["title"].replace("|", "\\|")
        src.append(f"| {i + 1} | " + (f"[{title}]({g['url']})" if g.get("url") else title) + f" | {g.get('covers') or '–'} |")
    if A.get("not_covered"):
        src += ["", "Not covered: " + A["not_covered"]]
    text = section(text, "sources", "\n".join(src))

    dark = A["dark"]
    pal = [f"Every color the guidelines define ({len(A['colors'])}), in their own groups. Contrast is symmetric, so *With white* is both the color as text on white "
           f"and white text on the color. *With {dark}* uses the guidelines' darkest neutral, the text color they'd pair with a pale fill.", "",
           f"| Color | Name | Group | Family | With white | With `{dark}` |", "| --- | --- | --- | --- | --- | --- |"]
    for c in A["colors"]:
        pal.append(f"| `{c['hex']}` | {c['name']} | {c.get('group') or '–'} | {c['family']} | {c['with_white']}:1 ({verdict(c['with_white'])}) | "
                   + (f"{c['with_dark']}:1 ({verdict(c['with_dark'])})" if c["hex"] != dark else "–") + " |")
    text = section(text, "audit-palette", "\n".join(pal))

    chromatic = [c for c in A["colors"] if c["hex"] not in ("#ffffff", dark)]
    n = {v: len([c for c in chromatic if verdict(c["with_white"]) == v]) for v in ("AA", "large text only", "fails")}
    con = [f"**Measured contrast:** of the {len(chromatic)} colors besides white and `{dark}`, {n['AA']} reach 4.5:1 with white, enough for text of any size. "
           f"{n['large text only']} reach only 3:1, enough for large text and UI parts such as borders and icons. {n['fails']} fall short of 3:1, so neither they on white nor white on them is readable."]
    if A["pairs"]:
        bad = [q for q in A["pairs"] if q["ratio"] < 4.5]
        con += ["", f"Every pairing the guidelines show or name ({len(A['pairs'])})" + (f", {len(bad)} below 4.5:1." if bad else ", all at 4.5:1 or more."), "",
                "| Text | On | Ratio | Use | Source |", "| --- | --- | --- | --- | --- |"]
        con += [f"| `{q['fg'].lower()}` | `{q['bg'].lower()}` | {q['ratio']}:1 ({verdict(q['ratio'])}) | {q.get('use') or '–'} | {q.get('source') or '–'} |" for q in A["pairs"]]
    text = section(text, "audit-contrast", "\n".join(con))

    ty = ["Every typeface and weight the guidelines name, with the use they give it.", "",
          "| Typeface | Role | Weight | Use |", "| --- | --- | --- | --- |"]
    for t in A["type"]:
        for i, w in enumerate(t.get("weights") or [{"weight": "–"}]):
            ty.append(f"| {t['family'] if i == 0 else ''} | {(t.get('role') or '–') if i == 0 else ''} | {w['weight']}" + (f" ({w['name']})" if w.get("name") else "") + f" | {w.get('use') or '–'} |")
    notes = [f"- **{t['family']}:** {t['notes']}" for t in A["type"] if t.get("notes")]
    if A["sizes"]:
        unit = lambda z: z.get("unit") or "px"
        ty += ["", "The sizes the guidelines set" + (", for print" if any(unit(z) == "pt" for z in A["sizes"]) else "") + ".", "",
               "| Style | Size / leading | Weight |", "| --- | --- | --- |"]
        ty += [f"| {z['style']} | {z['size']:g}{unit(z)}" + (f" / {z['line_height']:g}{unit(z)}" if z.get("line_height") else "") + f" | {z.get('weight') or '–'} |" for z in A["sizes"]]
    else:
        ty += ["", "The guidelines set no type sizes or line heights."]
    text = section(text, "audit-type", "\n".join(ty + ([""] + notes if notes else [])))
    return text


def summary(A):
    """The analysis as readable notes, for Claude to write the audit and proposal from."""
    mob = len([p for p in A["pages"] if p and A["mobile"] in (p.get("widths") or [])])
    o = [f"# Analysis of {A['site']}", f"{A['contrast']['pages_checked']} pages at {A['desktop']}px" + (f", {mob} at {A['mobile']}px" if A["mobile"] else "") + ":"]
    o += [f"  [{i + 1}] {p.get('kind') or '?'}: {p['url']}" + (" (form)" if p.get("has_form") else "") + (f" ERROR {p['error']}" if p.get("error") else "") for i, p in enumerate(A["pages"]) if p]
    o.append("")
    o.append("## Palette (merged near-identical colors; weight = share of use)")
    for c in A["palette"][:18]:
        o.append(f"- {c['hex']} {c['family']}: {', '.join(c['roles'])}; weight {c['weight']:.2f}; {c['on_white']}:1 on white; {len(c['members'])} variant(s)")
    o.append("Brand candidates: " + ", ".join(f"{b['hex']} ({b['family']}, {'/'.join(b['roles'])}, {b['on_white']}:1 on white)" for b in A["brand_candidates"]))
    if A.get("brand_scale_suggestion"):
        s = A["brand_scale_suggestion"]
        o.append(f"Suggested brand scale (kit lightness, base at step {s['base_step'] + 1}): " + ", ".join(f"{h} ({r}:1)" for h, r in zip(s["steps"], s["on_white"])))
    if A["near_duplicates"]:
        o.append("Near-duplicates: " + ", ".join(f"{d['a']}≈{d['b']} (ΔE {d['delta']})" for d in A["near_duplicates"][:10]))
    o += ["", "## Contrast as used", f"{A['contrast']['failing_share']:.0%} of text (by characters) fails AA; {A['contrast']['on_image_share']:.0%} sits on images (not measured)."]
    for f in A["contrast"]["fails"][:10]:
        o.append(f"- {f['fg']} on {f['bg']} at {f['size']}px/{f['weight']}: {f['ratio']}:1 (needs {f['need']}), {f['chars']} chars, e.g. \"{f['example']}\"")
    o += ["", "## Type", "Families: " + ", ".join(f"{f['family']} {f['share']:.0%}" for f in A["families"][:5]), f"{A['distinct_sizes']} distinct font sizes on desktop."]
    for r, v in A["type_roles"]["desktop"].items():
        m = A["type_roles"]["mobile"].get(r, {})
        o.append(f"- {r}: {v['size']}px / {v.get('weight')} {v.get('family')} (mobile {m.get('size', '–')}px)"
                 + (f", {v['variants']} size/weight combinations" if v.get("variants") else f", from {v.get('source')}"))
    o.append("Kit sizes (desktop/mobile): " + ", ".join(f"{n.split('/')[1]} {d}/{mo}" for n, (d, mo) in A["kit_type"]["sizes"].items()))
    o.append("Kit families: " + ", ".join(f"{n} = {v}" for n, v in A["kit_type"]["families"].items()))
    o.append("Fonts loaded: " + ", ".join(A["fonts_loaded"][:12]))
    o += ["", "## Spacing, shape, depth", "Spacing top values: " + ", ".join(f"{v:g}px×{n}" for v, n in A["spacing"]["top"] if isinstance(v, float)),
          f"{A['spacing']['on_kit_scale']:.0%} of spacing values are on the kit scale; {A['spacing']['on_4px_grid']:.0%} on a 4px grid. Kit: " + ", ".join(f"{k} {v:g}" for k, v in A["spacing"]["kit"].items()),
          "Radii: " + ", ".join(f"{k}px×{n} ({ex})" for k, n, ex in A["radius"]["top"]) + ". Kit: " + ", ".join(f"{k} {v:g}" for k, v in A["radius"]["kit"].items()),
          "Shadows: " + "; ".join(f"{s} ×{n}" for s, n in A["shadows"][:5]),
          "Breakpoints: " + ", ".join(f"{k}px ×{n}" for k, n in A["breakpoints"]["site"]) + ". Kit: " + ", ".join(f"{k} {v:g}" for k, v in A["breakpoints"]["kit"].items())]
    o += ["", "## Buttons (distinct styles)"]
    for b in A["buttons"][:10]:
        o.append(f"- {b['kind']}: bg {b['bg']}, text {b['text']}, border {b['border']}, radius {b['radius']}, padding {b['padding']}"
                 + (f", {b['height']}px tall" if b.get("height") else "") + f", {b['size']}px/{b['weight']} {b['transform']}, ×{b['count']} on {len(b['pages'])} page(s), contrast {b['contrast']}, e.g. \"{b['example']}\"")
    o += ["", "## Inputs"] + [f"- {i}" for i in A["inputs"][:6]]
    o += ["", "## Inline links"] + [f"- {l}" for l in A["inline_links"][:5]]
    o += ["", "## Patterns seen (pages = how widespread)"]
    for p in A["patterns"]:
        o.append(f"- {p['pattern']}: ×{p['count']} on {len(p['pages'])} page(s)" + (f", kit: {p['kit']}" if p["kit"] else ", not in the kit" if p["candidate"] else "") + f" ({p['example']})")
    o.append("Kit components: " + ", ".join(A["kit_components"]))
    o += ["", "## Other accessibility findings"] + [f"- {i['issue']}: {i['count']}" + (f" ({i['note']})" if i["note"] else "") for i in A["issues"]]
    if A.get("css_scales"):
        o += ["", "## Color scales the site defines as CSS variables"] + [f"- {k}: " + ", ".join(f"{n} {h} ({ratio(h, '#ffffff'):.2f}:1)" for n, h in v)
              + (f". **{A['css_scale_order'][k]}**" if k in A["css_scale_order"] else "") for k, v in A["css_scales"].items()]
    if A.get("css_roles"):
        o += ["", "## Named color roles in the CSS"] + [f"- {k}: {v}" for k, v in A["css_roles"].items()]
    if A["css_variables"]:
        o += ["", f"## CSS variables ({A['css_variables_framework']} framework variables skipped)"] + [f"- {k}: {v}" for k, v in list(A["css_variables"].items())[:40]]
    return "\n".join(o)


# ── the intake doc ───────────────────────────────────────────────────

TABS = [("audit", "Audit"), ("proposal", "Proposal"), ("backlog", "Backlog")]     # the files, in the doc's tab order


def section(text, key, body, name="audit.md"):
    pat = re.compile(rf"(<!-- sync:begin {re.escape(key)} [^>]*-->\n)(.*?)(<!-- sync:end {re.escape(key)} -->)", re.S)
    if not pat.search(text):
        raise SystemExit(f"{name} has no generated section `{key}`. Start from the template.")
    return pat.sub(lambda m: m.group(1) + body.rstrip() + "\n" + m.group(3), text, count=1)


def page_list(A, idx):
    scanned = len([p for p in A["pages"] if p and "error" not in p])
    if idx and len(set(idx)) >= scanned > 2:
        return f"all {scanned}"
    return ", ".join(f"[{i + 1}]" for i in sorted(idx)) if idx else "–"


def render(root):
    A = json.loads((root / "intake" / "analysis.json").read_text())
    doc = root / "intake" / "audit.md"
    text = doc.read_text()
    if A.get("mode") == "guidelines":
        text = render_guidelines(root, A, text)
    else:
        text = render_site(root, A, text)
    doc.write_text(text)
    done = [doc.name]
    render_proposal(root, done)


def render_site(root, A, text):
    n_pages = len([p for p in A["pages"] if p and "error" not in p])
    one = n_pages == 1
    pages = "this page" if one else "these pages"
    src = [f"Measured from {A['site']} on {A['scanned_at'][:10]}. "
           + ("The site is a single page, so the audit covers all of it. Numbers in brackets in the audit and the backlog refer to it." if one else
              "One page of each kind of template, so the audit covers what the site is built from. Numbers in brackets in the audit and the backlog refer to these pages."), "",
           "| # | Page | Kind | Widths | Has a form |", "| --- | --- | --- | --- | --- |"]
    for i, p in enumerate(A["pages"]):
        if not p:
            continue
        if p.get("error"):
            src.append(f"| {i + 1} | {p['url']} | {p.get('kind') or '–'} | not scanned: {p['error']} | – |")
        else:
            title = (p.get("title") or p["url"]).split(" | ")[0].replace("|", "\\|")      # titles often carry " | Site name"
            src.append(f"| {i + 1} | [{title}]({p['url']}) | {p.get('kind') or '–'} | "
                       + ", ".join(f"{w}px" for w in sorted(p.get("widths") or [], reverse=True)) + f" | {'?' if p.get('has_form') is True else 'yes' if p.get('has_form') == 'fields' else p['has_form'] if p.get('has_form') else 'no'} |")
    if A.get("not_covered"):
        src += ["", "Not covered: " + A["not_covered"]]
    text = section(text, "sources", "\n".join(src))

    shown = A["palette"][:16]
    sampled = any(A.get("sampled", {}).get(k) for k in ("colors_text", "colors_bg", "colors_border"))
    if not sampled and len(shown) == len(A["palette"]) and not A.get("palette_rare"):
        intro = f"Every color the scan found on {pages} ({len(shown)}), with visually identical shades merged."
    else:
        left = []
        if len(shown) < len(A["palette"]):
            left.append(f"{len(A['palette']) - len(shown)} less-used colors")
        if A.get("palette_rare"):
            left.append(f"{A['palette_rare']} used only in a trace")
        if sampled:
            left.append("the scan's long tail of rarely used colors")
        intro = f"The {len(shown)} most-used colors on {pages}, with visually identical shades merged. Not shown: " + ", ".join(left) + "."
    pal = [intro, "", "| Color | Family | Used for | On white | Variants | Pages |", "| --- | --- | --- | --- | --- | --- |"]
    for c in shown:
        pal.append(f"| `{c['hex']}` | {c['family']} | {', '.join(c['roles'])} | {c['on_white']}:1 | {len(c['members'])} | {page_list(A, c['pages'])} |")
    text = section(text, "audit-palette", "\n".join(pal))

    con = [f"**Measured contrast:** {A['contrast']['failing_share']:.0%} of the text on {pages}, by character count, is below WCAG AA. "
           f"Another {A['contrast']['on_image_share']:.0%} sits on photos or video and can't be measured automatically; check those by eye.", ""]
    # pairs someone checked by eye and ruled out (text really on a photo or another fill, a disabled
    # control): intake/review.json {"contrast_ruled_out": [{"fg", "bg", "reason"}]}
    review = root / "intake" / "review.json"
    ruled = json.loads(review.read_text()).get("contrast_ruled_out", []) if review.exists() else []
    is_ruled = lambda f: next((r for r in ruled if r["fg"] == f["fg"] and r["bg"] == f["bg"]), None)
    kept = [f for f in A["contrast"]["fails"] if not is_ruled(f)]
    if A["contrast"]["fails"]:
        gone = sum(f["chars"] for f in A["contrast"]["fails"] if is_ruled(f))
        share = A["contrast"]["failing_share"] - gone / (A["contrast"].get("total_chars") or 1)
        A = {**A, "contrast": {**A["contrast"], "fails": kept, "failing_share": max(0, share)}}
    if kept:
        # one row per color pair: sizes, pages and characters merged
        pairs = {}
        for f in kept:
            g = pairs.setdefault((f["fg"], f["bg"]), {"ratio": f["ratio"], "need": f["need"], "sizes": set(), "pages": set(), "chars": 0, "example": f["example"]})
            g["sizes"].add(f["size"]); g["pages"] |= set(f["pages"]); g["chars"] += f["chars"]; g["need"] = max(g["need"], f["need"])
        rows = sorted(pairs.items(), key=lambda kv: -kv[1]["chars"])
        sizes = lambda ss: f"{min(ss):g}px" if len(ss) == 1 else f"{min(ss):g}–{max(ss):g}px"
        con += [(f"Every failing color pair found ({len(rows)})." if len(rows) <= 12 else f"The 12 failing color pairs with the most text, of {len(rows)}.") + " Pairs that pass aren't listed.", "",
                "| Text | On | Sizes | Ratio | Needs | Example | Pages |", "| --- | --- | --- | --- | --- | --- | --- |"]
        con += [f"| `{fg}` | `{bg}` | {sizes(g['sizes'])} | {g['ratio']}:1 | {g['need']:g}:1 | {g['example']} | {page_list(A, g['pages'])} |" for (fg, bg), g in rows[:12]]
    if ruled:
        con += ["", "Checked by eye and ruled out: " + "; ".join(f"`{r['fg']}` on `{r['bg']}` ({r['reason']})" for r in ruled) + "."]
    text = section(text, "audit-contrast", "\n".join(con))

    more = " at least" if A.get("sampled", {}).get("type") else ""
    ty = [f"A summary of the site's type. h1–h6 are its heading styles, measured on the pages or read from the site's own styles. "
          f"The other rows show the most typical size and weight of each kind of text. In all,{more} {A['distinct_sizes']} font sizes are used on desktop.", "",
          "| Style | Desktop | Mobile | Weight | Family |", "| --- | --- | --- | --- | --- |"]
    labels = {"body": "Body text", "small": "Small text", "label": "Labels", "link": "Links", "button": "Buttons"}
    for r in ["h1", "h2", "h3", "h4", "h5", "h6", "body", "small", "label", "link", "button"]:
        v = A["type_roles"]["desktop"].get(r)
        m = (A["type_roles"].get("mobile") or {}).get(r, {})
        if v or m:
            v = v or {}
            ty.append(f"| {labels.get(r, r)} | {('%gpx' % v['size']) if v.get('size') else '–'} | {('%gpx' % m['size']) if m.get('size') else '–'} | "
                      f"{v.get('weight') or m.get('weight') or '–'} | {v.get('family') or m.get('family') or '–'} |")
    text = section(text, "audit-type", "\n".join(ty))

    lay = [f"- **Spacing:** most used " + ", ".join(f"{v:g}px" for v, _ in A["spacing"]["top"][:8] if isinstance(v, float))
           + f". {A['spacing']['on_4px_grid']:.0%} of values sit on a 4px grid; {A['spacing']['on_kit_scale']:.0%} match the kit's scale.",
           "- **Corner radius:** " + ", ".join(f"{k}px ({n}×, e.g. `{ex}`)" for k, n, ex in A["radius"]["top"][:5]) + ".",
           "- **Borders:** " + ", ".join(f"{k}px ({n}×)" for k, n in A["borders"]["widths"][:4]) + ".",
           "- **Shadows:** " + ("; ".join(f"`{s}` ({n}×)" for s, n in A["shadows"][:3]) if A["shadows"] else "none") + ".",
           "- **Breakpoints in the CSS:** " + (", ".join(f"{k}px" for k, _ in A["breakpoints"]["site"][:6]) or "none found") + "."]
    text = section(text, "audit-layout", "\n".join(lay))

    comp = [f"Every pattern the scan looks for that appears on {pages}, plus button and input styles. "
            "Patterns it doesn't know to look for won't appear; the screenshots cover those.", "",
            "| Pattern | Seen | Pages | In the kit |", "| --- | --- | --- | --- |"]
    styled = [b for b in A["buttons"] if b["kind"] != "text only"]
    comp.append(f"| buttons | {len(styled)} filled or outlined styles, {len(A['buttons']) - len(styled)} text-only | {page_list(A, {p for b in A['buttons'] for p in b['pages']})} | Button |")
    comp.append(f"| text inputs | {len(A['inputs'])} distinct styles | {page_list(A, {p for i in A['inputs'] for p in i['pages']})} | Input |")
    for p in A["patterns"]:
        if p["pattern"] in NOT_COMPONENTS:
            continue
        comp.append(f"| {p['pattern']} | {p['count']}× | {page_list(A, p['pages'])} | {p['kit'] or '–'} |")
    comp += ["", "Detected from markup and class names, so treat counts as a guide: a class such as `stat` can match things that aren't statistics."]
    return section(text, "audit-patterns", "\n".join(comp))


def render_proposal(root, done):
    prop, pdoc = root / "intake" / "proposed-tokens.json", root / "intake" / "proposal.md"
    if prop.exists() and pdoc.exists():
        ptext = pdoc.read_text()
        for key, body in proposal_sections(root, prop).items():
            if f"sync:begin proposal-{key} " in ptext:
                ptext = section(ptext, f"proposal-{key}", body, "proposal.md")
            elif not body.startswith("No changes"):
                raise SystemExit(f"The proposal changes {key} tokens, but proposal.md has no `{key}` area. Add it from the template.")
        pdoc.write_text(ptext)
        done.append(pdoc.name)
    print(f"Updated intake/{' and intake/'.join(done)}.")


def export(root):
    """Clean copies for the doc: no frontmatter, prompts or sync markers."""
    out = root / "intake" / "publish"
    out.mkdir(exist_ok=True)
    for key, name in TABS:
        text = (root / "intake" / f"{key}.md").read_text()
        text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
        text = re.sub(r"<!--.*?-->\n?", "", text, flags=re.S)
        text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
        (out / f"{key}.md").write_text(text)
        print(f"intake/publish/{key}.md → the {name} tab ({len(text) // 1024 or 1} KB)")


# Section 2 goes area by area, so a designer can review and approve one at a time. Each token lands in
# an area by its tokens.json group; a group not listed here goes under Other.
AREAS = [("color", "Color", {"primitive-color", "semantic-color"}),
         ("type", "Typography", {"typography", "text-styles"}),
         ("shape", "Spacing and shape", {"spacing", "border-radius", "border-width", "icons"}),
         ("layout", "Layout", {"breakpoints", "grid-styles"}),
         ("other", "Other", None)]


def area_of(group):
    for key, _, groups in AREAS:
        if groups and group in groups:
            return key
    return "other"


def proposal_sections(root, prop):
    """{area key: markdown} for the generated part of each area in section 2."""
    import port
    p = port.plan(root, prop, offline=True)
    nodes = L.token_nodes(p["new_tokens"])
    where = lambda n: area_of(nodes[n][0]) if n in nodes else "other"
    out = {key: [] for key, _, _ in AREAS}

    def v(x):
        return "–" if x is None else f"`{x[1:]}`" if isinstance(x, str) and x.startswith("@") else f"`{x}`"

    # where each proposed primitive color comes from: the brand's own (a guidelines color, or one measured on the
    # site) or derived to fill a step the brand doesn't give
    known = {}
    an = root / "intake" / "analysis.json"
    if an.exists():
        A = json.loads(an.read_text())
        if A.get("mode") == "guidelines":
            known = {c["hex"].lower(): f"Source: {c['name']}" for c in A["colors"]}
        else:
            known = {m.lower(): "Source: the site" for c in A.get("palette", []) for m in c.get("members", [c["hex"]])}
            # the site's own CSS color variables, named by their variable
            for scale, steps in (A.get("css_scales") or {}).items():
                for step, h in steps:
                    known[h.lower()] = f"Source: `--{scale}-{step}`"
            for var, h in (A.get("css_roles") or {}).items():
                if isinstance(h, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", h):
                    known.setdefault(h.lower(), f"Source: `{var}`")

    def origin(x):
        return known.get(x.lower(), "Derived") if isinstance(x, str) and x.startswith("#") else "–"
    groups = {}
    for n in p["changes"] + [a for a in p["adds"] if p["after"][a].get("kind") == "var"]:
        b, a = p["before"].get(n, {}), p["after"][n]       # an addition has nothing before it
        kind = {"color": "Primitive colors" if nodes[n][0] == "primitive-color" else "Semantic colors", "fontFamily": "Typefaces", "fontWeight": "Weights"}.get(a.get("type")) \
            or ("Font sizes" if n.startswith("font-size/") else "Line heights" if n.startswith("line-height/")
                else "Paragraph spacing" if n.startswith("paragraph-spacing/") else nodes[n][0].replace("-", " ").capitalize())
        groups.setdefault((where(n), kind), []).append((n, b, a))
    for (area, kind), rows in groups.items():
        modes = sorted({m for _, b, a in rows for m in list(a.get("modes", {})) + list(b.get("modes", {}))})
        head = ["Token", "Now", "Proposed"] + (["Source or derived"] if kind == "Primitive colors" else [])
        if kind not in ("Primitive colors", "Semantic colors", "Typefaces"):     # modes only repeat the value for these
            head += sum(([f"{m} now", f"{m} proposed"] for m in modes), [])
        md = out[area]
        md += [f"**{kind}**", "", "| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
        for n, b, a in rows:
            cells = [f"`{n}`", v(b.get("value")) if b else "*new*", v(a.get("value"))]
            if kind == "Primitive colors":
                cells.append(origin(a.get("value")))
            elif len(head) > 3:
                cells += sum(([v(b.get("modes", {}).get(m)) if b else "*new*", v(a.get("modes", {}).get(m))] for m in modes), [])
            md.append("| " + " | ".join(cells) + " |")
        md.append("")
    if p["follow_rows"]:
        fam = ["content", "actions", "borders", "surfaces", "shadows"]
        out["color"] += ["**What this means for each role**", "",
                         "The semantic tokens components use point at the primitives above, so they change with them.", "",
                         "| Role | Now | Proposed | Comes from |", "| --- | --- | --- | --- |"]
        for r in sorted(p["follow_rows"], key=lambda r: (fam.index(r["token"].split("/")[0]) if r["token"].split("/")[0] in fam else 9, r["token"])):
            out["color"].append(f"| `{r['token']}` | {('`' + r['now'] + '`') if r['now'] else '–'} | `{r['proposed']}` | {('`' + r['from'] + '`') if r['from'] else '–'} |")
        out["color"].append("")
    # new styles have no single value for a table row, so they're listed
    for n, line in zip(p["adds"], p["add_lines"]):
        if p["after"][n].get("kind") != "var":
            out[where(n)].append(("**New styles**\n\n" if not any("**New styles**" in x for x in out[where(n)]) else "") + f"- {line}")
    for key in out:
        if out[key] and out[key][-1].startswith("- "):
            out[key].append("")
    fails = p["new_fails"] + p["add_fails"]
    if out["color"] or fails:
        out["color"] += (["**Contrast failures this proposal would cause**", ""] + [f"- {l}" for l in fails] + [""]) if fails \
            else ["Contrast: these colors cause no failures in any component.", ""]
    if p["fixed"]:
        out["color"] += ["Contrast failures they fix: " + ", ".join(p["fixed"]), ""]
    for w in p["add_warns"]:
        named = next((n for n in p["adds"] if f"`{n}`" in w or f"`{n.rsplit('/', 1)[0]}`" in w), None)
        out[where(named) if named else "other"] += [f"- **Warning:** {w}", ""]
    if p["fonts"]:
        out["type"] += ["Fonts the text styles will need in Figma: " + ", ".join(f"{f['family']} {f['weight']}" for f in p["fonts"]) + ".", ""]
    for key in out:
        out[key] = "\n".join(out[key]).rstrip() if out[key] else "No changes proposed: the system's values stay."
    return out


# ── Tablet sizes ─────────────────────────────────────────────────────

HEADINGS = ["heading-xs", "heading-s", "heading-m", "heading-l", "heading-xl", "heading-xxl"]   # smallest first


def _even(x):
    return int(2 * round(x / 2))


def _round_rule(x):
    """size × ratio to the nearest whole pixel, halves up (render.py's rule)."""
    x = round(x, 6)
    return int(x) if x == int(x) else int(x + 0.5)


def tablet_plan(root, data):
    """Tablet values for every type step, from the proposal's Desktop and Mobile sizes.

    Each step keeps the kit's own Tablet-to-Desktop ratio (54/60 for heading-xxl in the starter kit),
    rounded to an even pixel, kept between Mobile and Desktop, and at least 2px above the next
    heading down. Line height and paragraph spacing follow the step's ratios.
    Returns {} when the system has no Tablet mode. {step: {"font-size": n, "line-height": n, "paragraph-spacing": n}}"""
    tokens = json.loads((root / "tokens.json").read_text())
    ty = tokens["typography"]
    if "Tablet" not in ty["$extensions"]["figma"]["modes"]:
        return {}
    kv = lambda g, s, m=None: (ty[g][s]["$value"]["value"] if isinstance(ty[g][s]["$value"], dict) else ty[g][s]["$value"]) if m is None \
        else (lambda x: x["value"] if isinstance(x, dict) else x)(ty[g][s]["$extensions"]["figma"]["modes"][m])
    pv = lambda g, s, m=None: ((data.get("typography", {}).get(g, {}).get(s) or {}).get("$value") if m is None else
                               ((data.get("typography", {}).get(g, {}).get(s) or {}).get("$extensions", {}).get("figma", {}).get("modes", {}).get(m)))
    steps = [k for k in ty["font-size"] if not k.startswith("$")]
    ratio_names = {g: [k for k, n in ty[g].items() if not k.startswith("$") and n["$type"] == "number"] for g in ("line-height", "paragraph-spacing")}

    def ratio(g, s):        # the step's named ratio: proposed, else the system's
        desc = ty[g][s].get("$description", "")
        named = [r for r in ratio_names[g] if f"{g}/{r}." in desc or f"{g}/{r} " in desc]
        if len(named) != 1:
            return None
        return pv(g, named[0]) or ty[g][named[0]]["$value"]

    out, prev = {}, None
    for s in [h for h in HEADINGS if h in steps] + [x for x in steps if x not in HEADINGS]:
        d, m = pv("font-size", s) or kv("font-size", s), pv("font-size", s, "Mobile") or kv("font-size", s, "Mobile")
        r = kv("font-size", s, "Tablet") / kv("font-size", s)
        t = d if r >= 1 else _even(d * r)
        t = max(min(t, d), m)
        if s in HEADINGS and prev is not None and t < prev + 2:
            t = min(prev + 2, d)
        prev = t if s in HEADINGS else prev
        out[s] = {"font-size": t}
        for g in ("line-height", "paragraph-spacing"):
            rr = ratio(g, s)
            if rr:
                out[s][g] = _round_rule(t * rr)
    return out


def _ratio(root, data, g, s):
    """The step's named ratio for g (line-height or paragraph-spacing): proposed, else the system's."""
    ty = json.loads((root / "tokens.json").read_text())["typography"]
    names = [k for k, n in ty[g].items() if not k.startswith("$") and n["$type"] == "number"]
    desc = ty[g][s].get("$description", "")
    named = [r for r in names if f"{g}/{r}." in desc or f"{g}/{r} " in desc]
    if len(named) != 1:
        return None
    return (data.get("typography", {}).get(g, {}).get(named[0]) or {}).get("$value") or ty[g][named[0]]["$value"]


def ramp_targets(root, data):
    """What every proposed step should hold: {step: {group: {"": desktop, mode: value, ...}}}.
    Sizes are the proposal's (Tablet from the rule when it gives none); line height and
    paragraph spacing are size × the step's ratio at every mode."""
    ty = json.loads((root / "tokens.json").read_text())["typography"]
    modes = [m for m in ty["$extensions"]["figma"]["modes"] if m != ty["$extensions"]["figma"]["defaultMode"]]
    tab = tablet_plan(root, data)
    fs = data.get("typography", {}).get("font-size", {})
    kmode = lambda s, m: (lambda x: x["value"] if isinstance(x, dict) else x)(ty["font-size"][s]["$extensions"]["figma"]["modes"][m])
    out = {}
    for s, node in fs.items():
        pm = node.get("$extensions", {}).get("figma", {}).get("modes", {})
        size = {"": node["$value"]}
        for m in modes:
            size[m] = pm.get(m) if m in pm else (tab[s]["font-size"] if m == "Tablet" and tab else kmode(s, m))
        out[s] = {"font-size": size}
        for g in ("line-height", "paragraph-spacing"):
            r = _ratio(root, data, g, s)
            if r:
                out[s][g] = {m: _round_rule(v * r) for m, v in size.items()}
    return out


def ramp_problems(root, data):
    """(group/step, what's wrong) for every proposed step whose ramp isn't complete and consistent."""
    probs = []
    for s, groups in ramp_targets(root, data).items():
        for g, want in groups.items():
            node = data.get("typography", {}).get(g, {}).get(s)
            have = {"": node.get("$value")} if node else {}
            if node:
                have.update(node.get("$extensions", {}).get("figma", {}).get("modes", {}))
            for m, v in want.items():
                if m not in have:
                    probs.append((f"{g}/{s}", f"no {m or 'Desktop'} value"))
                elif g != "font-size" and have[m] != v:
                    probs.append((f"{g}/{s}", f"{m or 'Desktop'} is {have[m]}, its ratio gives {v}"))
    return probs


def fill_ramp(root):
    """Complete the proposal's type ramp: Tablet sizes by the rule, and line height and paragraph
    spacing from the ratios, at every mode. Only fills what's missing; never changes a value."""
    prop = root / "intake" / "proposed-tokens.json"
    data = json.loads(prop.read_text())
    added = 0
    for s, groups in ramp_targets(root, data).items():
        for g, want in groups.items():
            node = data["typography"].setdefault(g, {}).setdefault(s, {})
            if "$value" not in node:
                node["$value"] = want[""]; added += 1
            modes = node.setdefault("$extensions", {}).setdefault("figma", {}).setdefault("modes", {})
            for m, v in want.items():
                if m and m not in modes:
                    modes[m] = v; added += 1
    prop.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    ty = json.loads((root / "tokens.json").read_text())["typography"]
    modes = ty["$extensions"]["figma"]["modes"]
    print("| Step | " + " | ".join(modes) + " |\n| --- |" + " --- |" * len(modes))
    for s, node in data["typography"]["font-size"].items():
        mm = node.get("$extensions", {}).get("figma", {}).get("modes", {})
        print(f"| {s} | " + " | ".join(str(node["$value"] if m == ty["$extensions"]["figma"]["defaultMode"] else mm.get(m)) for m in modes) + " |")
    left = ramp_problems(root, data)
    print(f"\nAdded {added} value(s) to intake/proposed-tokens.json." + (" Review the Tablet sizes with the rest of the Typography area." if "Tablet" in modes else ""))
    if left:
        print("Still inconsistent (values the proposal set itself; fix them by hand):\n" + "\n".join(f"- `{n}`: {w}" for n, w in left))


def check(root):
    import port
    prop = root / "intake" / "proposed-tokens.json"
    if not prop.exists():
        raise SystemExit("No intake/proposed-tokens.json yet.")
    # font sizes and line heights are proposed in whole pixels, at every mode
    data = json.loads(prop.read_text())
    frac = []
    for group in ("font-size", "line-height"):
        for name, t in data.get("typography", {}).get(group, {}).items():
            vals = [t.get("$value")] + list(t.get("$extensions", {}).get("figma", {}).get("modes", {}).values())
            if any(isinstance(v, float) and v != int(v) for v in vals):
                frac.append(f"{group}/{name} ({', '.join(str(v) for v in vals)})")
    if frac:
        raise SystemExit("Round these to whole pixels first: " + "; ".join(frac))
    # every proposed step needs its whole ramp: a value at every mode, line height and paragraph spacing
    # by their ratios. Anything left out would keep the system's old value through the port.
    probs = ramp_problems(root, data)
    if probs:
        raise SystemExit("The type ramp is incomplete. Run `intake.py ramp` to fill what's missing, then fix anything it lists:\n"
                         + "\n".join(f"- `{n}`: {w}" for n, w in probs[:30]) + (f"\n- …and {len(probs) - 30} more" if len(probs) > 30 else ""))
    p = port.plan(root, prop, offline=True)
    print(port.describe(p, prop, root))
    doc = root / "intake" / "proposal.md"
    if doc.exists():
        sec = doc.read_text()
        areas = re.findall(r"^- \[( |x)\] \*\*(.+?) approved\*\*", sec, re.M)
        questions = [q for block in re.findall(r"\*\*Open questions\*\*\n\n((?:- .*\n?)+)", sec)
                     for q in re.findall(r"^- \*\*(.+?):\*\*", block, re.M)]
        print("\nReview: " + ", ".join(f"{name} {'approved' if mark == 'x' else 'open'}" for mark, name in areas) + ".")
        if questions:
            print("Open questions: " + ", ".join(questions) + ".")
        if areas and all(m == "x" for m, _ in areas) and not questions:
            print("Every area is approved. The proposal is ready for /port-tokens.")


# ── main ─────────────────────────────────────────────────────────────

def prepare(root, url, pages, n, client, not_covered=None):
    intake = root / "intake"
    intake.mkdir(exist_ok=True)
    for f in ("scan-raw.json", "scan.json", "analysis.json"):
        (intake / f).unlink(missing_ok=True)
    nonce = secrets.token_hex(6)
    origin = re.match(r"https?://[^/]+", url).group(0)
    inp = {"nonce": nonce, "widths": [1440, 390], "mobile_pages": 4}
    if pages:
        given = []
        for p in pages:             # "kind=URL" or a bare URL
            kind, _, u = p.partition("=") if not p.startswith("http") else ("", "", p)
            given.append({"url": u.strip(), "kind": kind.strip()})
        if url not in [g["url"] for g in given]:
            given.insert(0, {"url": url, "kind": "home"})
        inp["pages"] = given
    else:
        inp["auto_pages"] = n
    js = (HERE / "scan.template.js").read_text().replace("__INPUT__", json.dumps(inp))
    (intake / "scan.js").write_text(js)
    (intake / "scan-tag.json").write_text(json.dumps({"nonce": nonce, "url": url, "origin": origin, "client": client, "not_covered": not_covered}) + "\n")
    for key, _ in TABS:
        doc = intake / f"{key}.md"
        if not doc.exists():
            t = (HERE.parent / "templates" / f"{key}.md").read_text()
            doc.write_text(t.replace("{{client}}", client or origin.split("//")[1]).replace("{{date}}", datetime.date.today().isoformat()).replace("{{url}}", url))
    print(f"Wrote intake/scan.js ({len(js) // 1024} KB, tag {nonce}). Open {url} in the browser, run the whole script there, "
          "then run `intake.py collect`.")


def prepare_guidelines(root, client):
    if not client:
        raise SystemExit("prepare --guidelines needs --client.")
    intake = root / "intake"
    intake.mkdir(exist_ok=True)
    for f in ("scan-raw.json", "scan.json", "scan.js", "scan-tag.json", "analysis.json"):
        (intake / f).unlink(missing_ok=True)
    today = datetime.date.today().isoformat()
    for key, _ in TABS:
        doc = intake / f"{key}.md"
        if not doc.exists():
            t = (HERE.parent / "templates" / ("audit-guidelines.md" if key == "audit" else f"{key}.md")).read_text()
            doc.write_text(t.replace("{{client}}", client).replace("{{date}}", today).replace("{{url}}", "brand guidelines"))
    g = intake / "guidelines.json"
    if not g.exists():
        g.write_text(json.dumps({"client": client, "date": today, "sources": [], "colors": [], "pairs": [], "type": [], "sizes": []}, indent=1) + "\n")
    print("Wrote the three files and intake/guidelines.json. Fill guidelines.json from the guidelines, then run `intake.py analyse`.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["prepare", "collect", "analyse", "check", "ramp", "render", "export"])
    ap.add_argument("--root")
    ap.add_argument("--url")
    ap.add_argument("--page", action="append", default=[])
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--client")
    ap.add_argument("--guidelines", action="store_true", help="no site: audit the brand guidelines alone, from intake/guidelines.json")
    ap.add_argument("--not-covered", help="what the scan can't reach, such as a news site on another domain")
    a = ap.parse_args()
    root = L.find_root(a.root)
    if a.command == "prepare":
        if a.guidelines:
            prepare_guidelines(root, a.client)
        elif not a.url:
            raise SystemExit("prepare needs --url, or --guidelines when there's no site.")
        else:
            prepare(root, a.url, a.page, a.pages, a.client, a.not_covered)
    elif a.command == "collect":
        tag = json.loads((root / "intake" / "scan-tag.json").read_text())
        got = find_result(root, tag["nonce"])
        (root / "intake" / "scan.json").write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
        ok = [p for p in got["pages"] if p and "error" not in p]
        print(f"Saved intake/scan.json: {len(ok)} of {len(got['pages'])} pages scanned. Run `intake.py analyse`.")
    elif a.command == "analyse":
        guided = not (root / "intake" / "scan.json").exists() and (root / "intake" / "guidelines.json").exists()
        A = analyse_guidelines(root) if guided else analyse(root)
        (root / "intake" / "analysis.json").write_text(json.dumps(A, indent=1, ensure_ascii=False) + "\n")
        print(summary_guidelines(A) if guided else summary(A))
    elif a.command == "check":
        check(root)
    elif a.command == "ramp":
        fill_ramp(root)
    elif a.command == "export":
        export(root)
    else:
        render(root)


if __name__ == "__main__":
    main()
