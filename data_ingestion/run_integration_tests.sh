#!/usr/bin/env bash
set -euo pipefail

# Run from this script's directory so relative Compose paths are stable.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

started_mysql=0

# If mysql is not currently running, start it and remember to restore state.
if [[ -z "$(docker compose ps --status running -q mysql)" ]]; then
  docker compose up -d mysql
  started_mysql=1
fi

set +e
docker compose --profile test run --rm integration_tests
test_exit_code=$?
set -e

# Only stop mysql if we started it in this script.
if [[ $started_mysql -eq 1 ]]; then
  docker compose stop mysql
fi

exit "$test_exit_code"
