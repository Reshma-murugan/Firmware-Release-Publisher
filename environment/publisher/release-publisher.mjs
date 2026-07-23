/**
 * release-publisher.mjs
 *
 * Firmware Release Publisher — resolves the UNTRUSTED_SIGNATURE failure caused
 * by the key rotation. This script:
 *
 *   1. Ingests fixtures/build_manifest.csv into DuckDB.
 *   2. Reconciles the manifest with SQL:
 *        - Collapses exact-duplicate rows (same entry_id across every column).
 *        - Applies WITHDRAWAL records to cancel the BUILD they supersede.
 *        - Derives the set of publishable bundles (those with ≥ 1 surviving build).
 *   3. Fetches the current signing-key metadata from the distribution gateway.
 *   4. For each publishable bundle (ordered by bundle_id):
 *        a. Builds a canonical descriptor (UTF-8 JSON, keys sorted, no whitespace).
 *        b. Signs it with OpenSSL CMS using keys/current/current.key.pem + cert.
 *        c. POSTs the signed bundle to POST /v1/publications with a deterministic
 *           idempotency token  token-<bundle_id>.
 *        d. Persists the gateway receipt + token in releases.duckdb.
 *   5. Prints two deterministic status lines per bundle, ordered by bundle_id.
 *
 * Usage (as wired by package.json):
 *   npm run report   →   node publisher/release-publisher.mjs --report
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import 
duckdb from 'duckdb';

// ─── Path resolution ──────────────────────────────────────────────────────────
// The script is run as:  node publisher/release-publisher.mjs  from /app
// process.cwd() == /app  (set by package.json's workdir / the Dockerfile WORKDIR).


const ROOT             = process.cwd();
const CSV_PATH         = path.join(ROOT, 'fixtures', 'build_manifest.csv');
const DB_PATH          = path.join(ROOT, 'releases.duckdb');
const CURRENT_KEY_PATH = path.join(ROOT, 'keys', 'current', 'current.key.pem');
const CURRENT_CERT_PATH= path.join(ROOT, 'keys', 'current', 'current.cert.pem');
const GATEWAY_BASE     = 'http://127.0.0.1:7070';

// ─── Canonical JSON encoding ──────────────────────────────────────────────────
// UTF-8 JSON, object keys sorted lexicographically, no insignificant whitespace.
// Must match the gateway's canonicalEncode exactly — the same bytes are signed
// and verified; any divergence causes UNTRUSTED_SIGNATURE.

function canonicalEncode(value) {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return '[' + value.map(canonicalEncode).join(',') + ']';
  }
  const entries = Object.keys(value)
    .sort()
    .map((k) => JSON.stringify(k) + ':' + canonicalEncode(value[k]));
  return '{' + entries.join(',') + '}';
}

// ─── OpenSSL CMS detached signing ────────────────────────────────────────────
// Signs the descriptor bytes with the CURRENT key/cert and returns PEM string.
// The -binary flag passes data in binary mode; -out writes the PEM signature
// to a temp file so we can read it back as a string.

function signDescriptor(descriptorBytes) {
  const scratch    = fs.mkdtempSync(path.join(os.tmpdir(), 'fw-pub-'));
  const descFile   = path.join(scratch, 'descriptor.bin');
  const sigFile    = path.join(scratch, 'sig.pem');

  try {
    fs.writeFileSync(descFile, descriptorBytes);

    execFileSync('openssl', [
      'cms', '-sign',
      '-in',      descFile,
      '-signer',  CURRENT_CERT_PATH,
      '-inkey',   CURRENT_KEY_PATH,
      '-outform', 'PEM',
      '-out',     sigFile,
      '-binary',
    ], { stdio: ['ignore', 'ignore', 'pipe'] });

    return fs.readFileSync(sigFile, 'utf8');
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
  
}


// ─── DuckDB helpers ──────────────────────────────────────────────────────────

function dbAll(conn, sql, params = []) {
  return new Promise((resolve, reject) => {
    const cb = (err, rows) => (err ? reject(err) : resolve(rows));
    params.length > 0 ? conn.all(sql, ...params, cb) : conn.all(sql, cb);
  });
}

function dbRun(conn, sql, params = []) {
  return new Promise((resolve, reject) => {
    const cb = (err) => (err ? reject(err) : resolve());
    params.length > 0 ? conn.run(sql, ...params, cb) : conn.run(sql, cb);
  });
}

// ─── Gateway HTTP helpers ────────────────────────────────────────────────────

async function gatewayGet(urlPath) {
  const res = await fetch(GATEWAY_BASE + urlPath);
  if (!res.ok) throw new Error(`GET ${urlPath} → HTTP ${res.status}`);
  return res.json();
}

async function gatewayPost(urlPath, body) {
  const res = await fetch(GATEWAY_BASE + urlPath, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  return { httpStatus: res.status, data: await res.json() };
}

// ─── Main pipeline ────────────────────────────────────────────────────────────

async function main() {
  // ── Validate prerequisites ────────────────────────────────────────────────
  if (!fs.existsSync(CSV_PATH)) {
    throw new Error(`Missing manifest CSV at ${CSV_PATH}`);
  }
  if (!fs.existsSync(CURRENT_CERT_PATH) || !fs.existsSync(CURRENT_KEY_PATH)) {
    throw new Error(`Current signing keypair not found under ${ROOT}/keys/current/`);
  }

  // ── Open DuckDB (creates the file if absent) ──────────────────────────────
  const db   = new duckdb.Database(DB_PATH);
  const conn = db.connect();

  

  // ── Ensure publications persistence table exists ──────────────────────────
  await dbRun(conn, `
    CREATE TABLE IF NOT EXISTS publications (
      bundle_id      TEXT PRIMARY KEY,
      request_token  TEXT NOT NULL,
      publication_id TEXT NOT NULL,
      status         TEXT NOT NULL,
      key_id         TEXT NOT NULL
    )
  `);

  // ── Ingest CSV into a temporary table ────────────────────────────────────
  // DROP + CREATE gives a fresh ingest on every run while leaving the
  // publications table (our idempotency store) untouched.
  await dbRun(conn, `DROP TABLE IF EXISTS raw_manifest`);
  await dbRun(conn, `
    CREATE TABLE raw_manifest AS
    SELECT DISTINCT *
    FROM read_csv(
      '${CSV_PATH.replace(/\\/g, '/')}',
      header = true,
      columns = {
        'entry_id':      'VARCHAR',
        'bundle_id':     'VARCHAR',
        'component_id':  'VARCHAR',
        'version':       'VARCHAR',
        'size_bytes':    'BIGINT',
        'record_type':   'VARCHAR',
        'supersedes_id': 'VARCHAR',
        'recorded_at':   'VARCHAR'
      }
    )
  `);

  // ── SQL reconciliation ────────────────────────────────────────────────────
  // 1. DISTINCT already collapsed exact duplicate rows above.
  // 2. Find entry_ids cancelled by WITHDRAWAL rows.
  // 3. Keep surviving BUILDs; aggregate per bundle.
  // 4. Only bundles with ≥ 1 surviving build are publishable.
  const publishable = await dbAll(conn, `
    WITH withdrawn AS (
      SELECT supersedes_id AS entry_id
      FROM   raw_manifest
      WHERE  record_type = 'WITHDRAWAL'
        AND  supersedes_id IS NOT NULL
        AND  supersedes_id <> ''
    ),
    surviving AS (
      SELECT *
      FROM   raw_manifest
      WHERE  record_type = 'BUILD'
        AND  entry_id NOT IN (SELECT entry_id FROM withdrawn)
    )
    SELECT
      bundle_id,
      CAST(COUNT(*)        AS BIGINT) AS artifact_count,
      CAST(SUM(size_bytes) AS BIGINT) AS total_bytes
    FROM   surviving
    GROUP  BY bundle_id
    HAVING COUNT(*) > 0
    ORDER  BY bundle_id ASC
  `);

  if (publishable.length === 0) {
    db.close();
    return;
  }

  // ── Fetch current signing-key metadata ───────────────────────────────────
  const keyMeta = await gatewayGet('/v1/signing-key/current');
  const keyId   = keyMeta.key_id;

  // ── Publish each bundle (deterministic order: ascending bundle_id) ────────
  for (const row of publishable) {
    const bundleId      = row.bundle_id;
    const artifactCount = Number(row.artifact_count);
    const totalBytes    = Number(row.total_bytes);
    const requestToken  = `token-${bundleId}`;

    // Build the canonical descriptor — keys MUST be sorted lexicographically.
    // artifact_count < bundle_id < total_bytes  ✓
    const descriptorObj = {
      artifact_count: artifactCount,
      bundle_id:      bundleId,
      total_bytes:    totalBytes,
    };
    const descriptorStr   = canonicalEncode(descriptorObj);
    const descriptorBytes = Buffer.from(descriptorStr, 'utf8');

    // ── Idempotency: check local DB first ────────────────────────────────
    const existing = await dbAll(
      conn,
      `SELECT publication_id, key_id FROM publications WHERE bundle_id = ?`,
      [bundleId]
    );

    let publicationId;
    let resolvedKeyId = keyId;

    if (existing.length > 0) {
      // Already published and persisted locally — replay stored receipt.
      publicationId = existing[0].publication_id;
      resolvedKeyId = existing[0].key_id || keyId;
    } else {
      // Sign with the CURRENT key (the fix — never use the revoked key).
      const signaturePem = signDescriptor(descriptorBytes);

      // Submit to gateway.
      const { httpStatus, data: receipt } = await gatewayPost(
        '/v1/publications',
        {
          descriptor:    descriptorStr,
          signature:     signaturePem,
          request_token: requestToken,
        }
      );

      if (httpStatus !== 200 || receipt.status !== 'PUBLISHED') {
        const errMsg = receipt.error || JSON.stringify(receipt);
        throw new Error(`Gateway rejected bundle ${bundleId}: ${errMsg}`);
      }

      publicationId = receipt.publication_id;

      // Persist in DuckDB for idempotency on re-runs.
      await dbRun(conn, `
        INSERT INTO publications (bundle_id, request_token, publication_id, status, key_id)
        VALUES (?, ?, ?, ?, ?)
      `, [bundleId, requestToken, publicationId, receipt.status, keyId]);
    }

    // ── Deterministic output (two lines per bundle) ───────────────────────
    process.stdout.write(`BUNDLE ${bundleId} SIGNED KEY=${resolvedKeyId}\n`);
    process.stdout.write(
      `BUNDLE ${bundleId} PUBLISHED RECEIPT=${publicationId} TOKEN=${requestToken} STATUS=PUBLISHED\n`
    );
  }

  db.close();
}

main().catch((err) => {
  process.stderr.write(`release-publisher error: ${err.message || String(err)}\n`);
  process.exit(1);
});
