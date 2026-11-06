#!/usr/bin/env bash
# QEMU/Docker linux/arm64 execution proof for a staged release tree.
# Usage: qemu-arm64-release-proof.sh <release_dir> <data_root>
set -euo pipefail
RELEASE="${1:?release dir}"
DATA="${2:?empty data root}"
MEM_LIMIT="${ORNG_QEMU_MEM_LIMIT:-768m}"

docker run --rm --platform linux/arm64 \
  --memory="$MEM_LIMIT" --memory-swap="$MEM_LIMIT" \
  -v "$RELEASE:/release:ro" \
  -v "$DATA:/data" \
  -e ORNG_CONFIG_ROOT=/data \
  -e ORNG_DATA_ROOT=/data \
  -e ORNG_INSTALL_MODE=managed \
  -e ORNG_FRONTEND_DIST=/release/frontend_dist \
  -e ORNG_APP_PLATFORM=raspberry \
  -e PYTHONPATH=/release \
  -w /release \
  debian:bookworm-slim \
  bash -lc '
set -euo pipefail
PY=/release/runtime/bin/python
test -x "$PY" || PY=/release/runtime/bin/python3
"$PY" --version
"$PY" - <<'"'"'PY'"'"'
import platform, sys
assert platform.machine() == "aarch64", platform.machine()
assert sys.version_info >= (3, 12), sys.version
mods = [
  "fastapi","uvicorn","pydantic","pydantic_core","yaml",
  "httpx","websockets","cryptography","evdev","orng_led",
]
for m in mods:
  __import__(m)
  print("import_ok", m)
# native smoke
import pydantic_core, cryptography.hazmat.backends.openssl.backend as b
print("native_ok", pydantic_core.__file__)
PY
export ORNG_CONFIG_ROOT=/data ORNG_DATA_ROOT=/data
START=$(date +%s%3N)
"$PY" -m uvicorn orng_led.main:app --host 127.0.0.1 --port 8000 >/tmp/orng-app.log 2>&1 &
APP_PID=$!
cleanup() { kill -TERM "$APP_PID" 2>/dev/null || true; wait "$APP_PID" 2>/dev/null || true; }
trap cleanup EXIT
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/health >/tmp/health.json; then break; fi
  sleep 0.5
done
END=$(date +%s%3N)
echo "startup_ms=$((END-START))"
cat /tmp/health.json
"$PY" - <<'"'"'PY'"'"'
import json
h=json.load(open("/tmp/health.json"))
assert h.get("ready") is True
assert h.get("blackout") is True
assert h.get("output_armed") is False
assert h.get("platform") == "raspberry"
print("health_ok", h.get("version"), h.get("build_id"), h.get("arch"), h.get("degraded"))
PY
# idle RSS of python child
RSS=$(awk "/^VmRSS:/ {print \$2}" /proc/$APP_PID/status 2>/dev/null || echo 0)
echo "idle_rss_kb=$RSS"
test ! -f /tmp/oom || { echo OOM; exit 1; }
'
