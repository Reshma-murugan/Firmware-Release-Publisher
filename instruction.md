# Firmware Release Publisher — Task Instruction

## Goal

Implement a reference publisher that reads the raw build manifest, reconciles the manifest in DuckDB, signs publishable release descriptors with the currently trusted OpenSSL CMS key, and submits signed bundles to the provided distribution gateway with deterministic idempotent output.

## Repository layout

All runtime files for the exercise are expected under `/app` in the built container.

- `/app/environment/package.json` defines the `npm run report` entrypoint.
- `/app/environment/fixtures/build_manifest.csv` is the manifest input.
- `/app/environment/reports/publications.expected.txt` is the golden output reference.
- `/app/environment/distribution-gateway/` is the provided Express gateway. Do not modify it.
- `/app/environment/keys/current/current.key.pem` and `/app/environment/keys/current/current.cert.pem` are the currently trusted signing materials.
- `/app/environment/keys/revoked/revoked.key.pem` and `/app/environment/keys/revoked/revoked.cert.pem` are the revoked keypair used to reproduce the trust-rotation failure.

## Required behavior

The reference publisher must:

1. Load `/app/environment/fixtures/build_manifest.csv` into DuckDB.
2. Collapse exact duplicate rows across every column.
3. Apply `WITHDRAWAL` rows by cancelling the `BUILD` whose `entry_id` matches `supersedes_id`.
4. Derive publishable bundles using SQL such that only bundles with at least one surviving build are emitted.
5. For each publishable bundle, in ascending `bundle_id` order, build a canonical descriptor JSON object consisting of:
   - `artifact_count`
   - `bundle_id`
   - `total_bytes`
   and serialize it with deterministic UTF-8 JSON and lexicographically sorted keys.
6. Sign the descriptor bytes using OpenSSL detached CMS signing with the current PEM certificate and key pair.
7. POST `{ descriptor, signature, request_token }` to `http://127.0.0.1:7070/v1/publications`.
8. Record the returned `publication_id`, `request_token`, and signing `key_id` in DuckDB so repeated runs are idempotent.
9. Emit exactly two deterministic lines per bundle:

```
BUNDLE <bundle_id> SIGNED KEY=<key_id>
BUNDLE <bundle_id> PUBLISHED RECEIPT=<publication_id> TOKEN=<request_token> STATUS=PUBLISHED
```

## Success condition

The command below must succeed from the repository root using the environment package:

```
cd /app/environment
npm run report
```

The output must match `/app/environment/reports/publications.expected.txt` after masking the random receipt field, and re-running the command must produce byte-identical output.

## Important constraints

- Do not bypass gateway verification.
- Do not sign with the revoked key.
- Do not modify the gateway implementation.
- The canonical descriptor bytes must be exactly the same bytes sent as `descriptor`.
- The request token must be deterministically `token-<bundle_id>`.
- Persist receipts in `releases.duckdb` so a second run replays the previous publication rather than re-posting it.
- The publisher must remain deterministic across repeated executions.
