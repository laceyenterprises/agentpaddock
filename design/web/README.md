# Agent Paddock web prototype (PDW design reference)

These files are the approved design of the public Agent Paddock site, exported
from the operator-reviewed design canvas on 2026-11-03 (canvas version 25, after
roughly 30 rounds of operator comments). They are the visual and interaction
contract for the PDW build pack: each page ticket rebuilds one or more of them
as real static pages generated from the committed results tree.

They are Design Component files (`*.dc.html`): HTML with `{{hole}}` bindings,
`<sc-for>` / `<sc-if>` control flow and a `class Component extends DCLogic`
script whose `renderVals()` holds the page's sample data and interaction logic.
They need the design-canvas runtime (`support.js`, not included) to render, so
read them as source, not as a website. The numbers inside are a snapshot of the
eval bench on 2026-11-03; the real site computes every number from `results/`.

| Prototype file | Real route | Ticket |
|---|---|---|
| `Main.dc.html` (+ `HomeDesktop`) | `/` | PDW-04 |
| `Learn.dc.html` (+ `LearnDesktop`) | `/learn/` | PDW-05 |
| `Races.dc.html` (+ `RacesDesktop`) | `/races/` | PDW-06 |
| `Race.dc.html` (+ `RaceDesktop`) | `/races/<race-id>/` | PDW-06 |
| `Standings.dc.html` | `/standings/` | PDW-07 |
| `Entry.dc.html` (+ `EntryDesktop`) | `/entries/<entry-id>/` | PDW-07 |
| `Garage.dc.html` | `/garage/` | PDW-08 |
| `PitWall.dc.html` (+ `PitWallDesktop`) | `/pit-wall/` | PDW-08 |
| `Driver.dc.html` | `/drivers/<driver-id>/` | PDW-08 |
| `Community.dc.html` | `/community/` | PDW-09 |
| `RaceYourself.dc.html` | `/race-it-yourself/` | PDW-09 |
| `Rulebook.dc.html` | `/rulebook/` | PDW-09 |
| `Dictionary.dc.html` | `/dictionary/` | PDW-09 |
| `Contact.dc.html` | `/contact/` | PDW-09 |
| `./data/live.example.json` | `GET /live` on the submission API (`paddock-live/v1`) | PDW-12 |
| `canvas.json` | canvas layout only; not a site input | none |

Desktop files only wrap a page at 1440 px (`<dc-import>`); they show the same
page at desktop width, not a different design.

## Optics: disclaimer, independence and contact (operator, 2026-11-03)

The operator asked for a prominent but low-key disclaimer, a contact page, and an independence statement. These are
part of the design contract:

- **Every page's footer** carries two lines: "AI-run benchmarks: a narrow measurement on a fixed set of races, not a
  comprehensive ranking of any model or harness." and "Independent and open source (MIT). Lacey Enterprises has no
  partnerships with, and no financial interest in, any product on the board." Its links include Contact and about.
- **Home and Standings** carry the "How to read these results" note (rulebook glyph) where entries are ranked.
- **`Contact.dc.html`** (`/contact/`) holds the independence statement, what the results are and are not, and four
  ways in: ask about the program, challenge a result, suggest a race or an entry, and race it yourself. The contact
  address is `hello@agentpaddock.ai`.
- **Contact uses the `pit-board` glyph**, added to the published set; `team-radio` keeps meaning "skills". The
  hamburger menu on every page ends with Contact and about.
- Naming **Lacey Enterprises** in the independence statement is operator-approved. Personal names, emails and
  accounts stay off the site.


## Route map in the site generator (PDW-02)

`scripts/build_site.py` builds every route below through one shared layout in
`scripts/paddock_site/` (`routes.py` is the registry, `layout.py` the head,
header, nav, menu panel and footer, `glyphs.py` the inlined sprite and the
race-type roundel). Each page carries `<meta name="paddock-site" content="pdw-1">`.
Every link is relative, so a directory route is linked as its `index.html`.

The bar shows at 720 px and wider; below that a menu button opens the panel,
which holds the bar's items and the panel-only ones, each with its glyph. A
route whose ticket has not landed shows a short placeholder that points at
today's leaderboard; the ticket replaces it by registering its builder in
`scripts/build_site.py`.

| Route | Output file | Nav | Glyph | Built by today | Ticket |
|---|---|---|---|---|---|
| `/` | `index.html` | lockup | none | today's leaderboard | PDW-04 |
| `/learn/` | `learn/index.html` | bar | `track-map` | placeholder | PDW-05 |
| `/races/` | `races/index.html` | bar | `race-calendar` | placeholder | PDW-06 |
| `/standings/` | `standings/index.html` | bar | `timing-tower` | placeholder | PDW-07 |
| `/garage/` | `garage/index.html` | bar | `setup-sheet` | placeholder | PDW-08 |
| `/pit-wall/` | `pit-wall/index.html` | bar | `pit-wall` | placeholder | PDW-08 |
| `/community/` | `community/index.html` | panel | `entry` | placeholder | PDW-09 |
| `/drivers/` | `drivers/index.html` | panel | `driver` | placeholder | PDW-08 |
| `/entries/` | `entries/index.html` | panel | `podium` | placeholder | PDW-07 |
| `/rulebook/` | `rulebook/index.html` | panel | `rulebook` | placeholder | PDW-09 |
| `/dictionary/` | `dictionary/index.html` | panel | `briefing` | placeholder | PDW-09 |
| `/race-it-yourself/` | `race-it-yourself/index.html` | panel | `pit-lane` | placeholder | PDW-09 |
| `/contact/` | `contact/index.html` | panel | `pit-board` | placeholder | PDW-09 |
| `/matrix.html` | `matrix.html` | footer | none | today's harness × model matrix | kept until a PDW page replaces it |
| `/tasks.html` | `tasks.html` | footer | none | today's task list | kept until a PDW page replaces it |
| `/changelog.html` | `changelog.html` | footer | none | today's grading changelog | kept until a PDW page replaces it |
| `/methodology.html` | `methodology.html` | footer | none | today's methodology | kept until a PDW page replaces it |
| `/board-<suite>-v<n>.html` | one per board | footer (via the leaderboard) | none | today's board pages | kept until a PDW page replaces it |

`/drivers/` and `/entries/` are the menu's landing pages for the per-driver
(`/drivers/<driver-id>/`) and per-entry (`/entries/<entry-id>/`) routes above;
PDW-08 and PDW-07 decide what each lands on. One race (`/races/<race-id>/`) is
built by PDW-06.

Race types draw as roundels: the published `roundel` frame with the race-type
mark inside, as in `Races.dc.html`: coding `coding-worker`, fix `diff`, hard
fix `ci-failed` (red stroke), review `review`, hard review `red-team` (red
stroke), docs `documents`, root cause `search`. Those seven marks come from
this prototype, not the published set, so they appear only inside a roundel.

These design files are reference source: the build never reads them, ships
them or loads their runtime.
