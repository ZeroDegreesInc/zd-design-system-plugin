"""Refresh the documentation on Figma's Styles pages after token values change (a port, or a sync write).

The Colors page belongs to /color-cleanup. This covers the other Styles pages: every foundation file's
Figma page except the one documenting color.

  python3 -I docs.py prepare [--root DIR]
      Writes .sync/docs-read.js. Run it with use_figma. It only reads.
  python3 -I docs.py plan [--root DIR]
      Finds that result and prints the dry run: text to bind to its variable, stale text to rewrite,
      type specimens showing the wrong style, and what it can't fix. Saves .sync/docs-plan.json.
  python3 -I docs.py write [--root DIR] [--skip NODE ...] [--accept NODE ...]
      Writes .sync/docs-write.js from the plan, leaving out any node the designer struck, and including
      only the check-by-eye suggestions they accepted. Run it.
  python3 -I docs.py finish [--root DIR]
      Reports what Figma accepted, and removes the scripts.

Old values come from the snapshot (the last state everyone agreed on), new ones from the live file, so it
catches any token that moved, whoever moved it.
"""
import argparse
import json
import re
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "ds-sync" / "scripts"))
import synclib as L
from figma_read import find_result

NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
STYLE_WORD = re.compile(r"\b(thin|light|regular|book|medium|semi ?bold|bold|extra ?bold|black|heavy|italic)\b", re.I)   # a label naming a style


def doc_pages(root):
    """The Figma pages foundation files document, except color's."""
    out = []
    for p in sorted((root / "foundations").glob("*.md")):
        fm = L.frontmatter(p.read_text())
        page = (fm.get("figma") or {}).get("page")
        if page and p.stem != "color" and page not in out:
            out.append(page)
    return out


def prepare(root):
    (root / ".sync" / "figma-docs.json").unlink(missing_ok=True)       # a saved result from an earlier read or write
    nonce = secrets.token_hex(6)
    old = {n: v for n, v in old_values(root).items() if isinstance(v, (int, float, str)) and not (isinstance(v, str) and v.startswith("#"))}
    js = (HERE / "docs.template.js").read_text().replace("__INPUT__", json.dumps({"mode": "read", "nonce": nonce, "pages": doc_pages(root), "old": old, "exclude": L.load_config(root).get("exclude") or []}, ensure_ascii=False))
    (root / ".sync" / "docs-read.js").write_text(js)
    (root / ".sync" / "docs-tag.json").write_text(json.dumps({"read": nonce}) + "\n")
    print(f"Wrote .sync/docs-read.js ({len(js) // 1024} KB). Run it with use_figma for file {L.load_config(root)['figma']['file_key']}, then run `docs.py plan`.")


def old_values(root):
    """Each token's plain value as the snapshot last agreed it, aliases followed."""
    snap = json.loads((root / ".sync" / "snapshot.json").read_text())
    proj = L.snapshot_projections(snap["tokens"], L.load_config(root))

    def plain(n, depth=0):
        v = (proj.get(n) or {}).get("value")
        if isinstance(v, str) and v.startswith("@") and depth < 10:
            return plain(v[1:], depth + 1)
        return v
    return {n: plain(n) for n, p in proj.items() if p.get("kind") == "var"}


def fence(x):
    """A pattern for x on its own: a number not inside a longer number (1023 in 768–1023px), a word not inside a longer word."""
    return rf"(?<![\d.]){re.escape(x)}(?![\d.])" if re.fullmatch(r"\d+(\.\d+)?", x) else rf"(?<![\w-]){re.escape(x)}(?![\w-])"


def num(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def plan(root):
    tag = json.loads((root / ".sync" / "docs-tag.json").read_text())
    got = find_result(root, "docs", tag["read"])
    r = lambda x: round(x, 4) if isinstance(x, float) else x        # Figma stores 1.2 as 1.2000000476
    live = {n: r(t["v"]) for n, t in got["tokens"].items()}
    styles = got["styles"]
    old = {n: r(v) for n, v in old_values(root).items()}
    # a number against a name (a weight stored as 700 in the snapshot and "Bold" in Figma) isn't a change
    changed = {n: (old[n], v) for n, v in live.items() if n in old and old[n] is not None and v is not None and old[n] != v
               and isinstance(old[n], str) == isinstance(v, str)}
    numeric = {n: got["tokens"][n]["v"] for n in live if got["tokens"][n]["t"] == "n" and isinstance(live[n], (int, float))}
    ops, notes, kept, bindable = [], [], 0, {}

    for page in got["pages"]:
        for board, B in page["boards"].items():
            kept += B["bound"]
            mentioned = set(B["mentions"])
            where = f"{page['name']} › {board}"
            texts = B["texts"]

            # type specimens: a sample whose family follows a variable, with the label naming its style
            specimen_ids = set()
            for t in texts:
                if not (t.get("fv") and t.get("f") and t.get("lab")) or not STYLE_WORD.search(t["lab"]):
                    continue
                specimen_ids.add(t["id"])
                family, want = live.get(t["fv"]), t["lab"]
                key = lambda x: x.lower().replace(" ", "").replace("regularitalic", "italic")      # fonts call regular italic "Italic"
                match = next((st for st in styles.get(family, []) if key(st) == key(want)), None)
                if not match:
                    notes.append(f"{where}: {family} has no {want} style, so its specimen can't show it. Hide that slot or relabel it.")
                elif t["f"]["style"] != match:
                    ops.append({"node": t["id"], "kind": "specimen", "fontName": {"family": family, "style": match}, "family_var": t["fv"],
                                "where": f"{where} › {want}", "before": f"{t['f']['family']} {t['f']['style']}", "after": f"{family} {match}"})
                else:
                    kept += 1

            for t in texts:
                if t["id"] in specimen_ids:
                    continue
                text = t["s"]
                m = NUMBER.match(text)
                if m:
                    # a bare number standing for a token the board names. Figma's plugin API can't bind a number
                    # variable to text (its UI can), so: rewrite it when the token moved, and list it to bind by hand
                    val = float(m.group(1))
                    cands = [n for n, v in numeric.items() if v == val] + [n for n, (o, _) in changed.items() if isinstance(o, (int, float)) and o == val]
                    cands = sorted(set(c for c in cands if c in mentioned))
                    if len(cands) == 1:
                        n = cands[0]
                        bindable.setdefault((page["name"], n), []).append(t["id"])
                        if n in changed and changed[n][0] == val:
                            ops.append({"node": t["id"], "kind": "text", "characters": num(live[n]), "where": f"{where} › {t['at']}",
                                        "before": text.strip(), "after": num(live[n]), "because": f"`{n}` {num(val)} → {num(live[n])}"})
                        else:
                            kept += 1
                    elif len(cands) > 1:
                        notes.append(f"{where} › {t['at']}: “{text.strip()}” could be any of {', '.join(f'`{c}`' for c in cands)}. Check it by hand.")
                    continue
                # running text: replace an old value of a changed token with its new one. Old values are often
                # common numbers (an old 32 isn't every 32), so it's automatic only when the board names the
                # token in the same text; otherwise it's a suggestion the designer checks by eye. A number a still-valid
                # token named on the board accounts for (24 beside space-m) is left alone
                in_text = {m for m in mentioned if m in text}
                explained = {live[m] for m in mentioned if m not in changed}     # values a valid token on this board accounts for
                subs, why, sure = {}, [], True
                ranged = "–" in text or "-" in text
                for n, (o, nv) in changed.items():
                    if isinstance(o, (int, float)) and isinstance(nv, (int, float)):
                        if o in explained:
                            continue
                        pairs = [(num(o), num(nv))] + ([(num(o - 1), num(nv - 1))] if ranged else [])   # a range ending just below the old value
                    elif isinstance(o, str) and isinstance(nv, str) and not o.startswith("#"):
                        pairs = [(o, nv)]
                    else:
                        continue
                    hit = False
                    for a_, b_ in pairs:
                        if re.search(fence(a_), text):
                            if subs.get(a_, b_) != b_:          # two moved tokens shared this old value: can't tell which
                                subs[a_] = None
                            else:
                                subs[a_] = b_
                            hit = True
                    if hit:
                        why.append(f"`{n}` {num(o)} → {num(nv)}")
                        sure = sure and n in in_text
                if any(v is None for v in subs.values()):
                    notes.append(f"{where} › {t['at']}: “{text.strip()[:60]}” holds a value several moved tokens had. Check it by hand.")
                    continue
                # every replacement at once, on the original text, so 14 → 16 isn't then taken on to 18
                new_text = re.sub("|".join(fence(a_) for a_ in sorted(subs, key=len, reverse=True)),
                                  lambda m: subs[m.group(0)], text) if subs else text
                if new_text != text:
                    ops.append({"node": t["id"], "kind": "text" if sure else "check", "characters": new_text, "where": f"{where} › {t['at']}",
                                "before": text, "after": new_text, "because": ", ".join(why), "instance": bool(t.get("i"))})

    (root / ".sync" / "docs-plan.json").write_text(json.dumps({"ops": ops, "notes": notes, "bind_by_hand": {f"{pg}|{n}": ids for (pg, n), ids in bindable.items()}},
                                                              indent=1, ensure_ascii=False) + "\n")
    out = [f"Documentation on {len(got['pages'])} Styles pages (the Colors page is /color-cleanup's).",
           f"Tokens that moved since the last sync: {', '.join(f'`{n}` {num(o)} → {num(v)}' for n, (o, v) in sorted(changed.items())) or 'none'}.", ""]
    for kind, title in (("text", "Rewrite stale text"), ("specimen", "Fix type specimens"),
                        ("check", "Check by eye: an old value appears, but the text doesn't name its token. Written only with --accept")):
        rows = [o for o in ops if o["kind"] == kind]
        if rows:
            out += [f"## {title} ({len(rows)})", "", "| Node | Where | Before | After |", "| --- | --- | --- | --- |"]
            out += [f"| `{o['node']}` | {o['where']} | {o['before'].replace(chr(10), ' / ')} | {o['after'].replace(chr(10), ' / ')}"
                    + (f" ({o['because']})" if o.get("because") else "") + " |" for o in rows]
            out.append("")
    if notes:
        out += ["## Can't fix", ""] + [f"- {n}" for n in notes] + [""]
    if bindable:
        out += ["## Bind by hand (optional)", "",
                "These labels state a token's value as typed text. Bound to the variable instead, they'd update by themselves. "
                "Figma's plugin API can't make that binding, but the UI can: select the layers, then apply the variable to their text.", ""]
        out += [f"- **{pg}:** {len(ids)} label(s) showing `{n}`: " + ", ".join(f"`{i}`" for i in ids[:6]) + (" …" if len(ids) > 6 else "")
                for (pg, n), ids in sorted(bindable.items())] + [""]
    out.append(f"Already right: {kept} text layers (bound to a variable, or a specimen showing its style). {len(ops)} edit(s) to approve.")
    print("\n".join(out))


def write(root, skip, accept):
    p = json.loads((root / ".sync" / "docs-plan.json").read_text())
    (root / ".sync" / "figma-docs.json").unlink(missing_ok=True)       # the read's saved result; the write's comes next
    ops = [o for o in p["ops"] if o["node"] not in set(skip) and (o["kind"] != "check" or o["node"] in set(accept))]
    nonce = secrets.token_hex(6)
    js_ops = [{k: o[k] for k in ("node", "bind", "characters", "fontName", "family_var") if k in o} for o in ops]
    js = (HERE / "docs.template.js").read_text().replace("__INPUT__", json.dumps({"mode": "write", "nonce": nonce, "ops": js_ops}, ensure_ascii=False))
    (root / ".sync" / "docs-write.js").write_text(js)
    tag = json.loads((root / ".sync" / "docs-tag.json").read_text())
    tag["write"] = nonce
    (root / ".sync" / "docs-tag.json").write_text(json.dumps(tag) + "\n")
    print(f"Wrote .sync/docs-write.js: {len(ops)} edit(s){f', {len(skip)} struck' if skip else ''}. Run it with use_figma, then run `docs.py finish`.")


def finish(root):
    tag = json.loads((root / ".sync" / "docs-tag.json").read_text())
    got = find_result(root, "docs", tag["write"])
    print(f"Figma accepted {len(got['done'])} edit(s).")
    if got["errors"]:
        print("Refused:\n" + "\n".join(f"- `{e['node']}`: {e['error']}" for e in got["errors"]))
    for f in ("docs-read.js", "docs-write.js", "docs-tag.json", "docs-plan.json", "figma-docs.json"):
        (root / ".sync" / f).unlink(missing_ok=True)
    print("Run `docs.py prepare` and the read again to confirm nothing is left.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["prepare", "plan", "write", "finish"])
    ap.add_argument("--root")
    ap.add_argument("--skip", nargs="*", default=[], help="nodes the designer struck")
    ap.add_argument("--accept", nargs="*", default=[], help="check-by-eye suggestions the designer approved")
    a = ap.parse_args()
    root = L.find_root(a.root)
    {"prepare": lambda: prepare(root), "plan": lambda: plan(root), "write": lambda: write(root, a.skip, a.accept), "finish": lambda: finish(root)}[a.command]()


if __name__ == "__main__":
    main()
