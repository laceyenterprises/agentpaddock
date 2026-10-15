- **Retries prolong a rate-limit rejection**
  - **File:** `limiter.py`
  - **Problem:** A rejected request changes later admission decisions. A busy client repeatedly retrying can remain locked out.
  - **Recommended fix:** Record only admitted requests and return the remaining time until the oldest admitted request expires.

- **The response gives clients the wrong delay**
  - **File:** `headers.py`
  - **Problem:** Clients interpret Retry-After as whole seconds, while limiter waits are milliseconds. Some clients sleep for minutes after a short burst.
  - **Recommended fix:** Convert the delay into the least whole-second delay that satisfies the caller's retry contract.
