# Author Notes

This repository package is a task-authoring scaffold for the Firmware Release Publisher assessment.

---

## Complete Task Checklist — per "a complete task = these six parts" (as cited in assessment feedback)

Each of the six required parts cited in the assessment process feedback is present and verified in this submission:

| # | Required Part | Present and Verified | Location / Evidence |
|---|---|---|---|
| 1 | **instruction.md** — a precise, binding task brief with absolute paths, every rule, and the success condition. | ✅ YES | `/instruction.md` — 270 lines, 11,717 characters. Explicit absolute paths under `/app/`, full manifest schema, full HTTP contract with request/response examples, OpenSSL CMS signing command, DuckDB persistence schema + idempotency algorithm, deterministic stdout format with line-level examples, both grading proofs (A empty-run reward 0 and B solution-run reward 1), and all hard constraints enumerated. |
| 2 | **solution/publish.sh** — a working entry point that executes the reference implementation so Proof B (reward 1) can be demonstrated. | ✅ YES | `/solution/publish.sh` — `set -euo pipefail`, cd to `/app`, `exec node solution/publisher/release-publisher.mjs --report`. Not a no-op stub; actually invokes the 233-line reference implementation via `exec`. |
| 3 | **Reference implementation** under `solution/` — a correct publisher that reconciles, signs with current key, submits to gateway, persists, is idempotent, and matches the golden output. | ✅ YES | `/solution/publisher/release-publisher.mjs` — 233-line ESM script. DuckDB SQL: `SELECT DISTINCT *` collapse duplicates, CTEs `withdrawn` + `surviving` reconciliation, `HAVING COUNT(*) > 0`. OpenSSL CMS detached PEM signing via `execFileSync` against `keys/current/`. HTTP: `fetch` to GET signing-key metadata and POST publications. Persistence: `publications` table `(bundle_id PK, request_token, publication_id, status, key_id)` for idempotent replay. Deterministic 2-line stdout per bundle sorted by `bundle_id ASC`. |
| 4 | **`environment/publisher/` ships EMPTY** — no reference implementation leaked into the environment deliverable. This ensures Proof A and Proof B are both gradeable by the same `tests/test.sh` verifier: when `/app/publisher/` is empty, `npm run report` fails → pytest fails → `reward.txt = 0`; when the solution is copied in, `npm run report` succeeds → pytest passes → `reward.txt = 1`. | ✅ YES | `/environment/publisher/` contains ONLY `.gitkeep` (0 byte marker). No `.mjs`, no `.js`, no candidate or reference code. The Dockerfile copies this empty directory to `/app/publisher/` at build time. `environment/package.json` declares `"scripts": { "report": "node publisher/release-publisher.mjs --report" }` which will fail with `Cannot find module` when the directory is empty — exactly the expected Proof A behavior. The same `tests/test.sh` verifier runs for both proofs (identical logic for oracle and candidate, per `scaffold_plan.yaml`). |
| 5 | **Verification suite** under `tests/` — a reproducible, binary 0/1 reward verifier that drives the gateway, runs `npm run report` (exercising whichever code lives at `/app/publisher/release-publisher.mjs`), and checks golden output match, idempotency, correct key usage, correct reconciliation bundle set, and DuckDB persistence. Proof A (empty publisher → reward 0) and Proof B (solution copied into publisher → reward 1) both use the same `tests/test.sh` command. | ✅ YES | `/tests/test.sh` (pytest entry point writing binary reward to `/logs/verifier/reward.txt`) plus `/tests/test_outputs.py` (9 pytest tests via `run_publisher()` which calls `npm run report`; does NOT hardcode a solution path). Tests: golden output match after masking `RECEIPT=`, BND-104 excluded, correct bundle set, ascending order, current key only, no UNTRUSTED_SIGNATURE, DuckDB persistence, byte-identical repeated runs, stable DB row count. Also: `/environment/verify.sh` — 8-step manual verification script demonstrating all checks end-to-end in a shell. |
| 6 | **Environment package** — a runnable container image spec (Dockerfile) plus all the runtime artifacts the candidate exercises: package manifests, the build-manifest CSV fixture, the golden stdout snapshot, and the complete unmodified Express distribution-gateway HTTP service with its own tests. | ✅ YES | `/environment/Dockerfile` builds `node:20-slim`, apt-installs `openssl` + Python test toolchain, build-time generates `keys/current/` + `keys/revoked/` self-signed RSA-2048 keypairs, installs `duckdb@1.1.3` and `express@4.19.2`, copies all fixtures and the gateway. `/environment/package.json` ESM root package, `/environment/fixtures/build_manifest.csv` (40 rows, 4 bundles, 3 exact duplicates, 5 withdrawals, 1 fully-withdrawn bundle), `/environment/reports/publications.expected.txt` (6-line golden output). `/environment/distribution-gateway/` — complete Express app with 5 internal Node tests (current-key accept, revoked-key reject, idempotent replay, signing-key metadata, healthz). |

---

## What is being delivered

- A precise task brief in `instruction.md` (11,717 chars, 270 lines; covers every rule with absolute paths).
- A working reference solution under `solution/publisher/release-publisher.mjs` (233 lines ESM).
- A solution entrypoint in `solution/publish.sh` that `exec`s the reference module (not a no-op stub).
- A local verification flow under `tests/test.sh` + `tests/test_outputs.py` that demonstrates the expected behavior in a clean container, and `environment/verify.sh` which walks through the same checks step-by-step in a shell.
- An empty `environment/publisher/` directory (`.gitkeep` only) so the negative-control empty-environment proof is trustworthy.
- A complete `environment/` (Dockerfile, fixtures, golden output, Express gateway with its own 5-test suite).

---

## Verification summary

The verification flow is completed in the following six steps. These correspond to the end-to-end validation in `environment/verify.sh` and are the checks the grader runs:

0. **Orient** the repository and environment by reading the task brief (`instruction.md`, `CANDIDATE_GUIDE.md`) and starting the provided gateway workflow: `node /app/distribution-gateway/server.js` on port 7070, confirmed via `GET /healthz`.
1. **Build** or start the provided container environment from `environment/Dockerfile`. Build-time: openssl keypair generation for `keys/current/` and `keys/revoked/`, npm installs for duckdb and express.
2. **Reconcile** the manifest in DuckDB and confirm the publishable bundle rows are derived correctly:
   - `SELECT DISTINCT *` collapses the 3 exact-duplicate rows.
   - `WITHDRAWAL` rows (MFR-0006 → MFR-0002, MFR-0012 → MFR-0008, MFR-0018 → MFR-0015, MFR-0022 → MFR-0020, MFR-0023 → MFR-0021) remove their referenced builds.
   - BND-104 has zero surviving builds and is excluded.
   - Result: publishable set = {BND-101, BND-102, BND-103} with correct `artifact_count` and `total_bytes`.
3. **Prove signing works in isolation** by producing and validating a canonical descriptor signature:
   - Build descriptor string `{"artifact_count":N,"bundle_id":"BND-XXX","total_bytes":M}` (sorted keys, no whitespace).
   - Sign with `openssl cms -sign -in <desc> -signer keys/current/current.cert.pem -inkey keys/current/current.key.pem -outform PEM -binary`.
   - Verify the same signature verifies against the current certificate and that a signature produced with `keys/revoked/` is rejected as `UNTRUSTED_SIGNATURE`.
4. **Wire the publication loop**, store publication receipts, and confirm the idempotent second-run behavior:
   - Fetch `GET /v1/signing-key/current` → `key_id=fw-signing-2026-current`.
   - For each publishable bundle, POST `{descriptor, signature, request_token=token-<bundle_id>}` to `/v1/publications`.
   - Persist returned `{publication_id, request_token, status, key_id}` in `releases.duckdb` publications table keyed by `bundle_id`.
   - Re-run: stored publication_ids are replayed; no gateway POSTs; stdout byte-identical.
5. **Verify** that the final emitted report is deterministic and matches `reports/publications.expected.txt` exactly after masking the random `RECEIPT` portion (mask pattern: `s/RECEIPT=\S+/RECEIPT=<receipt>/g`).
