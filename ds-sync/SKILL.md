---
name: ds-sync
description: Keep a design system's places (Figma, the markdown folder, and code when present) in agreement. Compares each with the last sync snapshot, reports what changed and where (including contrast failures), carries Figma's changes into the markdown (tokens.json, icons.json, foundation and component files), and writes markdown's token, style and description changes into Figma, each after the person approves. Use when someone runs /ds-sync, asks "is Figma in sync with the markdown", "what changed since the last sync", "check for drift", "sync Figma to the docs", wants a pre-handoff or pre-PR check, or has just edited tokens, components or docs in one place.
---

# /ds-sync

Compares Figma and the markdown folder with `.sync/snapshot.json`, the last state where both agreed. Whatever moved since then is the change.

Three parts:
- **Check** (steps 1–6): read-only. It reports what changed and where.
- **Figma → markdown** (steps 7–11): carries Figma's changes into the markdown, after the person approves.
- **Markdown → Figma** (steps 12–15): writes markdown's changes into Figma, after the person approves.

When both sides have changes, run either direction first, then the check again before the other: each one moves the snapshot.

Read `design-system/FORMATS.md` first if you haven't this session; it defines every file this skill reads.

Scripts live next to this file in `scripts/`. Run them with `python3 -I`. `ROOT` below is the folder holding `design-system.config.yaml` (usually `design-system/`).

## Steps

**Start from the latest.** If the project is a git repository, switch to `main` and pull (`git switch main && git pull`) before anything else, so this run starts from what's on GitHub. If there are uncommitted changes, stop and ask what to do with them; never switch over them. Without git, skip this.

1. **Find the project.** Locate `design-system.config.yaml` and read `figma.file_key`. If `.sync/snapshot.json` is missing, stop: there's no agreed state to compare against. Say so, and that a first sync has to set one.

2. **Prepare the Figma read:**
   ```
   python3 -I <skill>/scripts/figma_read.py prepare --root ROOT
   ```
   It writes `ROOT/.sync/figma-read.js`: a fixed script of about 12 KB, tagged for this read.

3. **Read Figma.** Open a Figma session for `figma.file_key`, then run the whole of `.sync/figma-read.js`, unchanged, as the `use_figma` code.
   - It only reads.
   - It returns fingerprints for every token, style and component part, plus icons, new components and missing nodes.
   - **Don't copy the result anywhere.** The next step finds it in the session log by its tag.
   - If the file isn't reachable, run step 5 with `--no-figma` and say Figma wasn't checked.

4. **Compare:**
   ```
   python3 -I <skill>/scripts/figma_read.py compare --root ROOT
   ```
   - **If nothing needs details**, it writes `.sync/figma-read.json` and you go straight to step 5.
   - **Otherwise** it writes `.sync/figma-details.js`, a small script naming only what differs. Run that with `use_figma` the same way, then:
     ```
     python3 -I <skill>/scripts/figma_read.py merge --root ROOT
     ```
   - **If either step says it can't find the result**, save the `use_figma` result exactly as returned to the file it names, and run the step again.

5. **Run the check:**
   ```
   python3 -I <skill>/scripts/check.py --root ROOT
   ```
   It prints the report and saves it to `ROOT/.sync/last-check.md`. Exit code 0 means everything agrees, 1 means changes to sync, and 2 means problems to fix first.

6. **Report to the user.**
   - Lead with the headline line, then the problems and conflicts, then the change table.
   - Link `.sync/last-check.md` for the full report.
   - Don't restate unchanged items.

## What the report means

| Section | Meaning |
| --- | --- |
| Contrast | Text and icon colors that fall below WCAG AA against the fill they sit on, found from every variant's bindings (nested components included, disabled variants exempt). "Figma only" or "Markdown only" says which side's pending change brings it in. Doesn't block sync; tell the person. |
| Problems | Broken links: a token listed in frontmatter that doesn't exist, a Figma node a file names that's gone, a component deleted in Figma but still documented. Sync must not run until these are fixed. |
| Changes, "Changed in: Figma" | Figma moved; markdown is behind. Sync would update `tokens.json`, `icons.json` or the component file's generated sections. A component marked "new: its file joins sync" is one /build-component just built: its file names a Figma node the snapshot doesn't track yet, and sync adopts it. |
| Changes, "Changed in: Markdown" | `tokens.json`, `icons.json` or a component summary moved; Figma is behind. Steps 12–15 write it into Figma. |
| Changes, "Both" | Changed in both places. If they match, there's nothing to do. Otherwise it's a conflict, and the person decides which wins. |
| Introduction rows | The Introduction page in Figma (`figma.intro_page`) changed: a section written, edited, cleared, renamed, added or removed. Sync rewrites `design.md`'s generated block from it, so there's nothing to edit by hand. "new: design.md joins sync" is the first time, when the snapshot has no Introduction yet. A hand edit to that block alone is a warning: the change belongs in Figma. |
| Foundation rows | A foundation file's generated sections no longer match `tokens.json`. `_scripts/render.py` regenerates them. |
| Foundation checks | Rows from the foundation files' own checks (type ramp, spacing order, grid fit) that fail with the values in `tokens.json`, worked out fresh by `render.py --fails`. They usually mean a token changed without the tokens that follow from it, such as a font size without its paragraph spacing. Fix the values at their source and sync; sync doesn't change token values to make a check pass. |
| Hand-written text | Lines that disagree with `tokens.json` as it stands, whatever changed it: a contrast figure written beside the two colors it measures, or a value written beside a token's name (`992px … /* breakpoint-md */`, `` `space-m` (24px) ``). Checked on every run, so a line an earlier sync missed still shows. Sync doesn't rewrite these: correct each line by hand, keeping its wording. |
| Warnings | Generated sections edited by hand (sync would overwrite them), new Figma components with no file, names in prose that aren't tokens, or hand-written contrast figures that a tokens.json change has made wrong. |
| Notes | Hand-written edits. Nothing to sync. |

## Carrying Figma's changes into markdown

Only after the person has seen the report and said to go ahead.

7. **Plan.** Run this; it writes nothing:
   ```
   python3 -I <skill>/scripts/sync.py plan --root ROOT
   ```
   Show the person the plan. It lists:
   - tokens to update, add, rename or delete;
   - icon updates;
   - component files to update;
   - contrast failures the sync would bring in. Point these out before applying;
   - anything skipped.
   - **Conflicts are skipped** unless the person decides Figma wins. Add `--take-figma "NAME"` for each, to plan and apply alike. For `design.md`, the name is `design.md`.
   - **New icons are never added**, because each needs a glyph chosen by a person.

8. **Apply:**
   ```
   python3 -I <skill>/scripts/sync.py apply --root ROOT [--take-figma "NAME" ...]
   ```
   It writes what a script can write safely:
   - `tokens.json` (round-trip tested against every existing token) and `icons.json`;
   - foundation sections, by running `render.py`;
   - for each changed component file: the summary, missing frontmatter tokens and styles, and `last_synced`.
   It then lists the remaining edits in `ROOT/.sync/pending-edits.md`, in two kinds:
   - **Component sections** to update to match Figma.
   - **Hand-written text to review**: lines quoting a contrast figure the run changed (with the new figure, one per figure written), pairings a component no longer uses, new pairings the file never mentions, and lines that compare a token whose value changed or write its old value ("matching `text-m`", `1024px /* breakpoint-md */`).

9. **Make the pending edits.** For each component in `pending-edits.md`:
   - **Read the component file, then edit only the named sections** so they match the listed Figma changes. For example, change the Primary / Hover row's fill from `actions/hover` to `actions/default`.
   - **Keep the file's existing wording and table layout.** Change values, not structure, unless a variant or property was added or removed.
   - **Don't touch hand-written sections**, except the lines a "Hand-written text" item lists. For those, correct the figure or token name and keep the wording. Add a new pairing next to the file's other contrast figures. If a listed line was already right, leave it.
   - **Tick the item's box** (`- [x]`) once the file matches.

10. **Finish:**
    ```
    python3 -I <skill>/scripts/sync.py finish --root ROOT
    ```
    It refuses while a box is unticked, a summary doesn't match Figma, or a table row for a changed variant still names the old token. Fix what it lists and run it again. When it passes, it moves the snapshot forward for everything this run carried across, and only those items. Skipped conflicts and markdown-side changes keep showing up.

11. **Confirm.** Run steps 2–5 again. It's normally one small read, with no details call, and the rows this run handled should be gone. Tell the person what changed, file by file, and what's still pending.

## Carrying markdown's changes into Figma

For "Changed in: Markdown" rows. Only after the person has seen the report and said to go ahead. It needs a Figma read from this session (steps 2–4), so it never writes over a Figma change it hasn't seen.

12. **Plan.** Run this; it writes nothing:
    ```
    python3 -I <skill>/scripts/figma_write.py plan --root ROOT
    ```
    Show the person the plan:
    - **Written to Figma:** variables and styles to rename, create, update or delete, in full from `tokens.json`; component descriptions from edited summaries.
    - **For a person to do in Figma:** edited bindings, properties or anatomy, and icon glyph changes. Sync carries the result back afterwards.
    - **Conflicts are skipped** unless the person decides the markdown wins. Add `--take-markdown "NAME"` for each, to plan and prepare alike.
    - **Deletions remove the variable or style from Figma.** Say so plainly; anything still bound to it loses the binding.
    - A rename is blocked while any file still names the old token. Rename it everywhere in the markdown first.

13. **Prepare:**
    ```
    python3 -I <skill>/scripts/figma_write.py prepare --root ROOT [--take-markdown "NAME" ...]
    ```
    It regenerates stale foundation sections (markdown only), then writes `.sync/figma-write.js`.

14. **Write.** Run the whole of `.sync/figma-write.js`, unchanged, as the `use_figma` code. It renames first, then creates, then sets every field, then deletes. It returns which items Figma accepted and any it refused. Don't copy the result anywhere.

    **Then refresh the documentation in Figma** if any token's value changed: /port-tokens' step 6 (`colors.py` for the Colors page, `docs.py` for the other Styles pages). Do it before finishing, because it compares Figma with the snapshot and finishing moves the snapshot forward.

15. **Finish:**
    ```
    python3 -I <skill>/scripts/figma_write.py finish --root ROOT
    ```
    It writes the ids of new variables and styles into `tokens.json` and moves the snapshot forward for what Figma accepted, and only that. Refused items stay in the report with Figma's reason. Then confirm with steps 2–5: Figma should match with no details call.

Never edit `snapshot.json` by hand. Never run `snapshot.py --refresh-local` to clear a report; it's only for when every place is known to agree.

## Notes for maintainers

- **Modes.** A mode added in Figma reaches `tokens.json` through each token's `modes`; `synclib.sync_group_modes` then adds it to the collection group's `modes` list, which `render.py` reads to build its columns. Numbers are rounded to 4 places on the way in (`norm`), because Figma stores 32-bit floats: 1.2 comes back as 1.2000000476837158.
- **`design.md` comes from the Introduction page.** The read collects every frame on `figma.intro_page` holding a `heading` and a `body` text, in page order (`intro` in the read). The snapshot keeps that list as `intro`; `synclib.intro_diff` reports what moved per section, `render_intro` turns it into the block (a body starting "TODO" is shown as not written, several lines become a list) and `write_design` replaces only the block, keeping the file's hand-written head. `finish` refuses if the block doesn't match a fresh render. The read script avoids `\u` and `\n` escapes in that code: pasting the script through a tool call can turn them into real characters and break it.
- **New components join by their file.** A component file whose `figma.node` isn't in the snapshot (and isn't `"0:0"`) is adopted: `figma_read.py` reads that component in full, `check.py` reports every part as new, and `sync.py finish` records its node in the snapshot. Sync never creates a component file itself; /build-component writes it.

- **Fingerprints are FNV-1a over canonical JSON.** `synclib.py` and `figma_read.template.js` implement them identically. Change both together, and re-run the parity test (fingerprint every snapshot item in Python and Node, and expect zero mismatches).
- **Figma computes fingerprints; Python compares them.** The script carries no snapshot data, so it stays small and nearly the same on every run. Results are read from the Claude Code session log (`~/.claude/projects/*/*.jsonl`), matched by the random tag `prepare` and `compare` put in each script. Saving a result to `.sync/figma-fingerprints.json` or `.sync/figma-details.json` overrides that.
- **Tokens are compared as projections** (`synclib.project` and the template's equivalent): value, modes, description, scopes and visibility for variables, plus each style's own fields. IDs only catch renames.
- **Contrast comes from bindings** (`synclib.contrast_pairs`): each `.color` against the nearest `.fill` above it, blending fills with opacity, else `surfaces/primary`. Layers hidden only in some variants (an unchecked check) are skipped; ones hidden in every variant are property-toggled and checked. Large text (24px, or 18.66px bold, at its smallest mode) needs 3:1. `synclib.prose_review` recomputes figures quoted in hand-written lines (two decimals, like `7.51:1`) from the pairings the line names, giving each written figure its own pairing, and flags non-color tokens whose value changed when the line compares them or writes an old value. `synclib.stale_text` runs on every check against the current tokens: it only checks a figure whose pairing the text makes clear (two colors before it, or one plus the pairing before), so vague lines are skipped, not flagged. Test both against the original folder, which should report nothing, and by replaying a port (old tokens from before it, new after) over the pre-port docs.
- **The writer is the reader in reverse.** `figma_write.template.js` turns each projection back into Figma values, so a correct write reads back with the same fingerprint. Opacity aliases are `{color: alias, opacity: percent}`, durations are seconds, and font-weight variables keep their spelling (`Semibold`) while text styles load the font's own (`Semi Bold`). Renames follow into component bindings in the snapshot, since bindings name tokens.
- **Components are compared per part** (description, properties, default variant, one per variant), so a report can name the exact variant and binding that changed.
