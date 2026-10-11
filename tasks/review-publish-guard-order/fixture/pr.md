# Collect delivery receipts consistently

Move receipt collection into the shared publishing path. Disabled accounts must never send. store.send immediately transmits the payload to an external endpoint and returns a receipt; there is no rollback or transaction. store.is_enabled is a read-only check. Callers treat PermissionError as a permanent refusal.
