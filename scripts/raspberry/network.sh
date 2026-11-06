#!/usr/bin/env bash
# Art-Net Ethernet planner — dry-run / plan only on this host.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m orng_led.raspberry.network "$@"
