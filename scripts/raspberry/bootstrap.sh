#!/usr/bin/env bash
# Raspberry bootstrap wrapper — default is check/dry-run.
# Real apply: ./bootstrap.sh --apply [--release /path/to/verified/release]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m orng_led.raspberry.bootstrap "$@"
