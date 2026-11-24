"""Compare every place with the snapshot and write the sync report. Changes nothing.

  python3 -I check.py [--root DIR] [--no-figma]

Needs .sync/figma-read.json from the Figma read (see SKILL.md), unless --no-figma.
Writes .sync/last-check.md and prints it.
Exit code: 0 everything agrees · 1 changes to sync · 2 problems to fix first.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synclib as L

MAX_LINES = 6  # detail lines per item before "and N more"


# ── describing a change ──────────────────────────────────────────────

def short(v):
    if isinstance(v, str):
        return v if len(v) <= 60 else v[:57] + "…"
    return json.dumps(v, ensure_ascii=False)


def token_diff(old, new):
    if old is None:
        return ["new"]
    if new is None:
        return ["deleted"]
    out = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if L.canon(a) == L.canon(b):
            continue
        if k == "description":
            out.append("description edited")
        elif k == "modes":
            for m in sorted(set(a or {}) | set(b or {})):
                if L.canon((a or {}).get(m)) != L.canon((b or {}).get(m)):
                    out.append(f"{m} mode: {short((a or {}).get(m))} → {short((b or {}).get(m))}")
        else:
            out.append(f"{k}: {short(a)} → {short(b)}")
    return out


def binding_diff(variant, old, new):
    if old is None:
        return [f"new variant {variant}"]
    if new is None:
        return [f"variant {variant} removed"]
    out = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a != b:
            out.append(f"{variant}: `{k}` {short(a) if a is not None else '(none)'} → {short(b) if b is not None else '(none)'}")
    return out


def capped(lines):
    if len(lines) <= MAX_LINES:
        return lines
    return lines[:MAX_LINES] + [f"and {len(lines) - MAX_LINES} more"]


def decide(fig, md, same=False, fig_action="", md_action=""):
    if fig and md:
        return ("Both", "Nothing to do; they match" if same else "**Conflict: you choose**")
    if fig:
        return ("Figma", fig_action)
    return ("Markdown", md_action)


# ── main ─────────────────────────────────────────────────────────────

def analyse(root, no_figma=False):
    """Compare every place with the snapshot. Returns the findings; writes nothing."""
    cfg = L.load_config(root)
    places = ["figma", "markdown"]      # every project keeps these two; code joins once the kit has a library
    snap = json.loads((root / ".sync" / "snapshot.json").read_text())

    problems, warnings, notes, rows = [], [], [], []

    # Figma read
    fig = None
    if "figma" in places and not no_figma:
        fpath = root / ".sync" / "figma-read.json"
        if not fpath.exists():
            raise SystemExit("No .sync/figma-read.json. Run figma_read.py prepare, run the script in Figma, then figma_read.py compare.")
        fig = json.loads(fpath.read_text())["ds_sync_figma_read"]
        if fig.get("snapshot") != snap.get("synced_at"):
            raise SystemExit("The Figma read was made against a different snapshot. Run figma_read.py prepare and read Figma again.")
        if fig.get("file_key") and fig["file_key"] != (cfg.get("figma") or {}).get("file_key"):
            problems.append(f"The Figma read came from file `{fig['file_key']}`, but the config names `{cfg['figma']['file_key']}`.")

    # ── tokens ──
    tokens_json = json.loads((root / "tokens.json").read_text())
    local = L.projections(tokens_json, cfg)
    base = L.snapshot_projections(snap["tokens"], cfg)
    md_tok = {n: local.get(n) for n in set(local) | set(base) if L.canon(local.get(n)) != L.canon(base.get(n))}
    # a token renamed in tokens.json keeps its Figma id
    local_ids = {n: (e[2].get("$extensions", {}).get("figma", {}).get("id")) for n, e in L.token_nodes(tokens_json).items()}
    base_by_id = {e["id"]: n for n, e in snap["tokens"].items() if e.get("id")}
    md_renamed = {}
    for n in [n for n in md_tok if n in local and n not in base]:
        old = base_by_id.get(local_ids.get(n))
        if old and old not in local and old in md_tok:
            md_renamed[old] = n
            del md_tok[old]
            if L.canon(L.rename_refs(base[old], {old: n})) == L.canon(local[n]):
                del md_tok[n]
    if md_renamed:      # references that only follow a rename aren't changes of their own
        for n in [n for n in md_tok if n in local and n in base]:
            if L.canon(L.rename_refs(base[n], md_renamed)) == L.canon(local[n]):
                del md_tok[n]
    for old, new in md_renamed.items():
        rows.append(("Token", f"`{old}`", "Markdown", f"renamed to `{new}`", "Rename the Figma variable or style"))
    fig_tok, renamed = {}, {}
    if fig:
        t = fig["tokens"]
        fig_tok.update(t["changed"]); fig_tok.update(t["added"])
        for n in t["removed"]:
            fig_tok[n] = None
        renamed = t.get("renamed", {})
    for old, new in renamed.items():
        rows.append(("Token", f"`{old}`", "Figma", f"renamed to `{new}`", "Rename it in tokens.json and every markdown file"))
    tok = {}
    for n in sorted(set(md_tok) | set(fig_tok)):
        in_f, in_m = n in fig_tok, n in md_tok
        same = in_f and in_m and L.canon(fig_tok[n]) == L.canon(md_tok[n])
        was = base.get(n) or base.get({v: k for k, v in md_renamed.items()}.get(n))
        tok[n] = {"figma": in_f, "markdown": in_m, "same": same, "figma_new": fig_tok.get(n), "markdown_new": md_tok.get(n), "base": was}
        newer = fig_tok[n] if in_f else md_tok[n]
        detail = token_diff(was, newer)
        if in_f and in_m and not same:
            detail = [f"Figma: {', '.join(token_diff(base.get(n), fig_tok[n]))}",
                      f"Markdown: {', '.join(token_diff(base.get(n), md_tok[n]))}"]
        md_action = ("Delete the Figma variable or style" if in_m and md_tok[n] is None else
                     "Create the Figma variable or style" if in_m and was is None else "Update the Figma variable or style")
        where, action = decide(in_f, in_m, same, "Update tokens.json, then regenerate foundation sections", md_action)
        kind = "Style" if (newer or base.get(n) or {}).get("kind") in ("text", "effect", "grid") else "Token"
        rows.append((kind, f"`{n}`", where, "; ".join(capped(detail)), action))

    # ── icons ──
    icons_json = json.loads((root / "icons.json").read_text())
    loc_icons = {i["name"]: {"figma_node": i["figmaNode"], "glyph": i["glyph"]} for i in icons_json["icons"]}
    base_icons = snap["icons"]["icons"]
    if icons_json["library"] != snap["icons"]["library"]:
        rows.append(("Icons", "library", "Markdown", f"{snap['icons']['library'].get('name')} {snap['icons']['library'].get('version')} → {icons_json['library'].get('name')} {icons_json['library'].get('version')}", "Redraw every icon in Figma from the new glyphs"))
    for n in sorted(set(loc_icons) | set(base_icons)):
        a_, b_ = base_icons.get(n), loc_icons.get(n)
        if a_ == b_:
            continue
        what = "new" if a_ is None else "deleted" if b_ is None else f"glyph {a_['glyph']} → {b_['glyph']}" if a_["glyph"] != b_["glyph"] else "node changed"
        rows.append(("Icon", f"`{n}`", "Markdown", what, "Update the icon in Figma"))
    if fig:
        for n in fig["icons"]["removed"]:
            rows.append(("Icon", f"`{n}`", "Figma", "deleted", "Remove it from icons.json and iconography.md"))
        for i in fig["icons"]["added"]:
            rows.append(("Icon", f"`{i['name']}`", "Figma", "new", "Add it to icons.json with its glyph"))
        for n, node in fig["icons"]["moved"].items():
            rows.append(("Icon", f"`{n}`", "Figma", f"now node {node}", "Update its node in icons.json"))

    # ── markdown files: which changed, and how ──
    files_now = {str(p.relative_to(root)): L.file_record(p) for p in L.markdown_files(root)}
    files_base = snap["places"]["markdown"].get("files", {})
    changed_files = {}
    for rel in sorted(set(files_now) | set(files_base)):
        n_, b_ = files_now.get(rel), files_base.get(rel)
        if n_ and b_ and n_["hash"] == b_["hash"]:
            continue
        if not b_:
            changed_files[rel] = "new"
        elif not n_:
            changed_files[rel] = "deleted"
        else:
            gen = sorted(k for k in set(n_.get("sections", {})) | set(b_.get("sections", {}))
                         if n_.get("sections", {}).get(k) != b_.get("sections", {}).get(k))
            if gen and md_renamed and rel.endswith(".md"):
                gen = L.sections_beyond_renames((root / rel).read_text(), b_.get("sections"), md_renamed)
            changed_files[rel] = gen or "hand-written"

    # which component each markdown file covers
    comp_file = {}
    for p in sorted((root / "components").glob("*.md")):
        text = p.read_text()
        rel = str(p.relative_to(root))
        name = L.frontmatter(text).get("name")
        if name:
            comp_file[name] = rel
        for i in L.parse_internal(text):
            comp_file.setdefault(i["name"], rel)

    # ── components ──
    comps = {}
    if fig:
        for n, diff in sorted(fig["components"]["changed"].items()):
            base_e = snap["components"].get(n, {})
            lines = []
            if n in fig["components"].get("adopted", {}):
                lines.append("new: its file joins sync")
            if "name" in diff:
                lines.append(f"renamed to {diff['name']}")
            if "description" in diff:
                lines.append("description edited")
            if "properties" in diff:
                old_p, new_p = base_e.get("properties", {}), diff["properties"]
                for k in sorted(set(old_p) | set(new_p)):
                    if L.canon(old_p.get(k)) != L.canon(new_p.get(k)):
                        lines.append(f"property {k}: {short(old_p.get(k))} → {short(new_p.get(k))}")
            if "default_variant" in diff:
                lines.append(f"default variant → {diff['default_variant']}")
            for k, v in diff.items():
                if k.startswith("variant:"):
                    var = k[len("variant:"):]
                    lines += binding_diff(var, base_e.get("bindings", {}).get(var), v)
            rel = comp_file.get(n)
            md_gen = rel and isinstance(changed_files.get(rel), list)
            where, action = decide(True, bool(md_gen), False,
                                   f"Update the generated sections of `{rel}`" if rel else "Add a markdown file for it", "")
            comps[n] = {"diff": diff, "file": rel, "lines": list(lines), "md_gen_edited": changed_files[rel] if md_gen else []}
            if md_gen:
                lines.append(f"`{rel}` generated sections also edited: {', '.join(changed_files[rel])}")
            rows.append(("Component", f"`{n}`", where, "; ".join(capped(lines)), action))
        for n in fig["components"]["removed"]:
            rel = comp_file.get(n)
            problems.append(f"Component `{n}` is gone from Figma" + (f", but `{rel}` still documents it." if rel else "."))
        for c in fig["components"]["added"]:
            warnings.append(f"New in Figma with no markdown file: **{c['name']}** (`{c['node']}`, {c['page']}, {c['variants']} variants). /build-component or a hand-written file covers it; sync doesn't create component files.")
        for node in fig["missing_nodes"]:
            refs = [rel for rel in files_now if rel.endswith(".md") and node in (root / rel).read_text()]
            problems.append(f"Figma node `{node}` no longer exists. Named in: {', '.join(f'`{r}`' for r in refs) or 'a markdown file'}.")

    # ── the Introduction page → design.md ──
    intro = None
    intro_page = (cfg.get("figma") or {}).get("intro_page")
    if fig and intro_page:
        now_i = fig.get("intro")
        if now_i is None:
            problems.append(f"The config's figma.intro_page (`{intro_page}`) isn't a page in this Figma file.")
        else:
            path = root / L.DESIGN_FILE
            text = path.read_text() if path.exists() else None
            md_edited = isinstance(changed_files.get(L.DESIGN_FILE), list)
            if snap.get("intro") is None:
                done_n = sum(1 for x in now_i["sections"] if not L._blank(x["body"]))
                lines = [f"new: design.md joins sync ({len(now_i['sections'])} sections, {done_n} written)"]
            else:
                lines = L.intro_diff(snap["intro"], now_i)
            if text is None and not lines:
                lines = ["`design.md` is missing"]
            if lines:
                where, action = decide(True, md_edited, False, f"Rewrite the generated block of `{L.DESIGN_FILE}`", "")
                rows.append(("Introduction", f"`{L.DESIGN_FILE}`", where, "; ".join(capped(lines)), action))
            intro = {"figma": now_i, "lines": lines, "md_edited": md_edited}

    # component files edited without a Figma change
    fig_changed = set(fig["components"]["changed"]) if fig else set()
    for rel, how in changed_files.items():
        if not rel.startswith("components/"):
            continue
        names = [n for n, f in comp_file.items() if f == rel]
        if any(n in fig_changed for n in names):
            continue
        if how == "new":
            notes.append(f"`{rel}` is new since the last sync.")
        elif how == "deleted":
            problems.append(f"`{rel}` was deleted, but the snapshot still has its component.")
        elif how == "hand-written":
            notes.append(f"`{rel}`: hand-written edits only. Nothing to sync.")
        else:
            rest = [k for k in how if k != "summary"]
            if "summary" in how:
                rows.append(("Component", f"`{names[0] if names else rel}`", "Markdown", "summary edited", "Set it as the Figma component's description"))
            if rest:
                warnings.append(f"`{rel}`: generated sections edited by hand ({', '.join(rest)}). Sync would overwrite them from Figma; make the change in Figma instead.")

    # foundation and other files
    render = root / "_scripts" / "render.py"
    stale = {}
    if render.exists():
        r = subprocess.run([sys.executable, "-I", str(render), str(root), "--check"], capture_output=True, text=True)
        try:
            stale = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            warnings.append(f"render.py --check failed: {r.stderr.strip()[:200]}")
    # the foundations' own checks (type ramp, spacing order, grid fit), worked out from tokens.json
    fails = {}
    if render.exists():
        r = subprocess.run([sys.executable, "-I", str(render), str(root), "--fails"], capture_output=True, text=True)
        try:
            fails = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            warnings.append(f"render.py --fails failed: {r.stderr.strip()[:200]}")
    for rel, keys in stale.items():
        rows.append(("Foundation", f"`{rel}`", "Markdown", f"generated sections out of date with tokens.json: {', '.join(keys)}", "Regenerate with render.py"))
    for rel, how in changed_files.items():
        if rel.startswith("components/") or rel in ("tokens.json", "icons.json") or rel in stale:
            continue
        if rel == L.DESIGN_FILE and intro_page:
            if isinstance(how, list) and not (intro and intro["lines"]):
                warnings.append(f"`{rel}`: its generated block was edited by hand. Sync rewrites it from the Introduction page in Figma; make the change there.")
            elif how == "hand-written":
                notes.append(f"`{rel}`: hand-written edits only. Nothing to sync.")
            continue
        if how == "new":
            notes.append(f"`{rel}` is new since the last sync.")
        elif how == "deleted":
            notes.append(f"`{rel}` was deleted since the last sync.")
        elif how == "hand-written":
            notes.append(f"`{rel}`: hand-written edits only. Nothing to sync.")
        else:
            warnings.append(f"`{rel}`: generated sections edited by hand ({', '.join(how)}). They'd be regenerated from tokens.json.")

    # ── link checks ──
    names = set(local)
    for rel in sorted(files_now):
        if not rel.endswith(".md") or not (rel.startswith("components/") or rel.startswith("foundations/")):
            continue
        text = (root / rel).read_text()
        fm = L.frontmatter(text)
        for key in ("tokens", "styles"):
            for t in fm.get(key) or []:
                if t.endswith(".*"):
                    continue
                if t not in names and not L.excluded(t, cfg):
                    problems.append(f"`{rel}` lists {key[:-1]} `{t}`, which isn't in tokens.json.")
        body = text.split("\n---\n", 1)[-1]
        unknown = set()
        for m in re.finditer(r"`([^`\n]+)`", body):
            s = m.group(1)
            looks = re.fullmatch(r"[a-z][a-z0-9-]*(/[a-z0-9-]+)+", s) or re.fullmatch(r"(space|radius|border-width|icon-size|icon-stroke|breakpoint|container)-[a-z0-9-]*|container-max|radius-full|icon-stroke", s) \
                or re.fullmatch(r"(Headings|Paragraph|UI|Utility|Elevation|Effects)/[A-Za-z -]+", s)
            if looks and s not in names and not L.excluded(s, cfg):
                unknown.add(s)
        if unknown:
            warnings.append(f"`{rel}` mentions names that aren't tokens or styles: {', '.join(f'`{u}`' for u in sorted(unknown))}.")

    # ── contrast, from every component's bindings ──
    figma_comps = json.loads(json.dumps(snap["components"]))
    for n, diff in (fig["components"]["changed"].items() if fig else []):
        bind = figma_comps.setdefault(n, {"bindings": {}})["bindings"]
        for k, v in diff.items():
            if k.startswith("variant:"):
                if v is None:
                    bind.pop(k[len("variant:"):], None)
                else:
                    bind[k[len("variant:"):]] = v
    figma_proj = dict(base)
    for n, v in fig_tok.items():
        if v is None:
            figma_proj.pop(n, None)
        else:
            figma_proj[n] = v
    md_pairs = L.contrast_pairs(snap["components"], local)
    contrast = contrast_report(md_pairs, L.contrast_pairs(figma_comps, figma_proj) if fig else None)

    # hand-written text that tokens.json's own changes have made wrong
    if md_tok:
        texts = {rel: (root / rel).read_text() for rel in files_now if rel.endswith(".md")}
        review = L.prose_review(texts, comp_file, {"proj": base, "pairs": L.contrast_pairs(snap["components"], base)},
                                {"proj": local, "pairs": md_pairs})
        for rel, items in review.items():
            warnings.append(f"`{rel}` has hand-written text tokens.json's changes may have made wrong: "
                            + "; ".join(f"line {i}: {why}" if i else why for i, _, why in items) + ".")

    # hand-written text that disagrees with tokens.json as it stands: figures and values beside token names
    texts = {rel: (root / rel).read_text() for rel in files_now if rel.endswith(".md")}
    stale_text = L.stale_text(texts, local)

    # colors stored as another color at an opacity, left behind when their source changed
    derived = L.stale_derived(json.loads((root / "tokens.json").read_text()))
    if derived:
        warnings.append("tokens.json stores out-of-date values for colors that follow another color: "
                        + ", ".join(f"`{n}` is {had}, but `{src}` is now {now}" for n, src, had, now in derived)
                        + ". Figma already shows the new color; the next sync apply rewrites these values.")

    return {"root": root, "cfg": cfg, "places": places, "snap": snap, "fig": fig, "problems": problems, "stale_text": stale_text, "intro": intro, "foundation_fails": fails,
            "warnings": warnings, "notes": notes, "rows": rows, "tokens": tok, "renamed": renamed,
            "local_tokens": local, "components": comps, "comp_file": comp_file, "changed_files": changed_files,
            "stale": stale, "no_figma": no_figma, "contrast": contrast,
            "figma_components": figma_comps, "base_tokens": base, "md_renamed": md_renamed}


def contrast_report(md_pairs, fig_pairs):
    """Failing pairs, grouped by colors, saying where each fails. fig_pairs is None when Figma wasn't read."""
    fails = {}
    for place, pairs in (("Markdown", md_pairs), ("Figma", fig_pairs or [])):
        for x in pairs:
            if x["ratio"] >= x["need"]:
                continue
            f = fails.setdefault((x["fg"], x["bg"], x["kind"], x["need"]), {"ratio": x["ratio"], "where": {}, "places": set()})
            f["ratio"] = min(f["ratio"], x["ratio"])
            f["places"].add(place)
            f["where"].setdefault(x["where"].split(" › ")[0], set()).add(place)
    both = fig_pairs is not None
    lines = []
    for (fg, bg, kind, need), f in sorted(fails.items(), key=lambda kv: kv[1]["ratio"]):
        if not both:
            where = "everywhere"
        elif len(f["places"]) == 2:
            where = "in both places"
        else:
            where = f"in {next(iter(f['places']))} only"
        used = sorted(f["where"])
        shown = ", ".join(used[:4]) + (f" and {len(used) - 4} more" if len(used) > 4 else "")
        lines.append(f"`{fg}` on {bg} ({kind}): {f['ratio']:.2f}:1, needs {need:g}:1, {where}. Used by {shown}.")
    checked = len({(x["fg"], x["bg"], x["kind"]) for x in md_pairs + (fig_pairs or [])})
    return {"fails": lines, "checked": checked}


def report(A):
    root, snap, fig, places = A["root"], A["snap"], A["fig"], A["places"]
    problems, warnings, notes, rows = A["problems"], A["warnings"], A["notes"], A["rows"]
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    conflicts = sum(1 for r in rows if "Conflict" in r[4])
    if problems:
        headline = f"**{len(problems)} problem{'s' if len(problems) != 1 else ''} to fix before syncing.** Sync stops until links are sound."
    elif rows:
        headline = f"**{len(rows)} change{'s' if len(rows) != 1 else ''} since the last sync**" + (f", {conflicts} needing your decision." if conflicts else ". Sync can carry them across.")
    elif not A["stale_text"]:
        headline = "**Everything agrees with the last sync.** Nothing to do."
    else:
        headline = "**Figma and the markdown agree.**"
    nst = sum(len(v) for v in A["stale_text"].values())
    nff = sum(len(v) for v in A["foundation_fails"].values())
    if nff and not rows and not A["stale_text"] and not problems:
        headline = "**Figma and the markdown agree.**"
    if nff:
        headline += f" **{nff} foundation check{'s' if nff != 1 else ''} fail** (see Foundation checks)."
    if nst:
        headline += f" **{nst} hand-written line{'s' if nst != 1 else ''} disagree{'s' if nst == 1 else ''} with tokens.json** (see Hand-written text)."
    if A["contrast"]["fails"]:
        n = len(A["contrast"]["fails"])
        headline += f" **{n} contrast failure{'s' if n != 1 else ''}** (see Contrast)."
    out = ["# Sync check", "",
           f"Checked {now} against the snapshot from {snap.get('synced_at')}. "
           f"Places: {', '.join(places)}." + (f" Figma read at {fig['read_at']}." if fig else ""), "", headline, ""]
    if problems:
        out += ["## Problems", ""] + [f"- {p}" for p in problems] + [""]
    con = A["contrast"]
    if con["fails"]:
        out += ["## Contrast", "", f"{len(con['fails'])} of {con['checked']} text and icon pairings in the components fall below WCAG AA. "
                "\"Figma only\" means a pending Figma change would introduce it; \"Markdown only\", a change in tokens.json.", ""]
        out += [f"- {l}" for l in con["fails"]] + [""]
    if rows:
        out += ["## Changes since the last sync", "", "| Item | Name | Changed in | Change | What sync would do |", "| --- | --- | --- | --- | --- |"]
        out += ["| " + " | ".join([k, n, w, c.replace("|", "\\|"), d]) + " |" for k, n, w, c, d in rows] + [""]
    if nff:
        out += ["## Foundation checks", "", "Rows from the foundation files' own checks that fail with the values in tokens.json. "
                "They usually mean a token was changed without the tokens that follow from it, such as a font size without its line height. "
                "Fix the values in Figma (or tokens.json) and sync; the tables update themselves.", ""]
        for rel, rws in sorted(A["foundation_fails"].items()):
            out.append(f"- `{rel}`:")
            out += [f"  - {r.strip()}" for r in rws]
        out.append("")
    if nst:
        out += ["## Hand-written text", "", "These lines state a contrast figure or a value that tokens.json no longer gives. "
                "Sync doesn't rewrite hand-written text: correct each line, keeping its wording.", ""]
        out += [f"- `{rel}` line {i}: {why}." for rel, items in sorted(A["stale_text"].items()) for i, _, why in items] + [""]
    if warnings:
        out += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]
    if not con["fails"]:
        notes = notes + [f"Contrast: all {con['checked']} text and icon pairings in the components meet WCAG AA."]
    if notes:
        out += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    skipped = [p for p in ("figma", "markdown", "code") if p not in places]
    if A["no_figma"] and "figma" in places:
        skipped.append("figma (skipped with --no-figma)")
    if skipped:
        out += [f"Not checked: {', '.join(skipped)}. " + ("Code isn't part of this project yet." if "code" in skipped else ""), ""]
    return "\n".join(out).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--no-figma", action="store_true")
    a = ap.parse_args()
    A = analyse(L.find_root(a.root), a.no_figma)
    text = report(A)
    (A["root"] / ".sync" / "last-check.md").write_text(text)
    print(text)
    sys.exit(2 if A["problems"] else 1 if A["rows"] or A["stale_text"] or A["foundation_fails"] else 0)


if __name__ == "__main__":
    main()
