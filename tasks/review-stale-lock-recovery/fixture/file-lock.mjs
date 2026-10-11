import { openSync, mkdirSync, statSync, rmSync } from "node:fs";
import { dirname } from "node:path";
function sleepSync(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }

const LOCK_STALE_MS = 15_000;
const LOCK_RETRY_MS = 5;
const LOCK_TIMEOUT_MS = 5_000;

// sleepSync blocks the thread on purpose: every caller of this module is
// synchronous, so the wait between retries must be synchronous too.
function acquireFileLock(path, { now = () => Date.now() } = {}) {
  const lockPath = `${path}.lock`;
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const deadline = now() + LOCK_TIMEOUT_MS;
  for (;;) {
    try {
      return { fd: openSync(lockPath, "wx", 0o600), lockPath };
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      // Reclaim a lock left by a holder that crashed while holding it; otherwise
      // one crash would block every later caller on this host.
      let ageMs = Number.POSITIVE_INFINITY;
      try {
        ageMs = now() - statSync(lockPath).mtimeMs;
      } catch (statError) {
        if (statError?.code === "ENOENT") continue; // already gone; try again
      }
      if (ageMs > LOCK_STALE_MS) {
        rmSync(lockPath, { force: true });
        continue;
      }
      if (now() > deadline) {
        throw new Error(
          `timed out after ${LOCK_TIMEOUT_MS}ms acquiring file lock ${lockPath}`
        );
      }
      sleepSync(LOCK_RETRY_MS);
    }
  }
}
