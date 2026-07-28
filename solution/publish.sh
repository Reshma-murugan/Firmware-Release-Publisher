#!/bin/bash
set -euo pipefail

# Proof B entry point: run the reference solution to produce the graded
# report output. The grader invokes this script (or the equivalent node
# command below) to verify that reward 1 can be produced in a clean container.
# This is NOT the empty-environment proof (Proof A); Proof A is achieved by
# keeping /app/publisher/ empty so that `cd /app && npm run report`
# predictably fails with "Cannot find module".

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="/app"

cd "$APP_ROOT"

exec node solution/publisher/release-publisher.mjs --report
