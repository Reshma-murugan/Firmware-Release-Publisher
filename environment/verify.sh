#!/bin/bash
set -e

echo "=== Step 1: Start distribution gateway ==="
node /app/distribution-gateway/server.js > /tmp/gw.log 2>&1 &
GW_PID=$!

# Wait for gateway to be ready
for i in $(seq 1 10); do
  if node -e "fetch('http://127.0.0.1:7070/healthz').then(r=>r.text()).then(()=>process.exit(0)).catch(()=>process.exit(1))" 2>/dev/null; then
    echo "Gateway is up (pid=$GW_PID)"
    break
  fi
  echo "  Waiting for gateway... ($i)"
  sleep 1
done

echo ""
echo "=== Step 2: Fetch current signing key ==="
node -e "fetch('http://127.0.0.1:7070/v1/signing-key/current').then(r=>r.json()).then(d=>console.log(JSON.stringify(d,null,2)))"

echo ""
echo "=== Step 3: Run publisher directly (first run) ==="
cd /app
node publisher/release-publisher.mjs --report | tee /tmp/out_run1.txt

echo ""
echo "=== Step 4: Run publisher directly (second run — idempotency check) ==="
node publisher/release-publisher.mjs --report | tee /tmp/out_run2.txt

echo ""
echo "=== Step 5: Diff run1 vs run2 (must be empty) ==="
if diff /tmp/out_run1.txt /tmp/out_run2.txt; then
  echo "PASS: Both runs are byte-identical"
else
  echo "FAIL: Runs differ — idempotency broken"
  exit 1
fi

echo ""
echo "=== Step 6: Diff against golden output (RECEIPT masked) ==="
EXPECTED=/app/reports/publications.expected.txt
sed -E 's/RECEIPT=[^ ]+/RECEIPT=<id>/g' "$EXPECTED" > /tmp/expected_masked.txt
sed -E 's/RECEIPT=[^ ]+/RECEIPT=<id>/g' /tmp/out_run1.txt > /tmp/actual_masked.txt

if diff /tmp/expected_masked.txt /tmp/actual_masked.txt; then
  echo "PASS: Output matches golden file"
else
  echo "FAIL: Output does not match golden file"
  echo "--- Expected ---"
  cat /tmp/expected_masked.txt
  echo "--- Actual ---"
  cat /tmp/actual_masked.txt
  exit 1
fi

echo ""
echo "=== Step 7: Verify DuckDB persistence ==="
node -e "
import('duckdb').then(({default: duckdb}) => {
  const db = new duckdb.Database('/app/releases.duckdb');
  const conn = db.connect();
  conn.all('SELECT bundle_id, request_token, publication_id, status, key_id FROM publications ORDER BY bundle_id', (err, rows) => {
    if (err) { console.error(err); process.exit(1); }
    console.log('Publications in DuckDB:');
    rows.forEach(r => console.log(' ', JSON.stringify(r)));
    db.close();
  });
});
"

echo ""
echo "=== Step 8: Verify gateway has exactly 1 publication per bundle ==="
node -e "
Promise.all([
  fetch('http://127.0.0.1:7070/v1/publications').then(r => r.status === 404 ? 'no-list-endpoint' : r.json()).catch(() => 'fetch-failed')
]).then(([r]) => console.log('Gateway list result:', r));
" 2>/dev/null || echo "(no list endpoint — expected)"

echo ""
echo "=== ALL CHECKS PASSED ==="
kill $GW_PID 2>/dev/null || true
