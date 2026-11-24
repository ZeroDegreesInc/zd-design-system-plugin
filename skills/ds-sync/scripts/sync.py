"""Carry Figma's changes into the markdown place.

  python3 -I sync.py plan   [--root DIR] [--take-figma NAME ...]
  python3 -I sync.py apply  [--root DIR] [--take-figma NAME ...]
  python3 -I sync.py finish [--root DIR]

plan    Lists what apply would write. Writes nothing.
apply   Writes what a script can write safely: tokens.json, icons.json, the foundation
        sections (render.py), and each changed component file's summary, frontmatter
        lists and last_synced. Then lists the component sections that need a person
        or Claude to edit, in .sync/pending-edits.md, and records the run in
        .sync/sync-run.json.
finish  Checks those edits, then moves the snapshot forward for every item that now
        agrees. Items still pending keep showing up in the next check.

Needs a fresh .sync/figma-read.json (see SKILL.md). Conflicts are skipped unless
named with --take-figma, which means "Figma wins for this item".
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import synclib as L
import check as C

SECTION_HINTS = [  # binding field → the component-file sections it usually shows up in
    (re.compile(r"\.(padding-\w+|gap|width|height|size|text-style|stroke-width)$"), ["sizes", "bindings"]),
    (re.compile(r"\.(fill|stroke|color|effect)$"), ["bindings"]),
    (re.compile(r"\.(icon|component|hidden)$"), ["anatomy", "bindings"]),
    (re.compile(r"\.radius$"), ["bindings", "anatomy"]),
]


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def plan(A, take_figma):
    """What figma→markdown would do, from the check's findings."""
    p = {"tokens": [], "deletes": [], "renames": dict(A["renamed"]), "icons": [], "components": [], "intro": False, "skipped": [], "blocked": []}
    if A["problems"]:
        p["blocked"] = list(A["problems"])
        return p
    for n, t in A["tokens"].items():
        if not t["figma"]:
            continue                                   # markdown-only change: the other direction
        if t["markdown"] and not t["same"] and n not in take_figma:
            p["skipped"].append(f"`{n}`: changed in both places. Re-run with --take-figma \"{n}\" to let Figma win.")
            continue
        if t["markdown"] and t["same"]:
            continue                                   # already identical
        (p["deletes"] if t["figma_new"] is None else p["tokens"]).append(n)
    fig = A["fig"] or {}
    icons = fig.get("icons") or {}
    p["icons"] = {"moved": icons.get("moved", {}), "removed": icons.get("removed", []), "added": [i["name"] for i in icons.get("added", [])]}
    for n, c in A["components"].items():
        if c["md_gen_edited"] and n not in take_figma:
            p["skipped"].append(f"`{n}`: Figma changed it and `{c['file']}` has hand edits in generated sections ({', '.join(c['md_gen_edited'])}). Re-run with --take-figma \"{n}\" to let Figma win.")
            continue
        if not c["file"]:
            p["skipped"].append(f"`{n}`: changed in Figma but no markdown file covers it.")
            continue
        p["components"].append(n)
    it = A.get("intro")
    if it and it["lines"]:
        if it["md_edited"] and L.DESIGN_FILE not in take_figma:
            p["skipped"].append(f"`{L.DESIGN_FILE}`: the Introduction page changed and the file's generated block has hand edits. Re-run with --take-figma \"{L.DESIGN_FILE}\" to let Figma win.")
        else:
            p["intro"] = True
    p["contrast"] = [l for l in A["contrast"]["fails"] if "in Figma only" in l or "in both places" in l]
    return p


def describe(p):
    out = []
    if p["blocked"]:
        return "Sync is blocked until these are fixed:\n" + "\n".join(f"- {x}" for x in p["blocked"])
    if p["renames"]:
        out.append("Rename in tokens.json: " + ", ".join(f"`{a}` → `{b}`" for a, b in p["renames"].items()))
    if p["tokens"]:
        out.append("Update or add in tokens.json: " + ", ".join(f"`{n}`" for n in p["tokens"]))
    if p["deletes"]:
        out.append("Delete from tokens.json: " + ", ".join(f"`{n}`" for n in p["deletes"]))
    ic = p["icons"]
    if ic.get("moved") or ic.get("removed"):
        out.append(f"icons.json: {len(ic.get('moved', {}))} node updates, {len(ic.get('removed', []))} removals")
    if ic.get("added"):
        out.append("New icons need a glyph chosen by a person, so they aren't added: " + ", ".join(f"`{n}`" for n in ic["added"]))
    if p.get("intro"):
        out.append(f"Rewrite `{L.DESIGN_FILE}` from the Introduction page in Figma")
    if p["components"]:
        out.append("Component files to update: " + ", ".join(f"`{n}`" for n in p["components"]))
    if p.get("contrast"):
        out.append("Contrast failures this sync would bring in (fix them in Figma first, or sync knowingly):\n"
                   + "\n".join(f"  - {l}" for l in p["contrast"]))
    if p["skipped"]:
        out.append("Skipped:\n" + "\n".join(f"- {s}" for s in p["skipped"]))
    return "\n".join(f"- {x}" if not x.startswith("Skipped") else x for x in out) or "Nothing to carry from Figma to markdown."


# ── apply ────────────────────────────────────────────────────────────

def apply(A, p, root):
    fig = A["fig"]
    run = {"started": now(), "snapshot": A["snap"]["synced_at"], "tokens": [], "deletes": [], "renames": {}, "icons": {},
           "components": {}, "files": []}
    tokens_path = root / "tokens.json"
    tokens = json.loads(tokens_path.read_text())

    # renames first, so later updates land on the new names
    for old, new in p["renames"].items():
        nodes = L.token_nodes(tokens)
        if old in nodes:
            group, path, node = nodes[old]
            kind = {"text-styles": "text", "effect-styles": "effect", "grid-styles": "grid"}.get(group, "var")
            L.delete_at(tokens, path)
            L.set_at(tokens, L.new_path(new, group, kind), node)
            if kind != "var":
                node["$extensions"]["figma"]["styleName"] = new
            run["renames"][old] = new
    groups = L.collection_groups(tokens)
    meta = (fig["tokens"].get("meta") or {})
    # variables before styles, so a new style can refer to a variable added in the same run
    for n in sorted(p["tokens"], key=lambda n: A["tokens"][n]["figma_new"]["kind"] != "var"):
        conv = L.Converter(tokens)
        proj = A["tokens"][n]["figma_new"]
        existing = conv.nodes.get(n)
        node = conv.node(n, proj, meta.get(n), existing[2] if existing else None)
        if existing:
            L.set_at(tokens, existing[1], node)
        else:
            col = (meta.get(n) or {}).get("collection")
            group = groups.get(col)
            if not group:
                raise SystemExit(f"`{n}` is in Figma collection {col!r}, which has no group in tokens.json. Add the group (see FORMATS.md) and re-run.")
            L.set_at(tokens, L.new_path(n, group, proj["kind"]), node)
        run["tokens"].append(n)
    for n in p["deletes"]:
        nodes = L.token_nodes(tokens)
        if n in nodes:
            L.delete_at(tokens, nodes[n][1])
            run["deletes"].append(n)
    if run["tokens"] or run["deletes"] or run["renames"]:
        L.sync_group_modes(tokens)      # a mode added in Figma joins its collection's list
        tokens_path.write_text(json.dumps(tokens, indent=2, ensure_ascii=False) + "\n")
        run["files"].append("tokens.json")

    # icons
    ic = p["icons"]
    if ic.get("moved") or ic.get("removed"):
        ipath = root / "icons.json"
        icons = json.loads(ipath.read_text())
        for i in icons["icons"]:
            if i["name"] in ic["moved"]:
                i["figmaNode"] = ic["moved"][i["name"]]
        icons["icons"] = [i for i in icons["icons"] if i["name"] not in ic["removed"]]
        ipath.write_text(json.dumps(icons, indent=2, ensure_ascii=False) + "\n")
        run["icons"] = {"moved": ic["moved"], "removed": ic["removed"]}
        run["files"].append("icons.json")

    # foundation sections follow tokens.json and icons.json
    render = root / "_scripts" / "render.py"
    if render.exists() and ("tokens.json" in run["files"] or "icons.json" in run["files"]):
        r = subprocess.run([sys.executable, "-I", str(render), str(root)], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("render.py failed:\n" + r.stderr)
        run["files"] += [line.split(":")[0] for line in r.stdout.splitlines() if line.startswith("foundations/")]

    # design.md: written whole from the Introduction page, so nothing is left to edit by hand
    if p.get("intro"):
        L.write_design(root, A["intro"]["figma"])
        run["intro"] = True
        run["files"].append(L.DESIGN_FILE)

    # component files: the parts a script can write, and a hand-off list for the rest
    pending = []
    used_cache = {}
    for n in p["components"]:
        c = A["components"][n]
        rel, diff = c["file"], c["diff"]
        path = root / rel
        text = path.read_text()
        done, todo = [], set()
        if "description" in diff:
            text, k = re.subn(r"(<!-- sync:begin summary [^>]*-->\n).*?(<!-- sync:end summary -->)",
                              lambda m: m.group(1) + "> " + diff["description"] + "\n" + m.group(2), text, flags=re.S)
            if k == 1 and rel.endswith(".md") and L.frontmatter(text).get("name") == n:
                done.append("summary")
            elif k == 1:
                todo.add("summary")   # a hidden wrapper's description isn't the file's summary; leave it to judgment
        if "properties" in diff or "default_variant" in diff:
            todo.update(["properties", "anatomy"])
        if "name" in diff:
            todo.update(["frontmatter name and title", "every mention of the old name"])
        for k, v in diff.items():
            if not k.startswith("variant:"):
                continue
            if v is None:
                todo.update(["properties", "bindings", "anatomy"])
                continue
            base_b = (A["snap"]["components"].get(n, {}).get("bindings") or {}).get(k[len("variant:"):], {})
            for key in set(base_b) | set(v):
                if base_b.get(key) != v.get(key):
                    for pat, secs in SECTION_HINTS:
                        if pat.search(key):
                            todo.update(secs)
                            break
                    else:
                        todo.add("bindings")
        # frontmatter: add tokens and styles the component now uses
        used = used_tokens_after(A, n, diff)
        fm = L.frontmatter(text)
        missing_t = sorted(t for t in used["tokens"] if t not in (fm.get("tokens") or []))
        missing_s = sorted(s for s in used["styles"] if s not in (fm.get("styles") or []))
        text = add_frontmatter_items(text, "tokens", missing_t)
        text = add_frontmatter_items(text, "styles", missing_s)
        # drop tokens this run's changes replaced, when nothing in the component uses them any more
        replaced = set()
        for k, v in diff.items():
            if k.startswith("variant:") and v:
                base_b = (A["snap"]["components"].get(n, {}).get("bindings") or {}).get(k[len("variant:"):], {})
                for key, old in base_b.items():
                    if isinstance(old, str) and old != v.get(key):
                        replaced.update(old.split("+"))
        gone_t = sorted(t for t in replaced if t in (fm.get("tokens") or []) and t not in used["tokens"])
        gone_s = sorted(t for t in replaced if t in (fm.get("styles") or []) and t not in used["styles"])
        text = remove_frontmatter_items(text, "tokens", gone_t)
        text = remove_frontmatter_items(text, "styles", gone_s)
        if missing_t or missing_s or gone_t or gone_s:
            done.append("frontmatter lists")
        text = re.sub(r"^last_synced: .*$", f"last_synced: {datetime.date.today().isoformat()}", text, count=1, flags=re.M)
        path.write_text(text)
        run["components"][n] = {"file": rel, "written": done, "to_edit": sorted(todo), "changes": c["lines"]}
        run["files"].append(rel)
        if todo:
            pending.append((n, rel, sorted(todo), c["lines"]))

    # hand-written text this run may have made wrong: contrast figures and pairings
    texts = {str(f.relative_to(root)): f.read_text() for f in L.markdown_files(root) if f.suffix == ".md"}
    after = L.projections(json.loads(tokens_path.read_text()), A["cfg"])
    comps_after = {n: (A["figma_components"][n] if n in p["components"] else e) for n, e in A["snap"]["components"].items()}
    review = L.prose_review(texts, A["comp_file"],
                            {"proj": A["base_tokens"], "pairs": L.contrast_pairs(A["snap"]["components"], A["base_tokens"])},
                            {"proj": after, "pairs": L.contrast_pairs(comps_after, after)})
    run["review"] = {rel: [why for _, _, why in items] for rel, items in review.items()}

    (root / ".sync" / "sync-run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n")
    lines = ["# Pending edits", "", f"From the sync run started {run['started']}. Edit these sections to match Figma, then run `sync.py finish`.", ""]
    if not pending and not review:
        lines.append("None. Every change was written automatically.")
    else:
        lines.append("Tick each box once its file matches Figma. `finish` won't run until every box is ticked.")
        lines.append("")
    for n, rel, todo, changes in pending:
        lines += [f"## {n} · `{rel}`", "", f"- [ ] Updated: {', '.join(todo)}", "", "What changed in Figma:"]
        lines += [f"- {x}" for x in changes] + [""]
    for rel, items in review.items():
        lines += [f"## Hand-written text · `{rel}`", "", "- [ ] Reviewed: each line below is correct again, or was already right", "",
                  "This run's changes may have made these wrong. Fix figures and names; keep the wording:"]
        lines += [f"- Line {i}: {why}" if i else f"- {why}" for i, _, why in items] + [""]
    (root / ".sync" / "pending-edits.md").write_text("\n".join(lines).rstrip() + "\n")
    return run, pending + [(None, rel, ["hand-written text"], []) for rel in review]


def used_tokens_after(A, n, diff):
    """Tokens and styles the component's bindings use once Figma's changes are applied (including its wrappers)."""
    tok_names = set(A["local_tokens"])
    entries = [n] + [m for m, f in A["comp_file"].items() if f == A["components"][n]["file"] and m != n]
    out = {"tokens": set(), "styles": set()}
    for name in entries:
        base = (A["snap"]["components"].get(name) or {}).get("bindings") or {}
        d = (A["components"].get(name) or {}).get("diff", {}) if name != n else diff
        merged = dict(base)
        for k, v in d.items():
            if k.startswith("variant:"):
                if v is None:
                    merged.pop(k[len("variant:"):], None)
                else:
                    merged[k[len("variant:"):]] = v
        for b in merged.values():
            for key, val in b.items():
                if not isinstance(val, str) or key.endswith(".component") or key.endswith(".icon"):
                    continue
                for part in val.split("+"):
                    if part in tok_names:
                        kind = (A["local_tokens"][part] or {}).get("kind")
                        out["styles" if kind in ("text", "effect", "grid") else "tokens"].add(part)
    return out


def remove_frontmatter_items(text, key, items):
    for i in items:
        text = re.sub(rf"(^{key}:\n(?:  - .*\n)*?)  - {re.escape(i)}\n", r"\1", text, count=1, flags=re.M)
    return text


def add_frontmatter_items(text, key, items):
    if not items:
        return text
    m = re.search(rf"^{key}:\n((?:  - .*\n)+)", text, re.M)
    if m:
        return text[:m.end()] + "".join(f"  - {i}\n" for i in items) + text[m.end():]
    m = re.search(rf"^{key}: \[(.*)\]$", text, re.M)
    if m:
        cur = [x.strip() for x in m.group(1).split(",") if x.strip()]
        return text[:m.start()] + f"{key}: [{', '.join(cur + items)}]" + text[m.end():]
    return text


# ── finish ───────────────────────────────────────────────────────────

def finish(root):
    run_path = root / ".sync" / "sync-run.json"
    if not run_path.exists():
        raise SystemExit("No .sync/sync-run.json. Run `sync.py apply` first.")
    run = json.loads(run_path.read_text())
    snap_path = root / ".sync" / "snapshot.json"
    snap = json.loads(snap_path.read_text())
    if snap["synced_at"] != run["snapshot"]:
        raise SystemExit("The snapshot moved since this run was applied. Check again from the start.")
    fig = json.loads((root / ".sync" / "figma-read.json").read_text())["ds_sync_figma_read"]

    # every pending edit must be signed off
    problems = []
    pending = (root / ".sync" / "pending-edits.md")
    if pending.exists():
        unticked = re.findall(r"^## (.+)\n\n- \[ \]", pending.read_text(), re.M)
        for n in unticked:
            problems.append(f"{n}: its box in .sync/pending-edits.md isn't ticked.")

    # verify component files
    for n, c in run["components"].items():
        text = (root / c["file"]).read_text()
        diff = fig["components"]["changed"].get(n, {})
        fm = L.frontmatter(text)
        if "description" in diff and fm.get("name") == n:
            m = re.search(r"sync:begin summary[^\n]*\n> (.*)\n", text)
            if not m or m.group(1).strip() != diff["description"].strip():
                problems.append(f"`{c['file']}`: the summary doesn't match Figma's description.")
        for k, v in diff.items():
            if not (k.startswith("variant:") and v):
                continue
            var = k[len("variant:"):]
            base_b = (snap["components"].get(n, {}).get("bindings") or {}).get(var, {})
            # a table row for this variant (its option values as cells) must not still name the old token
            opts = [x.split("=", 1)[1] for x in var.split(", ") if "=" in x]
            rows = [l for l in text.splitlines() if l.startswith("|") and opts and all(f"| {o} |" in l or l.startswith(f"| {o} |") for o in opts)]
            for key in v:
                old, new = base_b.get(key), v.get(key)
                if isinstance(old, str) and isinstance(new, str) and old != new and not key.endswith(".component"):
                    for row in rows:
                        if f"`{old}`" in row and f"`{new}`" not in row:
                            problems.append(f"`{c['file']}`: the {' / '.join(opts)} row still says `{old}`; Figma now uses `{new}` (`{key}`).")
            for key in v:
                new = v.get(key)
                if isinstance(new, str) and new != base_b.get(key) and not key.endswith(".component"):
                    for part in new.split("+"):
                        if part in s_names(snap) and f"`{part}`" not in text and part not in (fm.get("tokens") or []) + (fm.get("styles") or []):
                            problems.append(f"`{c['file']}` never mentions `{part}`, which {n} now uses ({k[len('variant:'):]}, `{key}`).")
    if run.get("intro"):
        text = (root / L.DESIGN_FILE).read_text() if (root / L.DESIGN_FILE).exists() else ""
        if L.design_block(text) != L.render_intro(fig["intro"]):
            problems.append(f"`{L.DESIGN_FILE}`: its generated block no longer matches the Introduction page. Run `sync.py apply` again rather than editing it.")
    if problems:
        print("Not finished. Fix these, then run `sync.py finish` again:\n" + "\n".join(f"- {x}" for x in problems))
        sys.exit(2)

    # move the snapshot forward for what this run carried across
    tokens = json.loads((root / "tokens.json").read_text())
    nodes = L.token_nodes(tokens)
    for old, new in run["renames"].items():
        snap["tokens"].pop(old, None)
        run["tokens"].append(new)
    L.rename_in_bindings(snap["components"], run["renames"])
    for n in run["tokens"]:
        g, path, node = nodes[n]
        snap["tokens"][n] = {"id": node.get("$extensions", {}).get("figma", {}).get("id"), "group": g, "path": path, "node": node}
    for n in run["deletes"]:
        snap["tokens"].pop(n, None)
    if run["icons"]:
        for name, node in run["icons"].get("moved", {}).items():
            snap["icons"]["icons"][name]["figma_node"] = node
        for name in run["icons"].get("removed", []):
            snap["icons"]["icons"].pop(name, None)
    for n in run["components"]:
        entry = snap["components"].setdefault(n, {})
        if n in fig["components"].get("adopted", {}):       # a new component joining sync
            entry["figma_node"] = fig["components"]["adopted"][n]
        for k, v in fig["components"]["changed"].get(n, {}).items():
            if k == "name":
                continue
            if k.startswith("variant:"):
                var = k[len("variant:"):]
                if v is None:
                    entry.get("bindings", {}).pop(var, None)
                else:
                    entry.setdefault("bindings", {})[var] = v
            else:
                entry[k] = v
        if "name" in fig["components"]["changed"].get(n, {}):
            snap["components"][fig["components"]["changed"][n]["name"]] = snap["components"].pop(n)
    if run.get("intro"):
        snap["intro"] = fig["intro"]
    # file records: every file this run wrote, plus files with hand-written-only edits
    files_now = {str(p.relative_to(root)): L.file_record(p) for p in L.markdown_files(root)}
    base_files = snap["places"]["markdown"]["files"]
    for rel, rec in files_now.items():
        old = base_files.get(rel)
        hand_only = old and rec.get("sections", {}) == old.get("sections", {})
        if rel in run["files"] or hand_only or old is None:
            base_files[rel] = rec
    for rel in [r for r in base_files if r not in files_now]:
        del base_files[rel]
    h = __import__("hashlib").sha256()
    for p in L.markdown_files(root):
        h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
    snap["places"]["markdown"]["fingerprint"] = "sha256:" + h.hexdigest()
    snap["places"]["figma"]["read_at"] = fig.get("read_at")
    snap["synced_at"] = now()
    snap_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    run_path.rename(run_path.with_name(f"sync-run-{snap['synced_at'].replace(':', '')}.json"))
    print(f"Snapshot moved forward ({snap['synced_at']}): {len(run['tokens'])} tokens, {len(run['deletes'])} deletions, "
          f"{len(run['components'])} components{', the Introduction' if run.get('intro') else ''}, {len(run['files'])} files written. Run the check again to confirm.")


def s_names(snap):
    return set(snap["tokens"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["plan", "apply", "finish"])
    ap.add_argument("--root")
    ap.add_argument("--take-figma", action="append", default=[])
    a = ap.parse_args()
    root = L.find_root(a.root)
    if a.command == "finish":
        return finish(root)
    A = C.analyse(root)
    if not A["fig"]:
        raise SystemExit("Figma-to-markdown needs a Figma read. See SKILL.md.")
    p = plan(A, set(a.take_figma))
    print(describe(p))
    if a.command == "plan" or p["blocked"]:
        sys.exit(2 if p["blocked"] else 0)
    run, pending = apply(A, p, root)
    print(f"\nApplied. Wrote: {', '.join(run['files']) or 'nothing'}.")
    if pending:
        print(f"{len(pending)} item(s) to edit or review: see .sync/pending-edits.md, then run `sync.py finish`.")
    else:
        print("No hand edits needed. Run `sync.py finish`.")


if __name__ == "__main__":
    main()
