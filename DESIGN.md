---
name: WIDER
description: A free, local tool that leads a researcher through a systematic literature review, one step at a time.
colors:
  blue: "#2563eb"
  blue-wash: "#eff6ff"
  violet: "#6d28d9"
  violet-wash: "#f5f3ff"
  stone: "#57534e"
  stone-wash: "#f5f5f4"
  rose: "#be123c"
  rose-wash: "#fff1f2"
  cyan: "#0e7490"
  cyan-wash: "#ecfeff"
  amber: "#b45309"
  amber-wash: "#fffbeb"
  green: "#15803d"
  green-wash: "#f0fdf4"
  paper: "#f9fafb"
  sheet: "#ffffff"
  ink: "#111827"
  graphite: "#5f6673"
  rule-line: "#e5e7eb"
  field-line: "#8b909a"
typography:
  headline:
    fontFamily: "Public Sans, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.45
  title:
    fontFamily: "Public Sans, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 500
    lineHeight: 1.45
  section-label:
    fontFamily: "Public Sans, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    letterSpacing: "0.06em"
  body:
    fontFamily: "Public Sans, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  body-small:
    fontFamily: "Public Sans, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Public Sans, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.45
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  code: "4px"
  field: "6px"
  control: "8px"
  card: "8px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  page-bottom: "48px"
components:
  button-primary:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.sheet}"
    rounded: "{rounded.control}"
    padding: "7px 14px"
    typography: "{typography.body}"
  button-quiet:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "7px 14px"
  button-small:
    rounded: "{rounded.control}"
    padding: "3px 8px"
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "7px 9px"
  card:
    backgroundColor: "{colors.sheet}"
    rounded: "{rounded.card}"
    padding: "16px"
  chip:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.graphite}"
    rounded: "{rounded.pill}"
    padding: "0 8px"
  state-chip-done:
    backgroundColor: "{colors.green-wash}"
    textColor: "{colors.green}"
    rounded: "{rounded.pill}"
    padding: "1px 10px"
  state-chip-ready:
    backgroundColor: "{colors.blue-wash}"
    textColor: "{colors.blue}"
    rounded: "{rounded.pill}"
    padding: "1px 10px"
  nav-link:
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "7px 8px"
  nav-link-current:
    backgroundColor: "{colors.blue-wash}"
    textColor: "{colors.blue}"
    rounded: "{rounded.control}"
    padding: "7px 8px"
  banner:
    backgroundColor: "{colors.stone-wash}"
    rounded: "{rounded.card}"
    padding: "10px 14px"
  toast:
    backgroundColor: "{colors.sheet}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
---

# Design System: WIDER

This file records the look WIDER ships (`wider/ui/static/style.css`). Since design pass A1 (18 Oct 2026) the font
and the colour and status tokens below are the shipped ones, on every page; the sidebar of steps and the top bar are on
every page too. The Deduplication page is the first page through the full pass; other pages keep their layout until
their own pass (A2 and later).

## Decided direction for A1 (author, 18 Oct 2026)

The author looked at eleven sketches in three rounds and chose
**`combo-flow-filter.html`**: a clean, familiar web-app look with the flow chart as the filter.

- **Look:** white pages on a very light grey, near-black text (#111827), one blue (#2563eb) for the single main
  button, the current page and links. Soft 8px corners, thin grey borders and a faint shadow on cards. The author
  named its clarity as the reason.
- **Colour (author, 18 Oct):** asked for "a bit more color in the design", kept violet, dropped yellow, then asked
  for three status colours and chose option 4 of four palettes:
  - **blue** (#2563eb) for you and the page's main action;
  - **violet** (#7c3aed) for what WIDER did on its own ("by WIDER", the notebook count);
  - **warm grey** (stone, #57534e on #fafaf9) for open items not yet done;
  - **rose red** (#be123c on #fff1f2, #e11d48 for the strongest mark) for what needs you: waiting for your
    decision, duplicate, open questions, the differences marked between two records;
  - **cyan** (#0e7490 on #ecfeff) for unsure and other in-between states;
  - **green** (#16a34a) for done.
  Every status still carries a word and an icon. The page-level "Waiting for you" label is a small tinted chip, not
  a solid bold block (author: too big and bold). The colour of each tone's text on its wash must pass 4.5:1 when A1
  builds it.
- **Font:** Public Sans, chosen by the author on 18 Oct from seven variants of the chosen sketch
  (Inter, IBM Plex Sans, Source Sans 3, Atkinson Hyperlegible Next, Noto Sans
  and Inter with a serif for the records were the others). WIDER fetches nothing from outside, so A1 ships the font
  files with WIDER (Public Sans is under the SIL Open Font License) and falls back to the system font; never a link
  to an online font service.
- **Page layout for a list of things to decide:** steps in the left sidebar; a top bar with the breadcrumb, "Step N of
  10", the guide and the notebook behind buttons, and the page's one main button; below the page title, the step's
  counts drawn as a PRISMA-style flow chart whose boxes are buttons that filter the list (for example "57 removed
  automatically" lists what WIDER removed on its own so the researcher can check it); then the list on the left and
  one item large on the right, field by field with both abstracts and the differences marked, big decision buttons
  with their keyboard keys, and that item's own history underneath.
- **Notebook:** the dated list of every change to the step (You or WIDER) is available behind a "Notebook" button with
  its count, not always open (author: "no need for notebook to be always open just be available"). Each item shows
  its own few entries inline.
- **Flow chart:** the same chart goes into the methods report; its caption says so. The message the page must give
  (for example "12 pairs need your decision…") sits beside the chart instead of in a separate banner.
- Kept from today: the feel **calm and exact**, the North Star's dated-and-signed record, status never by colour
  alone, one main action per page.

## Overview

**Creative North Star: "The Lab Notebook"** (chosen by the author, 17 Oct 2026)

A careful researcher's notebook: plain paper, exact numbers, every entry dated and signed, nothing decorative. The
pages are off-white paper with white sheets (cards) laid on them, ruled by thin grey lines. Blue marks you, what to
do next and where you are; violet marks what WIDER did on its own; stone, rose, cyan, amber and green say open, needs
you, unsure, needs review and done. Depth comes from lines and washes, almost never from shadows.

The feel is **calm and exact** (author, 17 Oct 2026): thin lines, soft corners, little colour, and (the goal for A1)
one strong button per page. Density is moderate: body text at 15px, tables and secondary text at 13px, so a long
session of reading counts and decisions stays comfortable. It is a working tool for a review that lasts weeks, so
the interface stays out of the way of the records.

**Key Characteristics:**
- Off-white paper, white sheets, grey rules; a matching dark theme follows the system setting.
- Blue for you and the one main action per page, violet for WIDER, five status tones.
- Every status has a word and an icon, never colour alone.
- Public Sans, shipped with WIDER as font files; nothing is fetched from an online font service.
- Flat: no shadows, no gradients, almost no motion.

## Colors

Seven tones, each a text colour on a pale wash, plus neutrals. Each tone's text passes 4.5:1 on its wash, and field
borders and the focus ring pass 3:1, in the light and the dark theme (`tests/unit/test_style_tokens.py`). Dark-theme
values follow in brackets.

### Tones
- **Blue** (#2563eb on #eff6ff; dark #93b4f8 on #1b2643): you and the main action. The page's one main button
  (`--blue-fill` #2563eb with white text in both themes), the current page in the sidebar, links, the focus ring.
- **Violet** (#6d28d9 on #f5f3ff; dark #c4b5fd on #2a2142): what WIDER did on its own (an automatic merge, a run).
- **Stone** (#57534e on #f5f5f4; dark #d6d3d1 on #292524): open, not started, idle; quiet button hover.
- **Rose** (#be123c on #fff1f2; dark #fda4af on #3b1720): needs you (pairs to decide, open questions) and errors.
- **Cyan** (#0e7490 on #ecfeff; dark #67e8f9 on #0f2f38): unsure, and warnings.
- **Amber** (#b45309 on #fffbeb; dark #fcd34d on #3a2a0c): needs review, always with the diamond icon and the words
  "needs review" (a finished result whose inputs changed since: the ranking after new labels, a step whose earlier
  step ran again). It never means an error or a refusal (ui-redesign D5). Also on Paper and Sheet at 4.5:1.
- **Green** (#15803d on #f0fdf4; dark #86efac on #122d1c): done.

### Neutral
- **Paper** (#f9fafb; dark #111318): the page background and the inside of fields.
- **Sheet** (#ffffff; dark #1b1e24): cards, the sidebar, the record drawer, toasts, quiet buttons.
- **Ink** (#111827; dark #e5e7eb): all main text.
- **Graphite** (#5f6673; dark #9ca3af; 4.5:1 on every tinted surface, audit 5.6): secondary text and labels.
- **Rule Line** (#e5e7eb; dark #2a2d34): borders and table rules.
- **Field Line** (#8b909a; dark #6b7280): field and quiet-button borders (3:1).

The old names (`--accent`, `--ok`, `--warning`, `--danger` and others) are aliases of these tones until A2.

### Named Rules
**The Blue Is You Rule.** Blue marks you, your next step and where you are; violet marks WIDER. Nothing decorative is
blue.

**The Status Has Words Rule.** No colour stands alone: every status (`window.Status` in `shell.js`: open, waiting,
duplicate, not duplicate, unsure, WIDER, done, needs you, blocked) has a word and an icon.

## Typography

**Body Font:** Public Sans (400, 500, 600, 700), shipped in `wider/ui/static/fonts/` under the SIL Open Font
License (`OFL.txt`), with the system sans-serif as fallback
**Mono Font:** the system monospace (ui-monospace, SFMono-Regular, Menlo), for codebook text and keys

**Character:** a single family in a few sizes, like a notebook written in one pen. Nothing is fetched from a font
service; the font files are served by WIDER itself.

### Hierarchy
- **Headline** (bold, 22px): the page title, one per page, next to its state chip.
- **Title** (500, 16px): project names on Home cards.
- **Section label** (bold, 15px, uppercase, 0.06em tracking, Graphite): the heading of each card.
- **Body** (400, 15px, line-height 1.45): explanations, the "why" box at the top of a page (14px there).
- **Body small** (400, 13px): tables, field labels, the project Info panel.
- **Label** (400, 11 to 12.5px): chips, badges, the top bar's step pill, small buttons, menu section titles.

### Named Rules
**The One Pen Rule.** One font family for all text; size and weight make the hierarchy, not new typefaces.

## Layout

- **Shell:** a sticky left sidebar (200px, folds to 52px icons) and a main column up to 1132px wide, centred, with
  16px side padding and 48px at the bottom.
- **Top bar:** above every page inside a project, a full-width bar with a bottom rule: the breadcrumb (project /
  page), "Step N of M" on stage pages, then on the right the Notebook and Guide buttons and the page's one main button
  where the page has them.
  Its colours are its own scoped tokens: `--chrome` (#111827; dark #05070a) behind `--on-chrome` (#f9fafb) text,
  `--chrome-muted` (#cbd5e1) for separators, `--chrome-line` (white at 28%) for button borders and `--chrome-hover`
  (white at 10%); the Guide button is outlined in the dark-theme blue (#93b4f8) and the main button keeps the blue fill.
- **Page head:** title and state chip on one line, then the "why" box (up to 900px wide) behind "Explain this step".
- **Content:** cards in an auto-fit grid (columns of at least 340px, 16px gap); Home uses project cards in columns of
  at least 300px with a 14px gap. Forms on Home are capped at 720px.
- **Record drawer:** a 320px panel fixed on the right; the page makes room for it. Below 900px wide it covers the
  full width.
- **Toasts:** bottom right, up to 420px wide, above the drawer.
- **Rhythm:** 4, 8, 12 and 16px steps; card padding 14 to 16px; rows of controls wrap with an 8px gap. Wide tables
  scroll inside their card instead of widening the page.

## Elevation & Depth

Flat. Depth is shown by sheets (white cards) on paper, 1px rule lines and pale washes. There is one ring, not a
shadow: a chosen picker card gets a 1px Blue ring around its border. Dialogs dim the page behind them with
a 35% black backdrop.

### Named Rules
**The Paper Rule.** No drop shadows and no gradients. If something must stand out, it gets a line, a wash or a left
bar.

## Shapes

Soft, small corners: 4px on inline code, 6px on fields, 8px on buttons, menu items and toasts, 12px on cards,
banners and dialogs, and full pills for chips and badges. Status containers share one form: a 1px border with a
thicker left bar (4px; 5px on the "next step" panel) in the status colour. Progress is drawn as thin 6px bars with
3px corners; step numbers sit in 22 to 26px circles.

## Components

### Buttons
- **Shape:** gently rounded (8px).
- **Main:** Blue fill (#2563eb) with white text in both themes, 7px by 14px padding. Exactly one per page (D32 F15):
  the step's own main action (marked `main-act`), or, once the step is done, "Continue to <next step>" at the end of
  the "What you can do here now" box, when the page's own main button turns into an outline. Deduplication and PRISMA
  reporting keep theirs in the top bar. Settings has none.
- **Quiet:** Sheet background, Ink text, a Field Line border, Stone wash on hover; for side actions ("Explain this
  step", Notebook, Guide).
- **Secondary:** transparent with a Blue border and text, 14px; the step's own actions besides the main one.
- **Text link:** `link-btn`, underlined Blue text, 14px, no border; rare actions. Where a page or header has several,
  they sit under a "More" disclosure (Concepts, Search results).
- **Chosen:** a chosen card shows a "Chosen" chip, not a button.
- **Small:** 3px by 8px, 12px text, for row actions.
- **Icon button:** 32px square, transparent, Graphite icon; Stone wash on hover.
- **Disabled:** half opacity.
- **Focus:** a 2px Blue outline, 2px away from the edge, on every focusable element.

### Chips
- **Style:** pill, 1px Rule Line border, Graphite text on Sheet, 11.5 to 12px.
- **State chips:** each status in its tone on its wash with its icon and word ("done" green, "ready" blue, "needs you"
  rose, "unsure" cyan, "needs review" amber, open stone).
- **Where the model runs:** green border for this computer, cyan for your network or online.

### Cards / Containers
- **Corner Style:** 8px.
- **Background:** Sheet on Paper; result cards and banners use a tone wash with a 4px left bar.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px Rule Line; the open project's card has a Blue border.
- **Internal Padding:** 14 to 16px.

### Inputs / Fields
- **Style:** 1px Field Line border (3:1), Paper background, 6px corners, 7px by 9px padding; the label sits above in 13px
  Graphite.
- **Focus:** the shared 2px Blue outline.
- **Error:** shown in a red box with a red left bar at the top of the page (read out by screen readers), not on the
  field itself.

### Navigation
- **Sidebar of steps:** the project box (name, "Open project" back to Home), then the four numbered sections, each
  number marked done (a check), current or upcoming. One link per step with an icon and a mark on the right: a green
  check when done, a blue dot when it is the next step, a ring otherwise; a step that is done but still has work for
  you adds a rose "needs you" mark, and one whose result needs review an amber diamond with "needs review". The current
  page has a Blue wash background and blue text. The footer says "N things need you" (to the Overview) or "Nothing
  waits for you." and "Next:" the recommended step; there is no steps-done count or bar (ui-redesign D11). Folded, only icons remain; every link keeps its name for screen readers.
- **Top bar:** see Layout; it replaces the stepper.

### Next-step panel (signature)
A Sheet box with a Blue border and a 5px blue left bar that says what to do next on the page.

### Status bar family (signature)
Banners, toasts, result cards, open questions, policy boxes and error boxes all share the 1px border plus coloured
left bar; the bar colour is the message's tone (stone, green, cyan, rose, blue).

### Project card
Each Home card says what needs you ("1 needs you", rose with its icon, or "Nothing waits for you"), any running job
and "Next:" the recommended step in plain text; no progress bar and no stage count (ui-redesign D11).

## Do's and Don'ts

### Do:
- **Do** use blue only for you, the next step, the current place and links (The Blue Is You Rule); violet for WIDER.
- **Do** give every status a word and an icon (The Status Has Words Rule).
- **Do** show depth with lines, washes and left bars, not shadows (The Paper Rule).
- **Do** ship fonts as files; nothing is loaded from an online font service.
- **Do** keep the PRISMA diagram black on white in dark mode, since it goes into manuscripts.
- **Do** respect reduced motion: the only animation (the running-job bar) stops when the system asks.
- **Do** give every page one main button and outline, quiet or text-link buttons for the rest.

### Don't:
- **Don't** put white text on a light accent: the main button keeps its dark blue fill (#2563eb) in the dark theme.
- **Don't** use the gold verdict colour (#b8860b) behind white text (3.3:1); it is a one-off outside the palette.
- **Don't** add a colour pair without adding it to `tests/unit/test_style_tokens.py`.
