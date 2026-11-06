#!/usr/bin/env bash
# Default dry-run. Explicit --apply required. Never purges data unless --purge-data on device.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m orng_led.raspberry.bootstrap --uninstall "$@"
