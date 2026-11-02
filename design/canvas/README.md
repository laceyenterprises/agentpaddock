# Illustration and results-board mocks

Design canvas sources for Agent Paddock: the illustrations that explain the bench, and mocks of the results boards.
`canvas.json` lays the artboards out on the canvas; each `.dc.html` file is one 1600 × 900 artboard. They are written
for a design canvas tool and need its runtime (`support.js`, the `x-dc` components) to render, so a browser shows
them only as raw markup.

**These are mocks.** Every number on them is a sample value chosen for the design, not a result. The site renders the
real views from the committed results documents under `results/`, and those are the only numbers to quote.

The results-board mocks are interactive: a dropdown picks the ranking, the car, the driver or the fuel measure, and
each board computes its view from the sample rows in its `data()` method.

| File | Board |
|---|---|
| [`Main.dc.html`](Main.dc.html) | The grid |
| [`Entry.dc.html`](Entry.dc.html) | The entry |
| [`PhotoFinish.dc.html`](PhotoFinish.dc.html) | Photo finish |
| [`ParcFerme.dc.html`](ParcFerme.dc.html) | Sealed circuit |
| [`Drill.dc.html`](Drill.dc.html) | Harness drill |
| [`Flags.dc.html`](Flags.dc.html) | Flags |
| [`Fuel.dc.html`](Fuel.dc.html) | Fuel bill |
| [`Shakedown.dc.html`](Shakedown.dc.html) | Shakedown |
| [`Calendar.dc.html`](Calendar.dc.html) | Race calendar |
| [`ChCoding.dc.html`](ChCoding.dc.html) | Coding: time trial |
| [`ChFix.dc.html`](ChFix.dc.html) | Fix: pit stop |
| [`ChHardFix.dc.html`](ChHardFix.dc.html) | Hard fix: the wrong call |
| [`ChReview.dc.html`](ChReview.dc.html) | Review: scrutineering |
| [`ChHardReview.dc.html`](ChHardReview.dc.html) | Hard review: decoys |
| [`ChDocs.dc.html`](ChDocs.dc.html) | Docs: the setup sheet |
| [`ChRca.dc.html`](ChRca.dc.html) | RCA: crash investigation |
| [`Leaderboard.dc.html`](Leaderboard.dc.html) | Results: full standings |
| [`PitWall.dc.html`](PitWall.dc.html) | Results: pit wall |
| [`Driver.dc.html`](Driver.dc.html) | Results: driver performance |
| [`FuelZone.dc.html`](FuelZone.dc.html) | Results: fuel zone |

The colours, type and wording follow the design system in [`../README.md`](../README.md); the glyphs come from
[`../glyphs/`](../glyphs/).
