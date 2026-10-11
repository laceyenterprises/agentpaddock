#!/usr/bin/env bash
set -euo pipefail
sandbox_root="${1:-.}"
mkdir -p "$sandbox_root"
cat >"$sandbox_root/surface.py" <<'RFD_INPUT'
def deliver(send):
    for attempt in range(3):
        try:
            send()
            return 0
        except TimeoutError:
            if attempt == 2:
                return 4
# Other exceptions propagate; no sleep after the last attempt.
RFD_INPUT
cat >"$sandbox_root/guide.md" <<'RFD_INPUT'
## Delivery
Delivery retries every exception indefinitely and exits 0 on exhaustion.
RFD_INPUT
cat >"$sandbox_root/change.diff" <<'RFD_INPUT'
--- a/delivery.py
+++ b/delivery.py
@@ -1,5 +1,9 @@
 def deliver(send):
-    while True:
+    for attempt in range(3):
         try:
             send()
             return 0
-        except Exception:
-            continue
+        except TimeoutError:
+            if attempt == 2:
+                return 4
RFD_INPUT
