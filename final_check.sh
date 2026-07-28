#!/bin/bash
set -e

echo "=== Proof A: empty publisher (expect reward 0) ==="
echo "publisher/ contents:" && ls /app/publisher/
cd /app/distribution-gateway && node server.js >/dev/null 2>&1 & GW_PID=$!
sleep 1
cd /tests && bash test.sh 2>&1 | tail -5
echo "Proof A reward=$(cat /logs/verifier/reward.txt)"
kill $GW_PID 2>/dev/null || true

echo ""
echo "=== Proof B: copy solution into publisher (expect reward 1) ==="
cp /app/solution/publisher/release-publisher.mjs /app/publisher/release-publisher.mjs
echo "publisher/ contents:" && ls /app/publisher/
cd /app/distribution-gateway && node server.js >/dev/null 2>&1 & GW_PID=$!
sleep 1
cd /tests && bash test.sh 2>&1 | tail -5
echo "Proof B reward=$(cat /logs/verifier/reward.txt)"
kill $GW_PID 2>/dev/null || true

echo ""
echo "=== Cleaning up: remove solution from publisher ==="
rm -f /app/publisher/release-publisher.mjs
ls /app/publisher/
