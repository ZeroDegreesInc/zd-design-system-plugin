---
name: brand-intake
description: Audit a client's existing brand from their live website, or from their brand guidelines alone when there's no site (plus any notes they provide), and turn it into an intake doc for the design system, published as one doc with three tabs. It covers a web and design-system audit, a token proposal in the system's own token names (intake/proposed-tokens.json, ready for /port-tokens), and a backlog of component changes and new components the site needs. Measures real computed styles across several pages at desktop and mobile widths. Use when someone runs /brand-intake, starts a new client, says "audit this brand", "pull the brand from their site", "what would it take to move this site onto our system", or shares a client URL or brand guide.
---

# /brand-intake

Turns a client's current brand into one doc with three tabs, each kept as its own working file in `ROOT/intake/`:
1. **Audit** (`audit.md`): what the site does today, from a design and accessibility point of view. It observes; it doesn't fix.
2. **Proposal** (`proposal.md`): the brand mapped onto the system's tokens, area by area, for the designer to review and approve. The values live in `proposed-tokens.json`, which /port-tokens reads.
3. **Backlog** (`backlog.md`): changes to the system's components, and new components this site needs.

The markdown files are the source; the doc is published from them (step 8). The skill writes only to `ROOT/intake/` and the doc, never to Figma, tokens.json or the system's markdown. `intake/` isn't part of the system, so sync ignores it.

Read `design-system/FORMATS.md` (the proposal format, under `intake/proposed-tokens.json`) and the system's foundation and component files before writing the proposal or the backlog. The proposal has to use this system's names, and the backlog has to know what already exists.

Scripts live in `scripts/` and reuse /ds-sync's and /port-tokens' (`../ds-sync/scripts/`, `../port-tokens/scripts/`). Run them with `python3 -I`. `ROOT` is the folder holding `design-system.config.yaml`.

## Inputs

- **The site's URL.** Required, unless there's no site: then work from the guidelines alone (see *Guidelines only* below).
- **Brand guidelines** (PDF, link or images), if the client has them. Read them before writing. The audit should say where the site departs from its own guidelines.
- **Specific pages** worth including, if the person names them. Otherwise you choose them by template (step 1).
- **Notes** from the client, such as "the red is our only brand color" or "we're dropping the serif".

## Steps

1. **Choose the pages by template.**
   - **Map the site.** Open the URL in the browser and run `scripts/map.js` as is. It lists the site's pages grouped by the kind of template they probably use, and names other sites the pages link to repeatedly, such as a separate news site.
   - **Pick one page of each kind** the site actually has:
     - the home page;
     - a section landing page;
     - a standard content page;
     - a listing (news, events, programs, products);
     - a detail page (an article, program, event or product);
     - a page with a form;
     - anything distinctive to this site, such as a people directory or a finder.

     Prefer pages people use most. Use the map's guesses as a starting point and check the ones that matter, because kinds are guessed from paths and link text. That's usually 6–9 pages; a small site may have only 3 kinds.
   - **Put the most important templates first.** The first four are also scanned at mobile width.
   - **Note what can't be covered.** Content on another domain or behind a login can't be scanned from here. If it matters, it needs its own intake.

   Then prepare the scan:
   ```
   python3 -I <skill>/scripts/intake.py prepare --root ROOT --url URL --client "Client name" \
     --page "section landing=URL" --page "content=URL" ... [--not-covered "what and why"]
   ```
   Each `--page` is `kind=URL`, in priority order. Without `--page`, the scan picks pages itself in the same spirit: one of each kind it can recognise, then one per remaining section, `--pages` in all (default 6). It writes `intake/scan.js`, and `audit.md`, `proposal.md` and `backlog.md` from the templates if they don't exist yet.

2. **Scan the site.** Open the URL in the browser, then run the whole of `intake/scan.js`, unchanged, with the browser's JavaScript tool.
   - **What it does:** it loads each page in a hidden same-origin frame at 1440px, and the first four again at 390px. It measures computed styles, and notes whether each page has a form of its own or one built by a form tool. It returns one JSON result and changes nothing on the site.
   - **Don't copy the result anywhere.** The next step finds it in the session log.
   - **Cookie banners:** don't accept or dismiss them. That's the person's consent to give. The scan doesn't need them gone.
   - **If a page reports an error:** the site may block framing, or the page needs a login. Rerun `prepare` with other `--page` choices.

3. **Collect and analyse:**
   ```
   python3 -I <skill>/scripts/intake.py collect --root ROOT
   python3 -I <skill>/scripts/intake.py analyse --root ROOT
   ```
   `analyse` writes `intake/analysis.json` and prints the findings to write from. It reports:
   - **Color:** palette with roles and contrast, brand candidates, near-duplicates, color scales and named roles the site defines as CSS variables;
   - **Contrast:** text contrast as actually used;
   - **Type:** type by role at both widths;
   - **Layout:** spacing, radii, shadows and breakpoints compared with the system's tokens;
   - **Components:** button and input styles, and patterns found on the pages;
   - **Accessibility:** controls whose focus outline is removed with nothing in its place, heading structure, missing alt text.

   If `collect` can't find the result, save the browser's result as `intake/scan-raw.json` and run it again.

4. **Look at the site.** Scroll through the home page and one or two inner pages in the browser and take screenshots. The numbers miss things only eyes catch:
   - photos behind headlines;
   - what the patterns really are;
   - which components are one-offs;
   - how the brand feels.

   Check anything the analysis finds suspicious, such as a contrast failure that may be text over a photo or another panel. The scan already skips controls marked disabled (attribute, `aria-disabled`, or a `disabled` class), but one disabled only by script or styling still needs ruling out. When a failure turns out not to be one, record it in `intake/review.json`:
   ```json
   {"contrast_ruled_out": [{"fg": "#ffffff", "bg": "#e7f4fc", "reason": "the quote banner is white on a blue panel; the scan read the pale tint behind it"}]}
   ```
   The doc then lists it as "checked by eye and ruled out", with the reason, instead of as a failure.

5. **Write the proposal**, `intake/proposed-tokens.json`, in the system's token names (see FORMATS.md). Work from the site's own CSS variables first, because the brand's team chose them, then from measured use.
   - **Brand scale.** Use the site's main interactive color and its own hover, matched to the roles each step plays in the system (read the semantic tokens that reference it). `analyse` suggests a scale in the system's lightness pattern when the site has none.
   - **Neutrals.** Map them step for step by role: headline, body, placeholder, borders, alternate surfaces. Keep each role's contrast.
   - **Status colors.** Keep the system's unless the brand's own pass as text.
   - **Never carry an accessibility failure into the system.** Darken or replace the value, and say so in Decisions.
   - **Fonts:** the families and weights the site loads.
   - **Type ramp:** from the site's heading sizes, at Desktop and Mobile. Round font sizes to whole pixels: a site's 19.2px (1.2rem) is proposed as 19. Then run `intake.py ramp --root ROOT`. It completes every step you sized:
     - **Tablet sizes**, when the system has a Tablet mode. Each step keeps the kit's own Tablet-to-Desktop ratio, rounded to an even pixel, between Mobile and Desktop and at least 2px above the next heading down. The site is never measured at tablet width, so present these as derived, in the Typography area.
     - **Line height and paragraph spacing** at every mode, from the system's ratios.
     It only fills what's missing. `check` refuses a sized step without its full ramp, or a line height or paragraph spacing that doesn't follow its ratio, because port would otherwise leave the old values in place.
   - **Spacing, radius, breakpoints:** change them only when the site is consistent enough to be worth adopting.
   - **Additions:** brand colors with no home in the system, such as a secondary or accent scale, go under `$additions`. Propose new semantic roles only when the site clearly uses the color for a job.

   Then check it:
   ```
   python3 -I <skill>/scripts/intake.py check --root ROOT
   ```
   This runs /port-tokens' own validation and accessibility checks without Figma. Fix every error, and resolve or explain every warning and contrast failure before going on.

6. **Fill the generated sections:**
   ```
   python3 -I <skill>/scripts/intake.py render --root ROOT
   ```
   It writes the tables between the sync markers: the audit's evidence and sources in `audit.md`, and each area's token tables in `proposal.md`. Run it again whenever the proposal changes.

7. **Write the rest of the three files**, replacing each `<!-- -->` prompt:
   - **Audit findings** (`audit.md`): the audit observes; it doesn't propose fixes. Fixes belong in the proposal's decisions and the backlog. There are two tables, rows most severe first:
     - **Design findings:** what a designer would notice, such as color roles, type, spacing and shape, states, component consistency, and how the site follows its own guidelines.
     - **Accessibility findings:** what a disabled user would hit, such as focus visibility, heading structure, alt text, text over imagery, text size and contrast. The measured contrast is generated above this table, so don't repeat its numbers. Leave out code hygiene and third-party tools unless they cause an accessibility problem.

     Keep each cell short so the tables scan:
     - **Severity:** High, Medium or Low.
     - **Finding:** a bold claim plus one sentence.
     - **Evidence:** a number or example, with page numbers.

     Put each issue in one table only. Aim for 4–7 design rows and 3–6 accessibility rows.
   - **The proposal, area by area** (`proposal.md`). It's laid out as Color, Typography, Spacing and shape, Layout and Other, so the designer can review one area at a time. In each area:
     - a **decisions table**: Decision (bold label plus the key values) and Why (one sentence, naming the finding it answers in italics). Say where it departs from the site.
     - **open questions** as bullets, one per choice the designer has to make, each starting with a bold label. They aren't checkboxes: a question is settled by a decision, not ticked. Leave the heading out if there are none.

     The token tables and approval box are generated, so leave them alone. Delete the Other area if the proposal changes nothing there.
   - **The backlog** (`backlog.md`):
     - Changes to the system's existing components, with evidence and priority.
     - New components, with where they appear and what they build on.
     - Patterns that don't need a component (third-party embeds, one-offs, things existing pieces cover).

     Keep the backlog to what this site needs, and remember the starter kit stays minimal: it's a list for the client project, not for the kit.
   - **Summary** (top of `audit.md`): written last. A few sentences on the brand and how consistently the site carries it, then three bullets: the biggest design issue, the biggest accessibility issue, and what the proposal does.

8. **Publish the doc.**
   ```
   python3 -I <skill>/scripts/intake.py export --root ROOT
   ```
   This writes clean copies to `intake/publish/`, without frontmatter, prompts or sync markers.
   - **The first time:** create the doc with the docs tools, named "Brand intake: <client>", with three tabs in this order: **Audit**, **Proposal**, **Backlog**.
     - Put the date and author byline under the Audit tab's title.
     - Open the doc for the person.
     - Fill one tab per call with that tab's exported markdown, minus its first `#` line.
     - Save the doc's link, its id, and each tab's id and body id in `intake/doc.json`.
   - **Later:** update only what changed. Replace one proposal area, or one audit section, rather than re-sending a whole tab, so comments elsewhere stay attached.

9. **Report.** Link the doc. Lead with the summary and the High design and accessibility findings. Then say what's needed before porting:
   - the designer's review of the proposal, area by area, and its open questions;
   - fonts active in Figma;
   - the team's agreement on the backlog order.

## Guidelines only

When there's no site, for a new brand or a client still building one, the intake works from the guidelines alone. The three files, the proposal, `check`, `render`, `export` and the doc work as above; only the evidence changes.

1. **Prepare:** `intake.py prepare --root ROOT --guidelines --client "Client name"`. It writes the three files, with the guidelines version of the audit, and an empty `intake/guidelines.json`.
2. **Read the guidelines** (PDF, pages or images): every color with a screen value, the pairings they name or show, typefaces and weights with the use given to each, any sizes, and anything else a screen needs. Record them in `guidelines.json` (see FORMATS.md). Leave out what isn't a token, such as logo rules and tone of voice, and say so in `not_covered`. If the brand has a separate digital design system, leave it out unless the person asks for it, and say so: the point is what the guidelines give.
3. **Analyse:** `intake.py analyse` measures each color with white and with the guidelines' darkest neutral, checks the named pairings, and suggests a brand scale from the color marked `primary`.
4. **Write the proposal** as in step 5, from the guidelines' own values. Where the guidelines allow tints, use them to fill the system's steps, and match each step's contrast. Where they say nothing (sizes, spacing, states), the system's values stay, and the decisions say why.
5. **Render, then write the rest** as in steps 6 and 7, with these differences:
   - **The audit observes the guidelines:** what a designer building screens would need and not find, and claims the numbers don't bear out, such as a palette called accessible whose colors can't all carry text. Quote briefly and cite the source by number.
   - **What the guidelines cover:** a table of the areas a system needs, each Yes, Partly or No, with a short note.
   - **The backlog:** only what the guidelines themselves require; a site scan later adds the rest.
6. **Publish and report** as in steps 8 and 9.

## Reviewing the proposal

The designer works through the Proposal tab one area at a time, with Claude.
- **Start by reading the tab.** People tick boxes and edit in the doc. Read the Proposal tab (its changes since your last read) and carry any ticks or edits back into `proposal.md`, so the files and the doc agree.
- **Questions or changes:** when they answer an open question or ask for a change ("make the error red deeper", "keep Lora for paragraphs"):
  1. update `proposed-tokens.json`;
  2. record the answer in that area's decisions table in `proposal.md`, and delete the question it settles;
  3. run `intake.py check` and fix anything it raises;
  4. run `intake.py render` and `intake.py export`;
  5. replace that area in the Proposal tab, from its heading to the next.
- **Comments:** a comment on the doc is a request like any other. Act on it as above, then reply in its thread.
- **Approval:** tick the area's **approved** box only when the designer says so, never on their behalf.
- **Edits to other areas:** a change can affect another area. A new brand color changes the contrast checks, for example. If an area the designer already approved now shows different values, untick it and say why.
- **Status:** `intake.py check` ends with the review status, read from `proposal.md`: each area approved or open, and the open questions. So bring the doc's ticks into the file first.
- **Ready:** the proposal is ready for /port-tokens when every area is approved and no questions are open.

## After the intake

- **Review:** see above. The designer can also edit `proposed-tokens.json` directly, then run `intake.py render`.
- **Port:** /port-tokens writes the approved proposal into Figma.
- **Sync:** /ds-sync carries it into the markdown.
- **Build:** backlog items become /build-component runs, one at a time.

## Notes for maintainers

- **The scan measures, the person judges.** `scan.template.js` reports computed styles and markup patterns. Pattern detection uses class names and roles, so its counts are a guide. The doc says so, and step 4 exists because of it.
- **Background detection.** Text counts as "on an image" when a CSS background image, or an `img`, `video` or `canvas` laid behind it, is found while walking up to its first solid background. Text the same color as its background is treated the same way.
- **Page kinds are guesses** from paths and link text, in `map.js` and in the scan's automatic choice. Both share the same `KINDS` list; change them together.
- **Buttons are measured as seen.** The scan descends into a single inner element when the outer one has no fill, padding or border, because many sites style an inner `span`.
- **Results come back through the session log**, matched by the nonce in `scan.js`, like /ds-sync's Figma reads.
- **The files are the source, the doc is the view.** Scripts only read and write the markdown; Claude moves changes between the files and the doc with the docs tools.
- **The proposal check is port's.** `intake.py check` and the proposal section call `port.plan(..., offline=True)`, so intake and port can never disagree about a proposal.
