# Disabled account is published before authorization

- **File:** target.py
- **Lines:** 1-5
- **Problem:** publish sends before checking is_enabled; a disabled account still causes a send.
- **Why it matters:** Unauthorized outbound side effects cannot be undone.
