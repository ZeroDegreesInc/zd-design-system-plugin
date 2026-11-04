---
name: build-component
description: Add a new component to the design system, or change an existing one, so Figma and the markdown agree from the start. Writes the component's markdown file first as the spec, waits for approval, builds it in Figma with the system's own recipe (hidden master, token bindings only, naming rules, an Examples frame), then hands it to /ds-sync, which links the file to the Figma component and starts tracking it. One component at a time, often a backlog item from /brand-intake. Use when someone runs /build-component, says "add a component", "build the X component", "we need a tabs / modal / table component", "add a variant to X", or picks an item from the backlog.
---

# /build-component

Takes one component from idea to tracked part of the system, in a fixed order:

1. **Spec:** the component's markdown file, written first.
2. **Approval:** the person approves the spec before anything is built.
3. **Build:** in Figma.
4. **Sync:** /ds-sync links the file to what was built, fills its generated sections from it, and starts tracking it.

The order matters. A spec written after the build only describes what happened to get built. Written first, it's the thing the build is checked against, and the person approves a page of text instead of reviewing a Figma file.

This skill has no scripts of its own. The checks are /ds-sync's.

## Before you start

- **Read** `design-system/FORMATS.md` (component property names, markdown files, hidden components), `_templates/component.md`, `README.md` and `design-system.config.yaml`.
- **Study two or three existing components closest to the new one,** both their files and their Figma boards. Copy how they're built: the new one should look like it was always there.
- **Check that everything agrees.** Run /ds-sync's check (its steps 2–5). If anything is pending, sync it or set it aside first, so this component's changes aren't mixed with others.
- **Confirm the Figma file.** The skill builds in `figma.file_key` from the config. When testing, point the config at a copy of the file, and work in a copy of the markdown folder too.

## Steps

1. **Scope it.** One component, or one change to an existing one.
   - **Is it needed?** If an existing component covers it, or a new variant of one would, say so. The starter kit stays minimal, so prefer a variant to a new component, and a new component to a new part.
   - **New component, new variant, or part?** A new public component gets its own file. A new variant changes an existing file. A hidden part shared by several components gets its own file only when more than one component uses it (see "Hidden components" in FORMATS.md).
   - **From the backlog:** use the item's evidence (pages, examples, sample layouts) as the brief.

2. **Write the spec.** For a new component, create `components/<name>.md` from the template, with the name in kebab case.
   - **Frontmatter:**
     - `name`: the Figma component set name, in Title Case.
     - `status: draft`.
     - `figma.page` and `figma.node`: leave as `"0:0"` until step 5.
     - `figma.internal`: the hidden masters and parts you plan, each with its role.
     - `tokens` and `styles`: what it will bind.
     - `related`: the components it builds on or sits beside.
   - **Summary:** the exact words you'll put in the Figma component set's description.
   - **Hand-written sections,** in full: When to use and Not for, Behavior, Accessibility, Do and don't. Accessibility names the element and role, keyboard support, labeling, and the contrast pairs it depends on.
   - **Generated sections,** as a plan: the anatomy, properties, sizes and bindings tables, written the way the finished ones read. Sync replaces them with what's actually built in step 6.
   - **Rules:**
     - Property names and option order follow FORMATS.md.
     - Colors are semantic tokens, never primitives. Text uses text styles; shadows and focus use effect styles.
     - Markdown never states a token's value. A measurement with no token is marked *(no token)*.
   - **Index:** add the file's line to the Components table in `README.md`.
   - **For a change to an existing component:** edit its hand-written sections. Then list the planned changes under a short heading at the end of the file, "Planned changes", and leave its generated sections alone.

3. **Get approval.** Show the person the spec with a short summary:
   - the properties and how many variants they make;
   - the tokens and styles it binds;
   - the contrast pairs it depends on, with their ratios;
   - anything you chose that they might not expect.

   Make their changes in the file, and build only after they say go ahead.

4. **Build it in Figma.** Load the Figma plugin's /figma-use skill before the first `use_figma` call. Build in small steps, and take a screenshot after each step to check it.
   - **Where:** on a component page listed in `figma.component_pages`, on a board laid out like its neighbours, with an Examples frame. The Examples frame holds:
     - instances in reading order;
     - frames named `Usage / <Pattern name>` with a caption, for components that are used together.
   - **Structure:** the public component set varies only by the choices people make, such as Priority, Status or State. When sizes, icons or text options would multiply its variants, put them on a hidden `.<Name>Master` wrapper inside it, as Button, Input and Badge do.
   - **Bindings:**
     - Every fill, stroke, text color, padding, gap, corner radius and icon size is bound to a variable.
     - Text uses text styles; shadows and focus use effect styles.
     - No raw values. The one exception is Focus variants: they keep a white fill at 1% opacity, because `Effects/Focus Ring` doesn't render on a frame with no fill.
   - **Naming:** variant properties, options and their order follow FORMATS.md, and the default variant is the first option of each.
   - **Description:** put the spec's summary on the component set, word for word.
   - **If the build has to depart from the spec,** update the spec, and tell the person why.

5. **Link the file.** Write the Figma page, the component set's node and each hidden part's node into the file's frontmatter. Then run /ds-sync's check:
   - The component shows as **"new: its file joins sync"**. Sync adopts a file whose Figma node it doesn't track yet.
   - Fix any **problems** (a node that doesn't exist, a misnamed property) in Figma or the file.
   - Fix any **contrast failure** in the build before going on. Never carry one into the system.

6. **Carry it across.** Run /ds-sync's steps 7–10: plan, apply, the pending edits, then finish.
   - **The pending edits:** rewrite the planned anatomy, properties, sizes and bindings tables so they match what was built, exactly.
   - **Finish:** it refuses while a table doesn't name a token or style the component uses. Once it passes, the snapshot tracks the component and its file.
   - **For a change to an existing component:** sync reports it as an ordinary Figma change. Delete the "Planned changes" list once the generated sections match.

7. **Close.**
   - **Run the check again.** It should report that everything agrees.
   - **Status:** leave the file at `status: draft` until the person says it's ready, then set it to `stable`.
   - **Backlog:** if the work came from `intake/backlog.md`, mark the item done there.
   - **Report:** link the file and the Figma board. Name the variant count, the tokens it binds, and anything that departs from the spec.

## Code

The starter kit has no coded library yet, so this skill builds in Figma only.

## Notes for maintainers

- **Adoption lives in /ds-sync.** `synclib.new_component_files` finds component files whose `figma.node` the snapshot doesn't track. `figma_read.py` reads those components in full. `check.py` reports them as "new: its file joins sync", and `sync.py finish` records their node in the snapshot. A file left at `"0:0"` is skipped, so a spec can sit unbuilt without blocking sync.
- **The recipe is the starter kit's.** If a project builds components differently, change step 4 rather than the steps around it.
