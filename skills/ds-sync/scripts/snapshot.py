"""Write or refresh .sync/snapshot.json.

  python3 -I snapshot.py --refresh-local [--root DIR]
      Rewrites tokens, icons and the markdown file records from the local files,
      keeping the stored components and Figma details. Use only when every place
      is known to agree, such as right after a successful sync.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import synclib as L


def local_parts(root):
    tokens = json.loads((root / "tokens.json").read_text())
    snap_tokens = {name: {"id": node.get("$extensions", {}).get("figma", {}).get("id"), "group": group, "path": path, "node": node}
                   for name, (group, path, node) in L.token_nodes(tokens).items()}
    icons = json.loads((root / "icons.json").read_text())
    snap_icons = {"library": icons["library"],
                  "icons": {i["name"]: {"figma_node": i["figmaNode"], "glyph": i["glyph"]} for i in icons["icons"]}}
    files = L.markdown_files(root)
    h = __import__("hashlib").sha256()
    for p in files:
        h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
    md = {"commit": None, "fingerprint": "sha256:" + h.hexdigest(),
          "files": {str(p.relative_to(root)): L.file_record(p) for p in files}}
    return snap_tokens, snap_icons, md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--refresh-local", action="store_true")
    a = ap.parse_args()
    root = L.find_root(a.root)
    path = root / ".sync" / "snapshot.json"
    snap = json.loads(path.read_text())
    if not a.refresh_local:
        ap.error("nothing to do; pass --refresh-local")
    snap_tokens, snap_icons, md = local_parts(root)
    snap["version"] = 2
    snap["synced_at"] = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    snap["places"]["markdown"] = md
    snap["tokens"] = snap_tokens
    snap["icons"] = snap_icons
    path.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    print(f"snapshot: {len(snap_tokens)} tokens, {len(snap_icons['icons'])} icons, "
          f"{len(snap['components'])} components, {len(md['files'])} markdown files")


if __name__ == "__main__":
    main()
