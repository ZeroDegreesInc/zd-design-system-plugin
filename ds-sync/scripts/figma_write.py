"""Carry markdown-side changes into Figma for /ds-sync.

  python3 -I figma_write.py plan [--root DIR] [--take-markdown NAME ...]
      What would be written to Figma, from the check's findings. Writes nothing.
  python3 -I figma_write.py prepare [--root DIR] [--take-markdown NAME ...]
      Regenerates stale foundation sections, then writes .sync/figma-write.js. Run it with use_figma.
  python3 -I figma_write.py finish [--root DIR]
      Finds that result, writes new Figma ids into tokens.json, and moves the snapshot forward
      for what Figma accepted.

Tokens and styles are written in full from tokens.json: value, modes, description, scopes and
visibility, or each style's own fields. Component summaries become Figma descriptions. Anything
else a markdown file changed about a component (bindings, properties, anatomy) and icon glyphs
are listed for a person to do in Figma; sync then carries the result back.
"""
import argparse
import datetime
import json
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import synclib as L
import check as C
from figma_read import find_result

STYLE_GROUPS = {"text-styles": "text", "effect-styles": "effect", "grid-styles": "grid"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def summary_of(text):
    import re
    m = re.search(r"sync:begin summary[^\n]*\n> (.*)\n", text)
    return m.group(1).strip() if m else None


def plan(A, take):
    p = {"ops": [], "manual": [], "skipped": [], "blocked": [], "render": sorted(A["stale"])}
    if A["problems"]:
        p["blocked"] = list(A["problems"])
        return p
    root, local, base = A["root"], A["local_tokens"], A["base_tokens"]
    tokens_json = json.loads((root / "tokens.json").read_text())
    nodes = L.token_nodes(tokens_json)
    groups = {g: (o.get("$extensions", {}).get("figma", {}).get("collection")) for g, o in tokens_json.items() if isinstance(o, dict)}

    renamed_to = {}
    for old, new in A["md_renamed"].items():
        fig_new = A["renamed"].get(old)
        if fig_new and fig_new != new and new not in take:
            p["skipped"].append(f"`{old}`: renamed to `{new}` in tokens.json and to `{fig_new}` in Figma. Re-run with --take-markdown \"{new}\" to keep the markdown name.")
            continue
        p["ops"].append({"op": "rename", "from": old, "name": new, "kind": local[new]["kind"]})
        renamed_to[new] = old
    for n, t in A["tokens"].items():
        if not t["markdown"]:
            continue                                   # Figma-side change: the other direction
        if t["figma"] and t["same"]:
            continue
        if t["figma"] and n not in take:
            p["skipped"].append(f"`{n}`: changed in both places. Re-run with --take-markdown \"{n}\" to let the markdown win.")
            continue
        new = t["markdown_new"]
        if new is None:
            p["ops"].append({"op": "delete", "name": n, "kind": t["base"]["kind"]})
            continue
        group = nodes[n][0]
        op = {"op": "update" if (t["base"] is not None or n in renamed_to) else "create", "name": n, "kind": new["kind"], "proj": new,
              "collection": groups.get(group) if group not in STYLE_GROUPS else None}
        if op["op"] == "create" and op["kind"] == "var" and not op["collection"]:
            p["skipped"].append(f"`{n}`: its group `{group}` names no Figma collection in tokens.json.")
            continue
        p["ops"].append(op)
    # component summaries → Figma descriptions; other generated-section edits are manual
    fig_changed = set((A["fig"] or {}).get("components", {}).get("changed", {}))
    snap_c = A["snap"]["components"]
    for rel, how in A["changed_files"].items():
        if not rel.startswith("components/") or not isinstance(how, list):
            continue
        text = (root / rel).read_text()
        name = L.frontmatter(text).get("name")
        rest = [k for k in how if k != "summary"]
        if "summary" in how and name in snap_c:
            if name in fig_changed and "description" in (A["fig"]["components"]["changed"][name]) and name not in take:
                p["skipped"].append(f"`{name}`: description changed in both places. Re-run with --take-markdown \"{name}\" to let the markdown win.")
            else:
                p["ops"].append({"op": "description", "name": name, "node": snap_c[name]["figma_node"], "description": summary_of(text), "file": rel})
        if rest:
            p["manual"].append(f"`{rel}`: generated sections edited ({', '.join(rest)}). Make the same change to {name or 'the component'} in Figma; the next sync carries it back.")
    for kind, n, where, change, _ in A["rows"]:
        if kind in ("Icon", "Icons") and where == "Markdown":
            p["manual"].append(f"Icon {n}: {change}. Redraw it in Figma from `icons/`.")
    return p


def describe(p):
    if p["blocked"]:
        return "Writing to Figma is blocked until these are fixed:\n" + "\n".join(f"- {x}" for x in p["blocked"])
    out = []
    by = {}
    for o in p["ops"]:
        by.setdefault(o["op"], []).append(o)
    label = {"rename": "Rename in Figma", "update": "Update in Figma", "create": "Create in Figma", "delete": "Delete from Figma",
             "description": "Set component descriptions from the summaries"}
    for k in ("rename", "create", "update", "description", "delete"):
        if k in by:
            names = [f"`{o['from']}` → `{o['name']}`" if k == "rename" else f"`{o['name']}`" for o in by[k]]
            out.append(f"- {label[k]}: " + ", ".join(names))
    if p["render"]:
        out.append("- Regenerate first (markdown only): " + ", ".join(f"`{r}`" for r in p["render"]))
    if p["manual"]:
        out.append("For a person to do in Figma:\n" + "\n".join(f"- {m}" for m in p["manual"]))
    if p["skipped"]:
        out.append("Skipped:\n" + "\n".join(f"- {s}" for s in p["skipped"]))
    return "\n".join(out) or "Nothing to carry from markdown to Figma."


def prepare(A, p, root):
    if p["render"]:
        r = subprocess.run([sys.executable, "-I", str(root / "_scripts" / "render.py"), str(root)], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit("render.py failed:\n" + r.stderr)
    ops = p["ops"]
    nonce = secrets.token_hex(6)
    js = (HERE / "figma_write.template.js").read_text().replace(
        "__INPUT__", json.dumps({"nonce": nonce, "ops": ops}, separators=(",", ":"), ensure_ascii=False))
    (root / ".sync" / "figma-write.js").write_text(js)
    (root / ".sync" / "figma-write-tag.json").write_text(json.dumps(
        {"nonce": nonce, "snapshot": A["snap"]["synced_at"], "ops": ops, "started": now()}, indent=1, ensure_ascii=False) + "\n")
    (root / ".sync" / "figma-write.json").unlink(missing_ok=True)
    return js, nonce


def finish(root):
    tag_path = root / ".sync" / "figma-write-tag.json"
    if not tag_path.exists():
        raise SystemExit("No .sync/figma-write-tag.json. Run `figma_write.py prepare` first.")
    tag = json.loads(tag_path.read_text())
    snap_path = root / ".sync" / "snapshot.json"
    snap = json.loads(snap_path.read_text())
    if snap["synced_at"] != tag["snapshot"]:
        raise SystemExit("The snapshot moved since this write was prepared. Check again from the start.")
    got = find_result(root, "write", tag["nonce"])
    done, errors = got["done"], got["errors"]

    # new ids into tokens.json
    tokens_path = root / "tokens.json"
    tokens = json.loads(tokens_path.read_text())
    nodes = L.token_nodes(tokens)
    wrote_tokens = False
    for o in tag["ops"]:
        if o["op"] in ("create", "rename", "update") and o["name"] in done and o["name"] in nodes:
            fig = nodes[o["name"]][2].setdefault("$extensions", {}).setdefault("figma", {})
            if fig.get("id") != done[o["name"]]:
                fig["id"] = done[o["name"]]
                wrote_tokens = True
    if wrote_tokens:
        tokens_path.write_text(json.dumps(tokens, indent=2, ensure_ascii=False) + "\n")
        nodes = L.token_nodes(tokens)

    # snapshot: only what Figma accepted
    renames = {o["from"]: o["name"] for o in tag["ops"] if o["op"] == "rename" and o["name"] in done}
    for old, new in renames.items():
        snap["tokens"].pop(old, None)
    L.rename_in_bindings(snap["components"], renames)
    if renames:   # tokens that only reference a renamed one follow it in Figma by themselves
        base = L.snapshot_projections(snap["tokens"])
        local = L.projections(tokens)
        for n in list(snap["tokens"]):
            if n in local and n in base and L.canon(L.rename_refs(base[n], renames)) != L.canon(base[n]):
                g, path, node = nodes[n]
                snap["tokens"][n] = {**snap["tokens"][n], "path": path, "node": node}
    for o in tag["ops"]:
        n = o["name"]
        if o["op"] in ("create", "update", "rename") and n in done:
            g, path, node = nodes[n]
            snap["tokens"][n] = {"id": node.get("$extensions", {}).get("figma", {}).get("id"), "group": g, "path": path, "node": node}
        elif o["op"] == "delete" and n in done:
            snap["tokens"].pop(n, None)
        elif o["op"] == "description" and "component:" + n in done:
            snap["components"][n]["description"] = o["description"]

    # file records: tokens.json, regenerated foundations, and component files whose summary went across
    # foundation files whose generated sections match tokens.json, which now agrees with Figma
    render = root / "_scripts" / "render.py"
    rendered = set()
    if render.exists():
        r = subprocess.run([sys.executable, "-I", str(render), str(root), "--check"], capture_output=True, text=True)
        stale = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
        rendered = {str(f.relative_to(root)) for f in (root / "foundations").glob("*.md")} - set(stale)
    files = {"tokens.json"} | rendered | {o["file"] for o in tag["ops"] if o["op"] == "description" and "component:" + o["name"] in done}
    recs = snap["places"]["markdown"]["files"]
    if renames:     # files whose generated sections only follow the renames
        for f in L.markdown_files(root):
            rel = str(f.relative_to(root))
            if f.suffix == ".md" and rel in recs and not L.sections_beyond_renames(f.read_text(), recs[rel].get("sections"), renames):
                files.add(rel)
    for rel in files:
        if (root / rel).exists():
            recs[rel] = L.file_record(root / rel)
    h = __import__("hashlib").sha256()
    for f in L.markdown_files(root):
        h.update(str(f.relative_to(root)).encode()); h.update(f.read_bytes())
    snap["places"]["markdown"]["fingerprint"] = "sha256:" + h.hexdigest()
    snap["places"]["figma"]["written_at"] = now()
    snap["synced_at"] = now()
    snap_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    tag_path.rename(tag_path.with_name(f"figma-write-{snap['synced_at'].replace(':', '')}.json"))
    for f in ("figma-write.json", "figma-write.js"):
        (root / ".sync" / f).unlink(missing_ok=True)
    print(f"Figma accepted {len(done)} item(s). Snapshot moved forward ({snap['synced_at']}) for those only."
          + (" New Figma ids written into tokens.json." if wrote_tokens else ""))
    if errors:
        print("Figma refused these; they stay in the report:\n" + "\n".join(f"- `{e['name']}`: {e['error']}" for e in errors))
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["plan", "prepare", "finish"])
    ap.add_argument("--root")
    ap.add_argument("--take-markdown", action="append", default=[])
    a = ap.parse_args()
    root = L.find_root(a.root)
    if a.command == "finish":
        return finish(root)
    A = C.analyse(root)
    if not A["fig"]:
        raise SystemExit("Markdown-to-Figma needs a fresh Figma read, so it doesn't overwrite Figma-side changes. See SKILL.md.")
    p = plan(A, set(a.take_markdown))
    print(describe(p))
    if a.command == "plan" or p["blocked"]:
        sys.exit(2 if p["blocked"] else 0)
    if not p["ops"]:
        print("\nNothing for Figma to do.")
        return
    js, nonce = prepare(A, p, root)
    print(f"\nWrote .sync/figma-write.js ({len(js) // 1024} KB, tag {nonce}). Run it with use_figma for file "
          f"{(A['cfg'].get('figma') or {}).get('file_key')}, then run `figma_write.py finish`.")


if __name__ == "__main__":
    main()
