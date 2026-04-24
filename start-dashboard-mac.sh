#!/usr/bin/env bash
set -euo pipefail

# Run this on the Mac frontend machine only.
# Tailscale:
#   Mac Ink dashboard:  100.104.250.54
#   Windows backend/API: 100.91.53.63

cd "$(dirname "$0")/dashboard"
export KELLY_API_BASE_URL="${KELLY_API_BASE_URL:-http://100.91.53.63:8765}"
exec npm start
