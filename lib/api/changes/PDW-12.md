# Live ticker API

`GET /live` exposes the latest `paddock-live/v1` document with a 60-second
public cache. `PUT /live` replaces it only after authenticating the owner of
a registered maintainer key and verifying an Ed25519 signature over canonical
document bytes. The single S3 object is `live.json`; only this object gains
read permission. Browser access is scoped to `https://agentpaddock.ai`.

The exact envelope, schema, error codes and content obligations are documented
in CONTRIBUTING's existing submission API contract and the API README.
