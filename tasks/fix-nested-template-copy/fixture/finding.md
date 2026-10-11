# Shallow copy leaks tags between requests

- **File:** target.py
- **Lines:** 1-4
- **Problem:** dict.copy shares the nested tags list; appending a request tag mutates the template and earlier results.
- **Why it matters:** Later requests inherit unrelated tags and change previous responses.
