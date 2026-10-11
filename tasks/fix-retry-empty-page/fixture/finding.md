# Temporary fetch failure is mistaken for an empty page

- **File:** target.py
- **Lines:** 1-17
- **Problem:** read_page converts TemporaryUnavailable to [], so drain appends and advances the cursor instead of preserving a retryable failure.
- **Why it matters:** A retry skips data from the failed page.
