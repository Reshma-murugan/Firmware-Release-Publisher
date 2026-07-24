#!/bin/bash
set -euo pipefail

cd /app
node solution/publisher/release-publisher.mjs --report
