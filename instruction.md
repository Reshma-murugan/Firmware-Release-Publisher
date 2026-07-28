# Firmware Release Publisher — Task Instruction (Binding Specification)

## Goal

Release Engineering rotated the firmware code-signing key and revoked the previous
certificate. The legacy publisher still signs with the revoked key, so every
bundle is rejected with `UNTRUSTED_SIGNATURE` at the distribution gateway.

Rewrite the publisher (`/app/publisher/release-publisher.mjs`) so that it:

1. **Reconciles** `/app/fixtures/build_manifest.csv` in DuckDB (collapse exact
   duplicates → apply `WITHDRAWAL` cancellations → keep only bundles with ≥1
   surviving build).
2. **Signs** each publishable descriptor with the **current** OpenSSL CMS
   keypair under `/app/keys/current/`. Signed bytes must equal the submitted
   `descriptor` bytes exactly.
3. **Submits** each signed descriptor to the Express gateway over HTTP on
   `http://127.0.0.1:7070`. Descriptors signed with `/app/keys/revoked/` are
   rejected as `UNTRUSTED_SIGNATURE` and must not occur.
4. **Records** the gateway receipt (`publication_id`) and request token in
   `/app/releases.duckdb` so a second run replays stored state instead of
   re-submitting (byte-identical stdout, no duplicate gateway rows).
5. **Prints** two deterministic lines per publishable bundle, ascending
   `bundle_id` order, matching the golden file `/app/reports/publications.expected.txt`
   after the random `RECEIPT` field is masked.

Invoked by the grader as: `cd /app && npm run report`  
(Expands via `/app/package.json` to: `node /app/publisher/release-publisher.mjs --report`.)

---

## Repository Layout (Absolute Paths Under `/app`)

| Path | Purpose |
|---|---|
| `/app/package.json` | Root ESM manifest; declares `duckdb` dep and the `npm run report` script above. |
| `/app/fixtures/build_manifest.csv` | Raw input manifest (see schema below). Reconcile this via SQL. |
| `/app/reports/publications.expected.txt` | Golden stdout reference. Match this byte-for-byte after masking `RECEIPT=...`. |
| `/app/distribution-gateway/` | Provided Express service (server + routes + lib + tests + fixtures). **Do NOT modify.** Start it with `node /app/distribution-gateway/server.js`; it listens on port 7070. Interact only over HTTP; never read or write `/app/distribution-gateway/data/gateway.json`. |
| `/app/keys/current/current.key.pem` | Private key of the keypair **currently in force** — sign with this. |
| `/app/keys/current/current.cert.pem` | Matching self-signed certificate (CN=`fw-signing-2026-current`) — the gateway verifies against this. |
| `/app/keys/revoked/revoked.key.pem` | **DO NOT USE.** Old rotated-out private key. |
| `/app/keys/revoked/revoked.cert.pem` | **DO NOT USE.** Matching revoked cert (CN=`fw-signing-2025-revoked`). Signing with this produces `UNTRUSTED_SIGNATURE`. |
| `/app/publisher/` | **Empty at build time.** Write your single deliverable here: `/app/publisher/release-publisher.mjs`. |
| `/app/releases.duckdb` | **Created at runtime by you.** Persistent DuckDB storing publication receipts and request tokens. Must NOT be pre-created. |
| `/app/solution/publisher/release-publisher.mjs` | Reference solution location (grader oracle). NOT the candidate deliverable. |
| `/tests/test.sh` + `/tests/test_outputs.py` | Grader: starts gateway in background, runs pytest, writes binary `0`/`1` to `/logs/verifier/reward.txt`. |

---

## Manifest Schema & Reconciliation (SQL in DuckDB)

`/app/fixtures/build_manifest.csv` has 8 columns, header row present:

```
entry_id, bundle_id, component_id, version, size_bytes, record_type, supersedes_id, recorded_at
```

- `entry_id` (VARCHAR) — unique manifest row id. Exact-duplicate rows share this
  and every other column.
- `record_type` = `BUILD` or `WITHDRAWAL` (upper case).
- `supersedes_id` — for `WITHDRAWAL` rows, the `entry_id` of a `BUILD` to cancel.
  Empty/NULL for `BUILD` rows.

Apply these reconciliation steps in SQL before signing anything:

1. **Collapse exact duplicates.** Rows identical across all 8 columns are the
   same record emitted twice → treat as one. Use `SELECT DISTINCT *`.
2. **Apply withdrawals.** From the de-duplicated set, exclude any `BUILD` whose
   `entry_id` appears as a `supersedes_id` in any `WITHDRAWAL` row.
3. **Derive publishable bundles.** Group surviving `BUILD`s by `bundle_id`.
   A bundle is publishable iff `COUNT(*) >= 1` surviving builds. A bundle
   whose every build was cancelled produces zero output lines.

For each publishable bundle compute:
- `artifact_count` = `COUNT(*)` of surviving builds.
- `total_bytes` = `SUM(size_bytes)` of surviving builds.

Iterate publishable bundles in **ascending `bundle_id` order**. (For the shipped
fixture the publishable set is `{BND-101, BND-102, BND-103}`; `BND-104` is
fully withdrawn and must be absent from output.)

---

## Distribution Gateway HTTP Contract

Base URL: `http://127.0.0.1:7070`. Gateway started automatically by the grader.

### `GET /v1/signing-key/current`

**Response 200:**
```json
{
  "key_id": "fw-signing-2026-current",
  "algorithm": "sha256WithRSAEncryption",
  "certificate_ref": "/app/keys/current/current.cert.pem",
  "status": "current"
}
```
Call this at runtime to obtain the authoritative `key_id` for stdout and DB
storage. Do NOT hardcode `key_id` in source.

### `POST /v1/publications`

**Request body (Content-Type: application/json):**
```json
{
  "descriptor":  "<canonical descriptor string, UTF-8>",
  "signature":   "<detached CMS signature, PEM>",
  "request_token": "token-<bundle_id>"
}
```

**Canonical descriptor format** — the signed bytes and `descriptor` bytes you
send MUST be identical. Use UTF-8 JSON with object keys sorted lexicographically
at every nesting level and no insignificant whitespace. For a publishable bundle
the descriptor has exactly three keys (fixed sorted order
`artifact_count`, `bundle_id`, `total_bytes`):
```
{"artifact_count":<int>,"bundle_id":"<BND-NNN>","total_bytes":<int>}
```

**Signing command** (equivalent shell for Node `execFileSync`):
```
openssl cms -sign -in descriptor.bin \
  -signer /app/keys/current/current.cert.pem \
  -inkey  /app/keys/current/current.key.pem \
  -outform PEM -binary -out sig.pem
```
The `-binary` flag must match on both sign and verify sides.

**Idempotency token:** `request_token` = literal string `token-<bundle_id>` for
the corresponding bundle. A repeated POST with the same token replays the
original receipt without creating a second gateway record.

**Success response 200:**
```json
{
  "publication_id": "pub_<24 hex chars, random>",
  "request_token": "token-BND-XXX",
  "status": "PUBLISHED"
}
```

**Error responses 400:**
- `MISSING_REQUEST_TOKEN` / `MISSING_SIGNATURE` / `MISSING_DESCRIPTOR` — malformed.
- `UNTRUSTED_SIGNATURE` — signature doesn't verify against the current
  certificate (wrong key, canonicalization mismatch, corruption). Nothing
  recorded; your solution must never hit this.

---

## Persistence & Idempotency in `/app/releases.duckdb`

Open/create a DuckDB file at `/app/releases.duckdb` (cwd = `/app`). Create at
minimum:

```sql
CREATE TABLE IF NOT EXISTS publications (
  bundle_id      TEXT PRIMARY KEY,
  request_token  TEXT NOT NULL,
  publication_id TEXT NOT NULL,
  status         TEXT NOT NULL,
  key_id         TEXT NOT NULL
);
```

Idempotency algorithm per bundle:
1. Compute `request_token = 'token-' + bundle_id`.
2. SELECT from `publications` where `bundle_id = ?`.
3. If a row exists → reuse stored `publication_id` and `key_id`. Do NOT sign
   or POST. Print status lines identically.
4. If no row exists → sign + POST, then INSERT the returned receipt.

This ensures the second run reuses stored `publication_id`s, making raw stdout
byte-identical across runs and preventing duplicate gateway rows.

---

## Deterministic Stdout Format

Two lines per publishable bundle, ascending `bundle_id` order, single newline
per line, no trailing blank lines, no stdout diagnostics (send logs to stderr).

Line 1: `BUNDLE <bundle_id> SIGNED KEY=<key_id>`  
Line 2: `BUNDLE <bundle_id> PUBLISHED RECEIPT=<publication_id> TOKEN=<request_token> STATUS=PUBLISHED`

Concrete example (receipt values are illustrative only):
```
BUNDLE BND-101 SIGNED KEY=fw-signing-2026-current
BUNDLE BND-101 PUBLISHED RECEIPT=pub_a1b2c3d4e5f6a1b2c3d4e5f6 TOKEN=token-BND-101 STATUS=PUBLISHED
BUNDLE BND-102 SIGNED KEY=fw-signing-2026-current
BUNDLE BND-102 PUBLISHED RECEIPT=pub_b2c3d4e5f6a1b2c3d4e5f6a1 TOKEN=token-BND-102 STATUS=PUBLISHED
BUNDLE BND-103 SIGNED KEY=fw-signing-2026-current
BUNDLE BND-103 PUBLISHED RECEIPT=pub_c3d4e5f6a1b2c3d4e5f6a1b2 TOKEN=token-BND-103 STATUS=PUBLISHED
```

The grader masks `RECEIPT=\S+` before diffing against
`/app/reports/publications.expected.txt`. For the raw idempotency check the
stored `publication_id`s keep the two runs byte-identical.

---

## Success Conditions (Grading Proofs)

Both proofs run in a freshly-built container and use the **same** verifier
command: `cd /tests && bash test.sh` (which runs `pytest` on `test_outputs.py`
and writes `0` or `1` to `/logs/verifier/reward.txt`). The pytest suite invokes
`npm run report` — i.e. `node publisher/release-publisher.mjs --report` from
`/app` — so it always exercises whichever code lives at
`/app/publisher/release-publisher.mjs`. The two proofs differ only in whether
that file exists.

### Proof A — Empty Environment Proof (Baseline Reward 0)

Premise: the environment ships with no candidate code. This sanity-checks that
the scaffolding correctly fails when no solution is supplied.

```
cd /tests && bash test.sh
```
`/app/publisher/` is empty (only `.gitkeep`), so `npm run report` fails with
`Error: Cannot find module`. Every pytest test fails, `test.sh` writes
`reward.txt = 0`.

**Expected: reward 0.** If this proof doesn't fail cleanly, the grader cannot
trust that a subsequent reward 1 reflects candidate work rather than leaked
scaffolding.

### Proof B — Solution Proof (Graded Reward 1)

The grader copies the reference solution into the candidate entry point, then
runs the **same** verifier:

```
cp /app/solution/publisher/release-publisher.mjs /app/publisher/release-publisher.mjs
cd /tests && bash test.sh
```

Now `npm run report` succeeds. All pytest tests pass, `test.sh` writes
`reward.txt = 1`.

All of the following must hold:
1. **Golden match (masked).** After `s/RECEIPT=\S+/RECEIPT=<mask>/g`, stdout
   byte-equals `/app/reports/publications.expected.txt` (line order, spacing,
   trailing newline).
2. **Deterministic across two runs.** Run twice consecutively without state
   reset → raw stdout byte-identical. Gateway ledger holds exactly one
   publication per bundle.
3. **Current key only.** Every `BUNDLE … SIGNED KEY=` line references the
   current key id; none references `fw-signing-2025-revoked`.
4. **Correct publishable bundles.** `{BND-101, BND-102, BND-103}` present in
   output; fully-withdrawn `BND-104` absent.
5. **Receipts persisted in DuckDB.** `/app/releases.duckdb` exists and
   contains one row per publishable bundle with: correct `bundle_id`,
   `request_token = token-BND-XXX`, `publication_id` present,
   `status = PUBLISHED`, `key_id = fw-signing-2026-current`.

---

## Hard Constraints (Failing Boundaries)

1. Interact with the gateway **only over HTTP**. Do not read or mutate
   `/app/distribution-gateway/data/gateway.json`.
2. **Never bypass signature verification.** No empty signatures, no gateway
   mutation, no env-var bypasses. The grader independently drives the
   current-key accept and revoked-key reject paths.
3. **Sign with `/app/keys/current/` only.** One `UNTRUSTED_SIGNATURE` on any
   submission during grading = fail.
4. **Do NOT hardcode golden output.** Derive publishable bundles, counts, and
   byte sums from the CSV at runtime via SQL — the solution must stay correct
   if the fixture rows changed.
5. **Deterministic ordering.** Always iterate and emit in ascending
   `bundle_id` order; never emit in arbitrary SQL order or hash-table order.
6. **Deterministic tokens.** Always `token-<bundle_id>`; never random UUIDs or
   timestamps.
7. **Keep artifacts in the working directory.** Write `releases.duckdb` to
   `/app/releases.duckdb`, not `/tmp` or elsewhere.

See `CANDIDATE_GUIDE.md` for a recommended development walkthrough.
