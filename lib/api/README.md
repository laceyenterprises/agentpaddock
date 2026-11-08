# Submission API (Lambda, not deployed)

This directory holds the server side of CONTRIBUTING's
[Signed answers and submission API contract](../../CONTRIBUTING.md#signed-answers-and-submission-api-contract):
a Python 3.12 AWS Lambda behind an Amazon API Gateway HTTP API, with signing
keys and the bundle index in Amazon DynamoDB and bundle files in Amazon S3
(Simple Storage Service). Nothing here is deployed by this repository, and no
CI job talks to AWS. A maintainer deploys it by following the runbook below.

| Path | What it is |
|---|---|
| `src/paddock_api/app.py` | Submission and live operations, their checks and error codes |
| `src/paddock_api/live.py` | Strict public `paddock-live/v1` ticker document validation |
| `src/paddock_api/auth.py` | GitHub-token authentication, resolved per request |
| `src/paddock_api/ed25519.py` | Ed25519 verification (RFC 8032), standard library only |
| `src/paddock_api/canonical.py` | `canonical_json` (identical to `scripts/leaderboard.py`) and a strict JSON reader |
| `src/paddock_api/multipart.py` | A strict `multipart/form-data` reader |
| `src/paddock_api/storage.py` | Storage interface, DynamoDB/S3 implementations, in-memory fakes |
| `src/paddock_api/handler.py` | Lambda entry point and environment wiring |
| `template.yaml` | AWS SAM (Serverless Application Model) / CloudFormation stack |
| `requirements.txt` | Runtime dependencies (only `boto3`, which the Lambda runtime provides) |

Submission tests are in `tests/test_submission_api.py`; live endpoint tests
are in the home page's existing fast file, `tests/test_build_site.py`, using
the same synthetic API fakes from `tests/api_test_support.py` and the `api`
fixture from `tests/conftest.py`. Neither API test group uses network, AWS or
credentials.

```bash
python3 -m pytest -q -p no:cacheprovider tests/test_submission_api.py
python3 -m pytest -q -p no:cacheprovider tests/test_build_site.py
```

## How it behaves

**Authentication.** Submission operations and `PUT /live` require `Authorization: Bearer <GitHub
token>`. The function sends the token once to `GET https://api.github.com/user`
to learn the account login, then drops it. The token is never cached, stored,
logged or echoed back. Any token GitHub accepts for that call works, including
a fine-grained token with no permissions. GitHub logins compare
case-insensitively. A submitter reads only their own bundles. The logins in
the `MaintainerAccounts` deploy parameter may read every bundle.
They may also publish signed live updates using their own registered keys.

**Live ticker.** `GET /live` is public with a 60-second cache, and returns
`404 live_unavailable` until a maintainer publishes a document. `PUT /live`
accepts exactly `{document, key_id, signature}`; the Ed25519 signature covers
the canonical document bytes. The key must belong to the authenticated account,
whose current deploy configuration must grant the maintainer role. Accepted
writes replace the single S3 key `live.json`, separate from private bundles;
reads return only the validated public document. The last accepted write wins.
S3 bucket versioning retains overwritten versions under its existing lifecycle.
The bucket uses SSE-S3 (`AES256`), so reads need no KMS decrypt grant.
Missing-object `AccessDenied` is checked with `ListObjectsV2`, restricted by IAM
to the `live.json` prefix, with at most one result. This confirms absence even
when the prefix condition prevents S3's implicit missing-key check. An existing
object that cannot be read, or a denied list request, remains a service error;
access denial alone never means missing. Normal successful reads use one
`GetObject` and no list call.
See CONTRIBUTING's contract for every document field and the content rules.
Malformed envelopes/documents return `400 malformed_live`, non-maintainers
`403 maintainer_required`, and signature failures `422 invalid_live_signature`;
authentication, ownership and unknown-key errors use the existing codes.
Only successful reads are cached; writes and errors use `no-store`.
Invalid stored JSON or schema returns `503 invalid_stored_live`; the function
logs only the object's SHA-256 digest, and a valid signed update repairs it.
The v1 signature deliberately relies on the strict, disjoint live and submission
schemas for purpose separation, as specified in CONTRIBUTING. Schema changes
that introduce overlap require a new signing context before deployment.
`/live` responses include a fixed Cross-Origin Resource Sharing (CORS) origin
of `https://agentpaddock.ai`, and `OPTIONS /live` handles browser preflight.
The submission routes have no CORS headers.

**Public traffic and cost.** The existing API Gateway default throttling applies
to `/live` too: 5 requests/second with a burst of 10, and Lambda reserved
concurrency is 5. These are overload controls, not a monthly spending cap.
There is no CDN or server-side response cache in this stack; the 60-second
header permits browser caching. The home page bypasses that cache when polling
every 60 seconds while visible, so expect about one request per minute per
visible page (100 pages: about 1.7 requests per second or 144,000 reads/day).
Each uncached successful read invokes Lambda and S3 once; absent documents can
add one S3 list call.
Sustained traffic at the default throttle could approach 432,000 requests/day.
Monitor request volume and throttles, and adjust the existing deploy parameters
to the intended audience. Complete the budget activation in runbook step 3;
budget alerts do not stop traffic or spending.

**Keys.** A key id is `ed25519-` plus the first 32 hex characters of the
SHA-256 of the raw 32-byte public key. Registering an already registered key
returns `409`. The body includes the existing key id only when the same
account registered it. The function refuses keys that are not canonical point
encodings, and small-order keys, as `malformed_key`.

**Bundles.** The `bundle` part is the UTF-8 JSON envelope and each `answers`
part carries one file, with its manifest path as the part's `filename`. The
signature must verify over `canonical_json(manifest)`. The bundle digest is
the SHA-256 of `canonical_json(envelope)`, so whitespace and key order in the
uploaded envelope do not change it. The function stores the canonical envelope
at `bundles/<sha256>/bundle.json` and each answer at
`bundles/<sha256>/answers/<path>`. S3 checks every object against its SHA-256
on write. Objects are written before the index record, so an index record never
names a missing object. Status changes after `pending` (`re-grading`,
`complete`, `rejected`) belong to maintainer tooling outside this API.

Answer paths are relative, with at most 16 `/`-separated segments. Each segment
uses `A-Z a-z 0-9 . _ -` and does not start with `.` or `-`, so absolute paths,
`..`, `.`, backslashes, drive letters and control characters are refused. Two
paths that differ only in case are duplicates. A path that would have to be a
directory of another path is unsafe. The server receives bytes, not a file
system, so it cannot see links. Submitters must not name one.

**Check order.** Each request gets one answer, chosen in this order: route,
size, authentication (`401`), request shape (`400`), key lookup (`404`), key
ownership (`403`), signature, answer digests and pins (`422`), duplicate bundle
(`409`). An unauthenticated request never learns whether a key or bundle exists.

**Pins (`422 incompatible_pin`).** `results_document` must name a top-level
results document (`results/<date>-<id>.json` or `.json.gz`, the only files
results discovery reads). `fork_commit` must be 40 lowercase hex characters,
`verifier.task` must be a task id (`[a-z0-9][a-z0-9._-]*`), and
`verifier.task_version` must be at least 1. Wrong types (for example a string
version) are a malformed manifest (`400`). The API does not fetch forks or
results; a maintainer verifies the pinned content when re-grading.

### Published error codes

Every error body is `{"error": "<code>", "message": "<fixed text>"}`. The
message is fixed for each code and never repeats request content. The two
`409` responses add the fields the contract names.

| Status | Code | Operation | Meaning |
|---|---|---|---|
| 400 | `invalid_json` | keys | Body is not strict JSON (also: duplicate keys) |
| 400 | `unsupported_algorithm` | keys | `algorithm` is not exactly `Ed25519` |
| 400 | `malformed_key` | keys | Not a canonical padded Base64 32-byte Ed25519 key, or extra fields |
| 400 | `malformed_request` | bundles | Not multipart form data, no single `bundle` part, or an `answers` part without a file name |
| 400 | `malformed_manifest` | bundles | Envelope or manifest is not the template's shape: strict JSON, no floats, no duplicate keys, exact fields and types |
| 400 | `duplicate_run_id` | bundles | Two runs share a `run_id` |
| 400 | `duplicate_answer_file` | bundles | Two runs, or two uploads, share an answer path (case-insensitive) |
| 400 | `unsafe_path` | bundles | Answer path is absolute, traverses, or is not portable |
| 400 | `missing_files` | bundles | A manifest path has no uploaded file |
| 400 | `extra_files` | bundles | An upload, or any other form field, is not in the manifest |
| 400 | `malformed_digest` | read | Digest is not 64 lowercase hex characters |
| 401 | `unauthenticated` | all | Missing, malformed or rejected GitHub token |
| 403 | `key_not_owned` | bundles | Key belongs to another account |
| 403 | `bundle_not_owned` | read | Bundle belongs to another account (and caller is no maintainer) |
| 404 | `unknown_key` | bundles | `key_id` is not registered |
| 404 | `unknown_bundle` | read | No bundle has this digest |
| 409 | `key_already_registered` | keys | Same account: adds `key_id`, `github_account`, `algorithm` |
| 409 | `duplicate_bundle` | bundles | Adds the existing `sha256` and `status` |
| 422 | `invalid_signature` | bundles | Signature is not padded Base64 of 64 bytes, or does not verify |
| 422 | `answer_digest_mismatch` | bundles | An answer file does not match its manifest SHA-256 |
| 422 | `incompatible_pin` | bundles | Results document, fork commit or verifier pin is invalid |

Responses from outside the contract table come from transport and service
limits: `404 not_found` and `405 method_not_allowed` for other routes,
`413 payload_too_large` above `MaxBodyBytes` or `MaxFiles`, `429` from API
Gateway throttling, `500 internal_error`, and `503 auth_unavailable` when
GitHub cannot answer (outage or rate limit). A `503` is never reported as `401`.

**Logs.** The function writes one JSON line per request: request id, route,
status, error code and the authenticated login. It never logs headers, bodies,
tokens or answer text. Unexpected exceptions are logged by type name only. API
Gateway access logs record request id, source IP, time, route, status and
response length, and never headers.

## Deploy runbook (PDW-14)

Every step here is an operator action with the operator's own AWS
credentials. The project region is `us-east-2`.

### Parameters

| Parameter | Value |
|---|---|
| `Region` | `us-east-2`. The function refuses to serve from any other region. |
| `MaintainerAccounts` | Comma-separated GitHub logins that may read all bundles and sign live updates; spaces after commas are allowed. Give it at deploy time; never commit it. |
| `BudgetAlertEmail` | Address that receives budget alerts. Give it at deploy time; never commit it. |
| `CostTagValue` | `paddock-api` (default): value of the `paddock-cost-center` tag |
| `EnableCostBudget` | `false` (default) on the first deploy; `true` on the redeploy in step 3, once the tag is active |
| `DomainName` | `api.agentpaddock.ai`, or empty for the execute-api URL only |
| `CertificateArn` | ACM (AWS Certificate Manager) certificate for `DomainName`, issued in `us-east-2` |
| `ReservedConcurrency` | `5` (default): upper bound on concurrent invocations |
| `ThrottleRate` / `ThrottleBurst` | `5` / `10` (defaults): requests per second across the API |
| `MaxBodyBytes` / `MaxFiles` | `4194304` / `256` (defaults) |

The budget is fixed at $10 per month in the template, by operator decision.
It covers only costs tagged `paddock-cost-center=<CostTagValue>`, never the
whole account. It emails at 80% of actual spend and at 100% of forecast spend.
The stack creates it only when `EnableCostBudget=true` (step 3), because the
tag filter cannot match anything until the tag key is active in Billing.

### 1. Certificate (custom domain only)

Request a public ACM certificate for `api.agentpaddock.ai` in `us-east-2`
with DNS validation, and add the validation CNAME record ACM shows at the
domain's DNS host. Wait for the status `Issued` and note its ARN (Amazon
Resource Name).

```bash
aws acm request-certificate --region us-east-2 \
  --domain-name api.agentpaddock.ai --validation-method DNS
```

### 2. Deploy

Use the AWS SAM CLI from the repository root. The function has no third-party
dependencies, so the build only copies `src/`.

```bash
sam build --template-file lib/api/template.yaml --build-dir .aws-sam/build
sam deploy --template-file .aws-sam/build/template.yaml \
  --stack-name paddock-submission-api --region us-east-2 \
  --capabilities CAPABILITY_IAM --resolve-s3 \
  --parameter-overrides \
    Region=us-east-2 \
    MaintainerAccounts="$PADDOCK_MAINTAINERS" \
    BudgetAlertEmail="$PADDOCK_BUDGET_EMAIL" \
    DomainName=api.agentpaddock.ai \
    CertificateArn="$PADDOCK_API_CERT_ARN"
```

This first deploy leaves `EnableCostBudget` at `false`, so no budget exists
yet; step 3 adds it. API Gateway attaches the access-log group's resource
policy itself while deploying, so the deploying credentials need the CloudWatch
Logs delivery permissions it uses (`logs:CreateLogDelivery`,
`logs:GetLogDelivery`, `logs:UpdateLogDelivery`, `logs:ListLogDeliveries`,
`logs:PutResourcePolicy`, `logs:DescribeResourcePolicies`,
`logs:DescribeLogGroups`).

`.aws-sam/` is local build output. Do not commit it. The tables and the
bucket have `DeletionPolicy: Retain`, and the tables have deletion protection,
so deleting the stack keeps submitted bundles.

### 3. Cost-allocation tag

The first deploy creates the `paddock-cost-center` tag. In the Billing console
under **Cost allocation tags**, activate `paddock-cost-center`. It can take up
to 24 hours to appear and to start filtering costs.

Once the tag shows as active, redeploy with the budget enabled:

```bash
sam deploy --template-file .aws-sam/build/template.yaml \
  --stack-name paddock-submission-api --region us-east-2 \
  --capabilities CAPABILITY_IAM --resolve-s3 \
  --parameter-overrides \
    Region=us-east-2 \
    MaintainerAccounts="$PADDOCK_MAINTAINERS" \
    BudgetAlertEmail="$PADDOCK_BUDGET_EMAIL" \
    DomainName=api.agentpaddock.ai \
    CertificateArn="$PADDOCK_API_CERT_ARN" \
    EnableCostBudget=true
```

Keep `EnableCostBudget=true` on every later deploy, or the stack deletes the
budget.

### 4. DNS record

Read the stack output `ApiDomainTarget` and create one record at the domain's
DNS host:

```text
api.agentpaddock.ai.  CNAME  <ApiDomainTarget>
```

```bash
aws cloudformation describe-stacks --region us-east-2 \
  --stack-name paddock-submission-api \
  --query "Stacks[0].Outputs" --output table
```

### 5. Maintainer key registration

Each maintainer generates an Ed25519 key pair on their own machine and
registers only the public key, authenticated with their own GitHub token.
The private key never leaves that machine.

```bash
openssl genpkey -algorithm ed25519 -out paddock-signing.pem
PUB=$(openssl pkey -in paddock-signing.pem -pubout -outform DER | tail -c 32 | base64 | tr -d '\n')
curl -sS -X POST "$API/submission/keys" \
  -H "Authorization: Bearer $(gh auth token)" \
  -H "Content-Type: application/json" \
  -d "{\"algorithm\":\"Ed25519\",\"public_key\":\"$PUB\"}"
```

Expect `201` with the key id and the maintainer's login. Running it again
returns `409` with the same key id.

### 6. Smoke commands

Set `API` to `https://api.agentpaddock.ai`, or to the `ApiEndpoint` output
before DNS exists.

```bash
# 401: no token
curl -sS -o /dev/null -w "%{http_code}\n" "$API/submission/bundles/$(printf '0%.0s' {1..64})"
# 400: malformed digest
curl -sS -H "Authorization: Bearer $(gh auth token)" "$API/submission/bundles/abc"
# 404: unknown bundle
curl -sS -H "Authorization: Bearer $(gh auth token)" \
  "$API/submission/bundles/$(printf '0%.0s' {1..64})"
```

To check a full signed submission, build a synthetic bundle from
`results/templates/answers-bundle.jsonc` with the key from step 5. Write the
filled envelope and answers outside the repository, and never commit them.

```bash
# manifest.json holds the filled manifest; sign its canonical bytes.
python3 -c 'import json,sys; sys.path.insert(0,"scripts"); from leaderboard import canonical_json; sys.stdout.write(canonical_json(json.load(open(sys.argv[1]))))' \
  manifest.json > manifest.canonical
SIG=$(openssl pkeyutl -sign -inkey paddock-signing.pem -rawin -in manifest.canonical | base64 | tr -d '\n')
python3 -c 'import json,sys; print(json.dumps({"manifest": json.load(open(sys.argv[1])), "signature": sys.argv[2]}))' \
  manifest.json "$SIG" > bundle.json
curl -sS -X POST "$API/submission/bundles" \
  -H "Authorization: Bearer $(gh auth token)" \
  -F "bundle=@bundle.json;type=application/json" \
  -F "answers=@run-001.json;filename=answers/run-001.json"
```

Expect `202` with the bundle digest and `"status":"pending"`. Repeating the
upload returns `409` with the same digest. Then `GET
$API/submission/bundles/<digest>` returns `200` for the submitter and for a
maintainer, and `403` for any other account.

### Not in this stack

There is no Lambda@Edge, no multi-region resource and no write path for
bundle statuses after `pending`. Live publication and browser CORS are confined
to `/live`; the function can read only `live.json` from S3, never bundle objects.
