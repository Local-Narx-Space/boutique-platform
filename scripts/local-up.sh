#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/init-local.sh
(cd local && docker compose up -d --build)
PORT_MAPPING=$(cd local && docker compose port frontend 8080)
BASE_URL="http://localhost:${PORT_MAPPING##*:}" python3 tests/smoke/smoke.py
