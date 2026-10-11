"""Configuration for the nightly account reconcile job."""

# The per-account presence check, as a plugins.resolve spec ("module:function").
# It is called as check(client, account) and returns the directory entry.
PRESENCE_CHECK = "directory:lookup"

# Accounts per batch; the job commits its report between batches.
BATCH_SIZE = 500
