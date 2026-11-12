---
name: port-tokens
description: Write an approved token proposal (intake/proposed-tokens.json, usually from /brand-intake) into the Figma variables (or into tokens.json first, when someone asks for that), so a rebrand can be reviewed on real components before /ds-sync carries it into the markdown. Changes values and adds new tokens, scales and styles set up like the system's own. Shows every value old → new, each addition, the colors that follow through references, contrast and accessibility checks and missing fonts before writing anything. Use when someone runs /port-tokens, says "apply the brand tokens", "port the proposal", "put the new brand colors into Figma", or has edited proposed-tokens.json and wants it in the system.
---

# /port-tokens

Takes `intake/proposed-tokens.json` and writes its values and additions into the Figma variables. A rebrand is approved by looking at it, so Figma comes first.

**Markdown first, only when asked.** If the person directly asks for the proposal to go into the markdown first, add `--to markdown` to `plan` and `prepare`: port writes `tokens.json` instead, and /ds-sync's markdown → Figma steps carry it into Figma afterwards. Never choose it yourself.

**Port writes to one place only.** It never touches the snapshot. Afterwards, /ds-sync sees that place changed and carries the change to the others, together with any tweaks the designer makes after looking.

Read `design-system/FORMATS.md` first if you haven't this session. It defines the proposal format.

Scripts live in `scripts/` and share /ds-sync's (`../ds-sync/scripts/`). Run them with `python3 -I`. `ROOT` is the folder holding `design-system.config.yaml`.

## Steps

**Start from the latest.** If the project is a git repository, switch to `main` and pull (`git switch main && git pull`) before anything else, so this run starts from what's on GitHub. If there are uncommitted changes, stop and ask what to do with them; never switch over them. Without git, skip this.

1. **Start from agreement.** Port refuses tokens with unsynced changes, so it needs a current check. Run /ds-sync's steps 1–5 (prepare, read Figma, compare, check). With `--to markdown`, the offline check (`check.py --no-figma`) is enough. There's no need to sync anything unrelated first; port only looks at the tokens in the proposal.

2. **Plan.** This writes nothing:
   ```
   python3 -I <skill>/scripts/port.py plan --root ROOT [--from FILE] [--to markdown]
   ```
   The proposal defaults to `ROOT/intake/proposed-tokens.json`. The script refuses a proposal it can't port and lists each problem:
   - unknown names outside `$additions`, and changes to styles;
   - malformed values, and references to tokens that don't exist;
   - additions in a group the system doesn't have, named against its pattern, or missing a description the group's other tokens all have.
   Fix them in the proposal, or ask whoever wrote it.

3. **Review with the designer.** Show the plan as it comes:
   - **Changes**: every token, old → new, including mode values. A mode that matched the default follows the new value; one that differed keeps its own, and the plan says so.
   - **Additions**: each new token or style, its value, its collection, and the existing token it's set up like.
   - **Remove from Figma**: additions an earlier port made that this proposal drops. They're removed only if they were never synced.
   - **Colors that follow**: semantic tokens whose color changes because they reference a changed primitive. This is usually where a rebrand really shows.
   - **Accessibility of the additions**:
     - each new primitive color's contrast on the page and under `content/light`, and what it's fit for;
     - each new semantic color against the pairings its role implies (content on the page, text on a surface, an action as text and as a fill, a border on the page) and the pairings its description names.
   - **New contrast failures**, in any component or any addition. Don't port past these without the designer deciding: change the proposal, or port and fix in Figma.
   - **Warnings**: a new scale that doesn't run light to dark or uses different steps, text under 12px, or an addition that repeats an existing value.
   - **Fonts** the text styles will need, when a family or weight changes.
   Wait for a clear go-ahead.

4. **Prepare:**
   ```
   python3 -I <skill>/scripts/port.py prepare --root ROOT [--from FILE] [--to markdown]
   ```
   - **Figma** (the default): writes `ROOT/.sync/port.js`. Run the whole of it, unchanged, as the `use_figma` code for `figma.file_key`. It checks the fonts first and writes nothing if any is missing. It then creates the additions in their collections (a new style goes into its folder), sets every value, removes dropped additions, and returns which items Figma accepted. Don't copy the result anywhere.
   - **`--to markdown`**: writes `tokens.json` and regenerates the foundation sections. Then run /ds-sync's markdown → Figma steps (12–15) to write the values into Figma, and continue at step 6.

5. **Finish** (Figma only):
   ```
   python3 -I <skill>/scripts/port.py finish --root ROOT
   ```
   It records the run in `.sync/port-<time>.json` and lists anything Figma refused.

6. **Refresh the documentation in Figma.** The Styles pages document the tokens, and only the parts bound to a variable update by themselves. Do this before /ds-sync: it finds stale text by comparing Figma with the snapshot, and sync moves the snapshot forward.
   - **The Colors page:**
     ```
     python3 -I <skill>/scripts/colors.py prepare --root ROOT
     ```
     Run `.sync/colors-read.js` with `use_figma` (it only reads), then `colors.py plan --root ROOT`. It checks every swatch on the page, not only what this run changed, so drift from hand edits is caught too. Show the designer the dry run, which leads with what's wrong in the harmful direction:
     - **Verdicts that say Pass but fail,** then verdicts that say Fail but pass. `16pt` is small text at 4.5:1, `24pt` large text at 3:1, each for black and for white text.
     - **Renamed tokens,** then hex or alias labels. A primitive's swatch shows its hex; a semantic token's shows the primitive it points at. Names follow the page's own convention, inferred from the swatches that already agree.
     - **Label colour:** the Text Color variant switches to whichever of black or white reads better, split into "unreadable now" and "readable, but the other is better". Close calls keep the designer's choice.
     - **Color Scales:** a family whose anchor or members moved gets its fifteen-step ramp regenerated (see `scales.py`). A ramp made by an earlier generator is regenerated only if the designer approves that family by name; one from another tool is left alone.
     - **New:** a swatch for every colour variable that has none, placed after its siblings. A new colour family also gets a palette row (its description is left for the designer to write) and a scale, anchored on the same step as the others.
     - **Left alone:** unbound swatches, translucent ones such as shadows (reported if their label disagrees with the variable), and ramp steps whose fill is bound.

     Then `colors.py write --root ROOT [--skip ID|VARIABLE ...] [--keep-colour ID ...] [--approve-family NAME ...]`, run `.sync/colors-write.js`, `colors.py finish`, and prepare and read again: the plan should come back with nothing to do.
   - **The other Styles pages:**
     ```
     python3 -I <skill>/scripts/docs.py prepare --root ROOT
     ```
     Run `.sync/docs-read.js` with `use_figma` (it only reads), then:
     ```
     python3 -I <skill>/scripts/docs.py plan --root ROOT
     ```
     Show the designer the dry run. It has six parts:
     - **Add typeface sections:** a font family with no section on the Typography page, such as a newly added mono face, gets one. It's copied from the last section there (the family's name, a character sample and its weights), with every layer pointed at the new family's variable and the label renamed. Afterwards, the read-again below fixes any weight sample the new family shows wrongly.
     - **Rewrite stale text:** running text that names a moved token and still states its old value, and number labels that show a moved token's old value, such as a grid's "24" beside `space-m`.
     - **Fix type specimens:** a weight sample showing the wrong style after a family change, such as "SemiBold" rendering Bold.
     - **Check by eye:** text holding an old value without naming its token, such as a breakpoint range. Often right, sometimes a coincidence. Nothing here is written unless the designer accepts it.
     - **Can't fix:** what needs a person, such as a weight the new family doesn't have, or a board explaining how a scale was built that the new values no longer follow.
     - **Bind by hand (optional):** number labels that state a token's value as typed text. Figma's plugin API can't bind a number variable to text, but its UI can: select them and apply the variable, and they'll update by themselves from then on. Until then, this step keeps them right.
   - **Write what they approve:**
     ```
     python3 -I <skill>/scripts/docs.py write --root ROOT [--skip NODE ...] [--accept NODE ...]
     ```
     `--skip` strikes an edit, including a typeface section (by the node the plan lists); `--accept` takes a check-by-eye suggestion. Run `.sync/docs-write.js`, then `docs.py finish`, then prepare and read again: the plan should come back empty apart from what was struck or can't be fixed.

7. **Hand over.**
   - **Look:** ask the designer to review the components in Figma and adjust anything there. That's the point of porting to Figma first.
   - **Sync:** when they're happy, run /ds-sync. It reports the ported tokens as "Changed in: Figma" and carries them, plus any tweaks, into `tokens.json` and the markdown. After a markdown-first port, sync has already carried the values; it picks up only the designer's tweaks.
   - **Review lines:** sync's review list will name any hand-written contrast figures the new values made wrong.

## Notes for maintainers

- **The Colors page is properties, not text.** Everything written on a `.Color Swatch` is a component property (`Color Name`, `Hex`, the four `16pt`/`24pt` verdicts) and the label colour is its `Text Color` variant, so `colors.py` sets properties and never touches text layers or fills on a swatch. The only fills it writes are the Color Scales steps, which are pasted rectangles.
- **The scale generator is checked against the starter kit.** `python3 -I scales.py check` regenerates the six reference ramps (set 2026-10-20) and must match every step exactly. Change the rules there and nowhere else.
- **Documentation: bound text is best, rewriting is the fallback.** Text bound to a variable never goes stale. Figma's plugin API refuses to bind a number variable to text (`variable of resolved type 'FLOAT' cannot be bound to 'characters'`), though the UI allows it, so `docs.py` rewrites number labels matched to a token named on the same board, and lists them for binding by hand. Only text in the documentation font family (the config's `exclude`, such as `documentation/*`) is treated as documentation. Text set in a system family is a specimen. Figma caps a script's result at 48 KB, so the read filters in Figma: bare numbers some token holds, text holding a moved token's old value, and specimens.

- **Port reuses /ds-sync's pieces.** It builds the new projections with `synclib`, writes through `figma_write.template.js`, and reads results with `figma_read.find_result`. A change to those formats changes port too.
- **Existing tokens: values only.** Their names, descriptions and scopes stay as they are. Renames and deletions are /ds-sync's job (markdown → Figma), where every file that names the token is checked.
- **Additions follow the system, not the kit.** Everything an addition gets (settings, naming pattern, whether it needs a description) is read from the system's own neighbouring tokens, so port works the same on any system built this way. The contrast roles in `ROLE_PAIRS` (`content`, `surfaces`, `actions`, `borders`) are the one place that assumes the kit's semantic names; a system with other roles adds them there, and well-described tokens are checked through their description either way.
- **Collections aren't created.** A new group means a new Figma collection with its own modes. That's rare and structural, so it's done by hand.
