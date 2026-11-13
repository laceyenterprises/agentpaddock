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

## Accessibility and page weight

Every generated page has a keyboard skip link and visible focus. Learn renders
all sector and flag explanations before JavaScript selects a panel. Reduced
motion disables transitions, the start-light sequence, ticker rotation and lap
playback; the controls still show the completed state.

Default engine map info buttons name their car and driver for screen readers.
On a fine pointer, hovering opens a tip beside its button; moving into the tip
keeps it open for reading or selecting text. Clicking pins a tip until another
click, Escape or an outside click dismisses it. Hovering other info buttons
does not dismiss a pinned tip. Touch devices show the tip near the bottom of
the viewport. The garage refreshes both the explanation and the button's
accessible name when its car or driver changes.

The cold-load budget per page is **768 KiB uncompressed and 128 KiB gzip**,
counting its HTML and each directly loaded first-party stylesheet, script and
image once. Embedded data and the glyph sprite count inside the HTML. Fonts
and analytics come from external services and are outside this reproducible
budget; the social share card is metadata, not a page-load resource.

`tests/test_build_site.py` checks those limits on synthetic and committed results,
local links and fragments, reading without JavaScript, chart names, text contrast
and both footer lines. It also proves that broken links, missing fragments,
overweight resources and hidden main content fail the assertions.

The same test file includes a local Chromium pass at 390, 768 and 1440 px,
with JavaScript both enabled and disabled. Install Playwright separately and set
`PADDOCK_BROWSER_EXECUTABLE` to a local Chromium executable when running it.
It blocks external requests, checks page overflow and control names, and exercises
the skip link, menu and reduced-motion start lights with the keyboard. It skips
when no executable is supplied; the existing fast job needs no browser install.


## Route map in the site generator (PDW-02)

`scripts/build_site.py` builds every route below through one shared layout in
`scripts/paddock_site/` (`routes.py` is the registry, `layout.py` the head,
header, nav, menu panel and footer, `glyphs.py` the inlined sprite and the
race-type roundel). Each page carries `<meta name="paddock-site" content="pdw-1">`.
Every link is relative, so a directory route is linked as its `index.html`.
`layout.page(current=...)` uses the physical output file to calculate those
links. Its optional `nav_current` selects the highlighted route and defaults
to `current`; `document(..., filename=...)` supplies the output filename and
active route separately. Glyph markup is read and parsed once per build
process, then reused across pages.

The bar shows at 720 px and wider; below that a menu button opens the panel,
which holds the bar's items and the panel-only ones, each with its glyph. A
route whose ticket has not landed shows a short placeholder that points at
today's leaderboard; the ticket replaces it by registering its builder in
`scripts/build_site.py`.
A builder takes the loaded results and the parsed site data (`data/*.json`,
keyed by output path, PDW-03), so a page reads its numbers from the same files
the build writes.

| Route | Output file | Nav | Glyph | Built by today | Ticket |
|---|---|---|---|---|---|
| `/` | `index.html` | lockup | none | home (`paddock_site/home.py`) | PDW-04 |
| `/learn/` | `learn/index.html` | bar | `track-map` | the Learn page (`learn.py`, `learn/learn.js`) | PDW-05 |
| `/races/` | `races/index.html` | bar | `race-calendar` | the races grid | PDW-06 |
| `/standings/` | `standings/index.html` | bar | `timing-tower` | the timing tower | PDW-07 |
| `/garage/` | `garage/index.html` | bar | `setup-sheet` | the garage (`garage.py`) | PDW-08 |
| `/pit-wall/` | `pit-wall/index.html` | bar | `pit-wall` | the pit wall (`pit_wall.py`) | PDW-08 |
| `/community/` | `community/index.html` | panel | `entry` | `paddock_site/community.py` | PDW-09 |
| `/drivers/` | `drivers/index.html` | panel | `driver` | the featured drivers, plus `drivers/<driver-id>/index.html` each (`drivers.py`) | PDW-08 |
| `/entries/` | `entries/index.html` | panel | `podium` | every entry's season, listed | PDW-07 |
| `/rulebook/` | `rulebook/index.html` | panel | `rulebook` | `paddock_site/rulebook.py` | PDW-09 |
| `/dictionary/` | `dictionary/index.html` | panel | `briefing` | `paddock_site/dictionary.py` | PDW-09 |
| `/race-it-yourself/` | `race-it-yourself/index.html` | panel | `pit-lane` | `paddock_site/pit_lane.py` | PDW-09 |
| `/contact/` | `contact/index.html` | panel | `pit-board` | `paddock_site/contact.py` | PDW-09 |
| `/leaderboard.html` | `leaderboard.html` | footer | none | today's leaderboard, moved off `/` by PDW-04 | kept until PDW-07's standings replace it |
| `/matrix.html` | `matrix.html` | footer | none | today's harness × model matrix | kept until a PDW page replaces it |
| `/tasks.html` | `tasks.html` | footer | none | today's task list | kept until a PDW page replaces it |
| `/changelog.html` | `changelog.html` | footer | none | today's grading changelog | kept until a PDW page replaces it |
| `/methodology.html` | `methodology.html` | footer | none | today's methodology | kept until a PDW page replaces it |
| `/board-<suite>-v<n>.html` | one per board | footer (via the leaderboard) | none | today's board pages | kept until a PDW page replaces it |

`/drivers/` and `/entries/` are the menu's landing pages for the per-driver
(`/drivers/<driver-id>/`) and per-entry (`/entries/<entry-id>/`) routes above;
PDW-08 and PDW-07 decide what each lands on. One race (`/races/<race-id>/`) is
built by PDW-06, one page per calendar race (see "Races" below).

Race types draw as roundels: the published `roundel` frame with the race-type
mark inside, as in `Races.dc.html`: coding `coding-worker`, fix `diff`, hard
fix `ci-failed` (red stroke), review `review`, hard review `red-team` (red
stroke), docs `documents`, root cause `search`. Those seven marks come from
this prototype, not the published set, so they appear only inside a roundel.

These design files are reference source: the build never reads them, ships
them or loads their runtime.

## The explainer pages (PDW-09)

Community, Race it yourself, Rulebook, Dictionary and Contact share
`scripts/paddock_site/kit.py`: the hero, the filter chips, the trust badge, and
the one stylesheet block and script block they add. Each reads in full without
JavaScript; the script adds the tabs, the filters, the step pager and the copy
buttons.

- **Race it yourself** reads every command at build time from the fenced block
  it restates (`pit_lane.COMMANDS`): the README's quick start, the README's
  runner section (`design/guide/race-it-yourself.md`) and the run-bundle
  section of CONTRIBUTING.md. A moved block fails the build.
- **Rulebook** cites the heading each rule restates; the tests check that every
  cited heading exists.
- **Dictionary** is generated from `design/glyphs/index.json`, one card per
  glyph outside the `engine` group, with the prototype's visitor wording.
- **Community** lists every entry in `data/community.json` below the paddock-run
  level. Until one exists it shows the empty state and EXAMPLE cards only.
- **Contact** uses the `pit-board` glyph, the `hello@agentpaddock.ai` mailto
  (the challenge link pre-fills the subject "Result challenge") and the public
  repository's issues.

## Site data (PDW-03)

Every page reads its numbers from JSON that the build writes under `site/data/`
(`scripts/paddock_site/data.py`). The prototypes' sample data is replaced by
these files, never copied. Nothing under `site/` is committed.

| File | Schema | Shapes it serves |
|---|---|---|
| `data/standings.json` | `paddock-site-standings/v1` | `Standings.dc.html`, home |
| `data/races.json` | `paddock-site-races/v1` | `Races.dc.html`, home |
| `data/races/<race-id>.json` | `paddock-site-race/v1` | `Race.dc.html` |
| `data/entries/<entry-id>.json` | `paddock-site-entry/v1` | `Entry.dc.html` |
| `data/drivers.json` | `paddock-site-drivers/v1` | `Driver.dc.html`, the `Learn.dc.html` ladders |
| `data/community.json` | `paddock-site-community/v1` | `Community.dc.html` |

Each file carries `schema`, `generatedAt` (the newest results document's date),
`state` and `basis`. Standings, race, entry and driver files count only results
documents at `reproduced` or `paddock-run` (CONTRIBUTING.md "Trust levels").
When none qualifies, `state` is `awaiting-trust-sign-off`, the lists are empty,
and the page says so. A race is a local task at its `task.toml` version. The
calendar is the races with at least ten counted runs. Standings place the
entries with a first result on every calendar race and list the rest as
`partial`. Checkpoints appear only as the race's track card discloses them.

## Races (PDW-06)

`scripts/paddock_site/races.py` builds `/races/` and one page per calendar
race at `/races/<race-id>/`, reading only `data/races.json` and
`data/races/<race-id>.json`. `/races/` is a route builder registered in
`scripts/build_site.py` that takes a `routes.Build` like the standings pages.
The race pages are built from the same computed site data, which also carries
each race's briefing prompt.

- **The grid**: one card per calendar race in hardest-first order (avg pass
  rate, lowest first; red under 50%), with challenge chips (counts and
  roundels), an A to Z order, the first 12 cards and then "Show all". A card
  holds the stage bars (the weakest in red) or "Stages not recorded yet",
  entries, median lap and median fuel. Without JavaScript every card shows.
- **A race page**: the header roundel, the avg pass rate with its 95% range,
  the difficulty band; the lap funnel (All runs, By car, By driver for drivers
  with five or more runs recording checks) with its worst checkpoint called
  out and playback at 1.6 s per checkpoint; the classification of each
  entry's first run (lap or fuel order, top 10 then all) and where the rest
  retired; the briefing; the staged files and the sealed circuit.
- **Short names** are plain words with acronyms spelled out, from the
  prototype's renames (`scripts/paddock_site/names.py`).
- **The briefing** follows the track card's `[briefing] show`: `full` shows
  the prompt word for word (its opening, then "Read it all"), `excerpt` its
  first prose paragraph cut at 60 words, `none` nothing. Blank paragraphs
  after headings do not count as prose. A card without
  `[briefing]`, or a task without a card, shows `full`, the documented
  default. The prompt text is never written to a data file.
- **The track and the sealed circuit** come from `leaderboard.py
  track-facts`: staged file and line counts when the race has a card
  `track-facts` accepts, and its conditions (`leaderboard.track_conditions`)
  for every race. No file names are shown: `track-facts` reports counts only.
  If the prompt is missing or unreadable, the site omits its briefing text
  and leaves the track counts and briefing word count unknown; the sealed
  circuit still appears.
- One script, `paddock.js`, carries the menu and these pages' controls; each
  part runs only when its markup is on the page.

## Standings and entry pages (PDW-07)

`scripts/paddock_site/standings.py` builds `/standings/`, `/entries/` and one
`/entries/<entry-id>/` page per entry in `data/standings.json`, placed or
partial. Page builders take a `routes.Build` (the results, the parsed
`data/*.json` and the route registry), so a page reads its numbers from the
data files and never from the prototypes.

- Standings follows `Standings.dc.html`: hero, then the "How to read these
  results" note, the scope panel, the rank switch (avg pass rate or fuel bill),
  the honest-ranges switch with its explainer, and the rows on a 75-100% scale
  whose header shares the rows' grid. A range below 75% is held to the left
  end. Rows are `<details>`, so they open without JavaScript. The board shows
  the top 16 in either ranking, keeping a tie at the cut whole, until "Show
  all" is pressed. Unpriced entries have no fuel rank and do not form a tie.
- An entry page follows `Entry.dc.html`: the placed entries as chips, the hero
  with its honest range on a 50-100% scale, four tiles, and the season grid,
  one cell per calendar race grouped by challenge (checkered, DNF, or a dashed
  cell for a race a partial entry has not run). A tapped flag shows its lap
  and fuel. Chips with the same driver, car and map show their setup hashes.
  A clean sweep means every calendar race was run and passed; partial entries
  describe only the races they ran.
- `/entries/` lists the placed entries in standings order and the partial ones
  behind a disclosure.
- Drivers show as their provider id (`openai/gpt-6.1-sol`): the results carry
  no display names. Cars use the names in `design/vocabulary.md`.
- `standings.js` only switches the view (ranking, ranges, all rows, the tapped
  flag). Without it the board shows the pass ranking with ranges, every row.
- With no trusted results both pages show the awaiting-trust state and no
  entry page is built.

## Home (PDW-04)

`/` rebuilds `Main.dc.html` in `scripts/paddock_site/home.py`; its styles ride
in `paddock.css` and its behaviour in `paddock.js`, which does nothing on any
other page. Its numbers come from `data/standings.json` (the leaders, the
photo finish, the fuel card), `data/races.json` (the hardest race and the
`challenges` list: every counted run on the calendar's races, pooled by
challenge) and the leaders' `data/entries/<entry-id>.json` (the ticker's sample
runs). The board pages that used to live at `/` moved to `/leaderboard.html`.

The live ticker reads the site config's `live_feed_url`
(`scripts/paddock_site/site_config.py`; default `https://api.agentpaddock.ai/live`,
override with `python3 scripts/build_site.py --config FILE`, where `FILE` is a
JSON object). The page fetches it with a four-second timeout, every minute
while visible, and rotates the ticks. A missing, slow or invalid feed shows the
built-in sample runs, labelled "Sample runs": recorded first results of the
leading entries and one example of the red flag. An infrastructure rerun shows
the red flag, never DNF.


## Learn page (PDW-05)

`/learn/` rebuilds `Learn.dc.html` from `data/drivers.json`
(`scripts/paddock_site/learn.py`). The module turns the file into a view: every
state each lab can show, with its words already written, and the rules for
what stays on screen (`show` tables). The page renders the first state of each
lab, embeds the view as JSON and `learn/learn.js` only switches between states.

- **Sector stepper.** Tapping the track drives the next sector, a sector number
  jumps to it, and after sector 4 a tap starts another lap.
- **Engine map.** One chip per driver, on the car with the most levels on the
  dial (then the most runs). The dial always shows low, medium, high, xhigh and
  max; a level the driver has not raced in that car is greyed with the reason,
  and levels off the dial (the car's own default, ultra) are named in the
  caption. The best level is the one whose honest range starts highest. The
  level on screen is held when the driver changes, or falls back to the best
  with a note. Notes call overlapping ranges a statistical tie and name a cost
  ratio from twice as much.
- **Honest ranges.** 9, 54 or 162 runs, starting at the car lab's top result and
  keeping its pass rate when the size changes. Every range is a
  `leaderboard.wilson` value from a table in the view, explained in four steps
  with the formula and live numbers.
- **Same driver, different car.** The car on screen is held across drivers, or
  falls back to the driver's best-placed car with a note. Bars start at 80%
  unless a value sits below 82%, and the caption says where they start.
- **Flags.** The six flags of `design/vocabulary.md`; DNF (did not finish) is
  spelled out at its first use.

Copy carries no numbers of its own: the prototype's sample figures (briefing
length, lap time, the hardest race's rate) are not on the page.

## Data pages (PDW-08)

The garage, the pit wall and the drivers are data pages: `scripts/build_site.py`
lists them in `DATA_PAGES`, and each builder gets the parsed data files
(`paddock_site/views.py`, which also turns ids into display names), the route
and the registry, and returns every file it writes. They read no results
document directly. The registry adapts each data-page builder to its single-page
interface; the site build also retains auxiliary files returned by the bundle.

Driver display names use the same set of trusted and community driver IDs on
all three pages; names shared by several hosts gain a host suffix. Links to
featured driver pages use the same collision-disambiguated slugs everywhere.

- **Garage** (`garage.py`): a level is open for a driver when a counted run of
  that driver at that level exists in any car and any document, trusted or not;
  every other level is greyed out with the reason. A raced combination shows its
  standings result. One raced only in untrusted documents points at the
  community board. An open but unraced combination shows "Nobody has raced this
  yet" and links to `/race-it-yourself/`. The selection lives in the address so
  a build can be shared. It opens on the best standings entry at one of the six
  supported maps, falling back to an open driver level when none qualifies.
- **Pit wall** (`pit_wall.py`): every placed entry by challenge, shaded with the
  `--viz-seq` ramp of `design/tokens.css`. The page script adds the car filter,
  the percent-or-count switch and column sort. Clicking the active column again
  reverses its order; a newly selected column starts best-first (fuel starts
  cheapest-first). Missing values stay last in either direction.
- **Drivers** (`drivers.py`): a driver with at least one placed entry, so a
  complete calendar, gets `/drivers/<driver-id>/`: best entry, car-by-car bars,
  wins and slips by challenge, and cheapest against best on fuel.
  `/drivers/` lists them.
