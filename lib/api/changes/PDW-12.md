# Live ticker API

`GET /live` exposes the latest `paddock-live/v1` document with a 60-second
public cache. `PUT /live` replaces it only after authenticating the owner of
a registered maintainer key and verifying an Ed25519 signature over canonical
document bytes. The single S3 object is `live.json`; only this object gains
read permission. Browser access is scoped to `https://agentpaddock.ai`.
An IAM grant to list only the `live.json` prefix lets reads confirm absence
after S3 denies a missing-object read, while preserving genuine access errors.
Invalid stored documents return uncached `503 invalid_stored_live` and log only
the object's SHA-256 digest. Response headers merge case-insensitively.
Existing API throttling and Lambda concurrency limits cover the public route;
the API README documents expected polling volume and cost limits.
The v1 signing contract retains strict, disjoint schemas as purpose separation.

The exact envelope, schema, error codes and content obligations are documented
in CONTRIBUTING's existing submission API contract and the API README.
