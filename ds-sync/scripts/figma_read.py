"""Read Figma for /ds-sync in two small steps, and produce .sync/figma-read.json for check.py.

  python3 -I figma_read.py prepare [--root DIR]
      Writes .sync/figma-read.js (mode "fingerprints"). Run it with use_figma.
  python3 -I figma_read.py compare [--root DIR]
      Finds that result (see find_result) and compares it with the snapshot. If nothing
      needs details, writes .sync/figma-read.json and you're done. Otherwise writes
      .sync/figma-details.js (mode "details", naming only what differs). Run it.
  python3 -I figma_read.py merge [--root DIR]
      Combines both results into .sync/figma-read.json.
"""
import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import synclib as L


def load(root):
    cfg = L.load_config(root)
    snap = json.loads((root / ".sync" / "snapshot.json").read_text())
    return cfg, snap


def script(root, cfg, snap, mode, want=None, nonce=None):
    figma = cfg.get("figma") or {}
    nodes = set()
    for md in sorted((root / "components").glob("*.md")) + sorted((root / "foundations").glob("*.md")):
        text = md.read_text()
        fig = (L.frontmatter(text).get("figma") or {})
        for k in ("node", "page"):
            if fig.get(k) and fig[k] != "0:0":         # "0:0": a spec not built yet
                nodes.add(fig[k])
        for i in L.parse_internal(text):
            if i["node"] != "0:0":
                nodes.add(i["node"])
    data = {
        "mode": mode,
        "nonce": nonce,
        "exclude": cfg.get("exclude") or [],
        "component_pages": figma.get("component_pages") or [],
        "icon_page": figma.get("icon_page"),
        "intro_page": figma.get("intro_page") if mode == "fingerprints" else None,
        # tracked components, plus new ones a markdown file names, so sync can adopt them
        "components": {**{n: e["figma_node"] for n, e in snap["components"].items()},
                       **{n: x["node"] for n, x in L.new_component_files(root, snap).items()}},
        "nodes": sorted(nodes) if mode == "fingerprints" else [],
        "want": want or {},
    }
    template = (HERE / "figma_read.template.js").read_text()
    return template.replace("__INPUT__", json.dumps(data, separators=(",", ":"), ensure_ascii=False))


def find_result(root, kind, nonce):
    """The Figma result for this read: from .sync/<file> if saved, otherwise from the Claude Code session log.

    use_figma results are written to the session's .jsonl log, so nobody has to copy a large result
    into a file by hand. The random nonce in the script ties the result to this exact read."""
    saved = root / ".sync" / f"figma-{kind}.json"
    key = f"ds_sync_figma_{kind}"
    if saved.exists():
        got = json.loads(saved.read_text())[key]
        if nonce and got.get("nonce") not in (None, nonce):
            raise SystemExit(f"{saved.name} is from a different read. Delete it, or run the current script again.")
        return got
    logs = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
    recent = sorted((p for p in logs.glob("*/*.jsonl") if time.time() - p.stat().st_mtime < 6 * 3600),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    marker = f'"nonce":"{nonce}"'
    for log in recent:
        # split on newlines only: splitlines() also breaks on U+2028, which Figma text can contain
        for line in reversed(log.read_text(errors="ignore").split("\n")):
            if key not in line or nonce not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:          # not every log line is one JSON record
                continue
            for c in ((entry.get("message") or {}).get("content") or []):
                if not isinstance(c, dict) or c.get("type") != "tool_result":
                    continue
                for x in c.get("content") or []:
                    t = x.get("text", "") if isinstance(x, dict) else ""
                    if t.startswith("{\"" + key) and marker in t:
                        saved.write_text(t + "\n")
                        return json.loads(t)[key]
    raise SystemExit(f"Couldn't find the Figma result for this read (tag {nonce}). Save the use_figma result as .sync/figma-{kind}.json and run this again.")


def expected(cfg, snap):
    proj = L.snapshot_projections(snap["tokens"], cfg)
    tokens = {n: (L.fp(p), snap["tokens"][n]["id"]) for n, p in proj.items()}
    comps = {n: {k: L.fp(v) for k, v in L.component_parts(e).items()} for n, e in snap["components"].items()}
    return tokens, comps


def compare(root):
    cfg, snap = load(root)
    tag = json.loads((root / ".sync" / "figma-read-tag.json").read_text())
    if tag["snapshot"] != snap["synced_at"]:
        raise SystemExit("The snapshot moved since this read was prepared. Run `figma_read.py prepare` again.")
    nonce = tag["nonce"]
    got = find_result(root, "fingerprints", nonce)
    exp_tok, exp_comp = expected(cfg, snap)

    live = {n: v for n, v in got["tokens"].items() if not L.excluded(n, cfg)}
    id_to_live = {i: n for n, (_, i) in live.items()}
    changed, removed, renamed = [], [], {}
    for n, (h, i) in exp_tok.items():
        if n in live:
            if live[n][0] != h:
                changed.append(n)
            continue
        now = id_to_live.get(i)
        if now and now not in exp_tok:
            renamed[n] = now
            if live[now][0] != h:
                changed.append(now)
        else:
            removed.append(n)
    added = [n for n in live if n not in exp_tok and n not in renamed.values()]

    comp_parts, comp_removed, comp_renamed = {}, [], {}
    for n, parts in exp_comp.items():
        g = got["components"].get(n)
        if g is None:
            comp_removed.append(n)
            continue
        diff = [k for k in set(parts) | set(g["parts"]) if parts.get(k) != g["parts"].get(k)]
        if diff:
            comp_parts[n] = sorted(diff)
        if g["name"] != n:
            comp_renamed[n] = g["name"]
            comp_parts.setdefault(n, [])
    # new components a markdown file names: every part is new, so read them all
    adopted = {}
    for n, x in L.new_component_files(root, snap).items():
        g = got["components"].get(n)
        if g:
            comp_parts[n] = sorted(g["parts"])
            adopted[n] = x["node"]
            if g["name"] != n:
                comp_renamed[n] = g["name"]

    snap_icons = {n: i["figma_node"] for n, i in snap["icons"]["icons"].items()}
    icons = {
        "moved": {n: got["icons"][n] for n in snap_icons if n in got["icons"] and got["icons"][n] != snap_icons[n]},
        "added": [{"name": n, "node": i} for n, i in got["icons"].items() if n not in snap_icons],
        "removed": [n for n in snap_icons if n not in got["icons"]],
    }
    state = {"snapshot": snap["synced_at"], "file_key": got.get("file_key"), "read_at": got.get("read_at"),
             "tokens": {"changed": changed, "added": added, "removed": removed, "renamed": renamed},
             "components": {"parts": comp_parts, "removed": comp_removed, "renamed": comp_renamed, "added": got["added"], "adopted": adopted},
             "icons": icons, "intro": got.get("intro"), "missing_nodes": got["missing_nodes"]}
    (root / ".sync" / "figma-compare.json").write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")

    want_tokens = sorted(set(changed) | set(added))
    want_comps = {n: ks for n, ks in comp_parts.items() if ks}
    if not want_tokens and not want_comps:
        write_read(root, state, {"tokens": {}, "components": {}})
        print("Figma matches the snapshot's fingerprints except as listed in .sync/figma-read.json. No details call needed.")
        return
    dnonce = secrets.token_hex(6)
    state["details_nonce"] = dnonce
    (root / ".sync" / "figma-compare.json").write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    js = script(root, cfg, snap, "details", {"tokens": want_tokens, "components": want_comps}, dnonce)
    (root / ".sync" / "figma-details.js").write_text(js)
    print(f"{len(want_tokens)} token(s) and {len(want_comps)} component(s) differ. "
          f"Run .sync/figma-details.js ({len(js) // 1024} KB) with use_figma, then run `figma_read.py merge`.")


def write_read(root, state, details):
    t, c = state["tokens"], state["components"]
    tok_out = {"changed": {}, "added": {}, "removed": t["removed"], "renamed": t["renamed"], "meta": {}}
    for n in t["changed"] + t["added"]:
        d = details["tokens"].get(n)
        if d is None:
            raise SystemExit(f"The details result has no entry for token `{n}`. Read Figma again.")
        tok_out["changed" if n in t["changed"] else "added"][n] = d["proj"]
        tok_out["meta"][n] = {"id": d["id"], "collection": d["collection"]}
    comp_out = {}
    for n, keys in c["parts"].items():
        d = details["components"].get(n) or {}
        diff = {k: d.get(k) for k in keys}
        if n in c["renamed"]:
            diff["name"] = c["renamed"][n]
        comp_out[n] = diff
    read = {"ds_sync_figma_read": {
        "snapshot": state["snapshot"], "file_key": state["file_key"], "read_at": state["read_at"],
        "tokens": tok_out,
        "components": {"changed": comp_out, "added": c["added"], "removed": c["removed"], "adopted": c.get("adopted", {})},
        "icons": state["icons"], "intro": state.get("intro"), "missing_nodes": state["missing_nodes"]}}
    (root / ".sync" / "figma-read.json").write_text(json.dumps(read, ensure_ascii=False) + "\n")


def merge(root):
    sync = root / ".sync"
    state = json.loads((sync / "figma-compare.json").read_text())
    _, snap = load(root)
    if state["snapshot"] != snap["synced_at"]:
        raise SystemExit("The snapshot moved since the compare. Start again from `figma_read.py prepare`.")
    details = find_result(root, "details", state["details_nonce"])
    write_read(root, state, details)
    print("Wrote .sync/figma-read.json. Run check.py.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["prepare", "compare", "merge"])
    ap.add_argument("--root")
    a = ap.parse_args()
    root = L.find_root(a.root)
    if a.command == "prepare":
        cfg, snap = load(root)
        for f in ("figma-fingerprints.json", "figma-compare.json", "figma-details.js", "figma-details.json", "figma-read.json"):
            (root / ".sync" / f).unlink(missing_ok=True)
        nonce = secrets.token_hex(6)
        js = script(root, cfg, snap, "fingerprints", nonce=nonce)
        (root / ".sync" / "figma-read.js").write_text(js)
        (root / ".sync" / "figma-read-tag.json").write_text(json.dumps({"nonce": nonce, "snapshot": snap["synced_at"]}) + "\n")
        print(f"Wrote .sync/figma-read.js ({len(js) // 1024} KB, tag {nonce}). Run it with use_figma for file "
              f"{(cfg.get('figma') or {}).get('file_key')}, then run `figma_read.py compare`.")
    elif a.command == "compare":
        compare(root)
    else:
        merge(root)


if __name__ == "__main__":
    main()
