- **A damaged checkpoint halts every later refresh**
  - **File:** `progress_journal.py`
  - **Lines:** 11-18 (the `ProgressJournal` constructor)
  - **Problem:** The constructor decodes every checkpoint under `progress/files/` and the identity index with `json.loads`, and by design lets a decode or shape error escape. The refresh command builds the journal first and without a handler. One truncated or hand-edited file therefore makes construction raise on every pass, and nothing in the refresh path moves the bad file aside.
  - **Why it matters:** A cache of progress becomes a hard precondition for the whole refresh, and the only way out is for an operator to find and delete the bad file by hand. The comment's reasoning does not hold: a source with no checkpoint is already reset and re-read from offset zero, so treating a damaged checkpoint as missing is as safe as first-time ingestion.
  - **Recommended fix:** Per file, handle read and decode failures (`OSError`, `json.JSONDecodeError`, `UnicodeDecodeError`) and shape or version mismatches. Rename the bad file aside with a `.corrupt-<timestamp>` suffix, log the event at ERROR, and treat that source as having no checkpoint. Rebuild or empty a damaged identity index. Add a test that corrupts one checkpoint and the identity index, then checks that the pass completes and both files are quarantined.


Fixture mapping: editable `target.py`. Lines above refer to the review fixture, not to this file.
Port of the checkpoint constructor into load(root). The fixture keeps loading, per-file isolation, quarantine and reset semantics; database ingestion and alert transport are omitted. Logging stands in for the service alert boundary.

Fixture contract: only decode, shape and version errors are corruption and get quarantined.
A read that fails with any other `OSError` (for example permission denied or EIO) must propagate unchanged, with no rename and no quarantine file.
`FileNotFoundError` means no checkpoint. This contract takes precedence over the review card’s Recommended fix for this fixture.
