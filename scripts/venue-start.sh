#!/usr/bin/env bash
# One-click venue boot for macOS: wait for Art-Net NIC, start server,
# Blackout + activate Art-Net + Arm, then open Chrome.
# After success: only turn Blackout OFF in the UI.
set -euo pipefail

HOST_ADDRESS="${HOST_ADDRESS:-127.0.0.1}"
PORT="${PORT:-8000}"
LAPTOP_IP="${LAPTOP_IP:-2.0.0.10}"
CONTROLLER_IP="${CONTROLLER_IP:-2.0.0.11}"
NETMASK="${NETMASK:-255.0.0.0}"
NETWORK_TIMEOUT_SEC="${NETWORK_TIMEOUT_SEC:-90}"
HEALTH_TIMEOUT_SEC="${HEALTH_TIMEOUT_SEC:-120}"
SKIP_BUILD="${SKIP_BUILD:-1}"
NO_CHROME="${NO_CHROME:-0}"
CONFIGURE_IP="${CONFIGURE_IP:-1}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_URL="http://${HOST_ADDRESS}:${PORT}"
PYTHON="$REPO_ROOT/.venv/bin/python"
RUN_SCRIPT="$REPO_ROOT/scripts/run.sh"
LOG_DIR="$REPO_ROOT/.venue-logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/venue-start-$STAMP.log"
SERVER_LOG="$LOG_DIR/server-$STAMP.log"

mkdir -p "$LOG_DIR"

step() {
  local msg="$1"
  local line
  line="[$(date +%H:%M:%S)] $msg"
  echo "$line"
  echo "$line" >>"$LOG_FILE"
}

fail() {
  step "ERROR: $1"
  echo ""
  echo "Boot failed. Log: $LOG_FILE"
  echo "Check USB-C Ethernet / IP $LAPTOP_IP / controller $CONTROLLER_IP."
  echo ""
  if [[ -z "${ORNG_VENUE_NO_PAUSE:-}" ]]; then
    read -r -p "Press Enter to close "
  fi
  exit 1
}

json_post() {
  local path="$1"
  local body="$2"
  curl -fsS -X POST "$BASE_URL$path" \
    -H "Content-Type: application/json" \
    -d "$body"
}

api_ready() {
  curl -fsS "$BASE_URL/api/health" 2>/dev/null | grep -q '"ready"[[:space:]]*:[[:space:]]*true'
}

find_artnet_iface() {
  # Prefer any iface that already has a 2.x address.
  local iface ip
  while read -r iface ip; do
    [[ -n "$iface" && -n "$ip" ]] || continue
    if [[ "$ip" == 2.* ]]; then
      echo "$iface"
      return 0
    fi
  done < <(ifconfig -l 2>/dev/null | tr ' ' '\n' | while read -r ifc; do
    ip="$(ifconfig "$ifc" 2>/dev/null | awk '/inet /{print $2; exit}')"
    [[ -n "$ip" ]] && echo "$ifc $ip"
  done)

  # Fallback: hardware port whose name looks like USB Ethernet.
  if command -v networksetup >/dev/null 2>&1; then
    local port device
    while IFS= read -r line; do
      if [[ "$line" == "Hardware Port:"* ]]; then
        port="${line#Hardware Port: }"
      elif [[ "$line" == "Device:"* ]]; then
        device="${line#Device: }"
        if [[ "$port" == *[Uu][Ss][Bb]* || "$port" == *[Ee]thernet* ]]; then
          if ifconfig "$device" >/dev/null 2>&1; then
            # Prefer USB-looking ports.
            if [[ "$port" == *[Uu][Ss][Bb]* ]]; then
              echo "$device"
              return 0
            fi
          fi
        fi
      fi
    done < <(networksetup -listallhardwareports 2>/dev/null)
  fi
  return 1
}

service_for_device() {
  local device="$1"
  networksetup -listallhardwareports 2>/dev/null | awk -v dev="$device" '
    /^Hardware Port:/ { port=$0; sub(/^Hardware Port: /, "", port) }
    /^Device:/ {
      d=$0; sub(/^Device: /, "", d)
      if (d == dev) { print port; exit }
    }
  '
}

ensure_network() {
  step "Waiting for USB-C / Art-Net adapter (up to ${NETWORK_TIMEOUT_SEC}s) ..."
  local deadline=$((SECONDS + NETWORK_TIMEOUT_SEC))
  local iface=""
  while (( SECONDS < deadline )); do
    if iface="$(find_artnet_iface)"; then
      break
    fi
    sleep 2
  done
  [[ -n "$iface" ]] || fail "Art-Net NIC not found. Plug USB-C Ethernet, wait for link, retry."

  local current_ip
  current_ip="$(ifconfig "$iface" 2>/dev/null | awk '/inet /{print $2; exit}')"
  step "NIC: $iface (ip=${current_ip:-none})"

  if [[ "$current_ip" != "$LAPTOP_IP" && "$CONFIGURE_IP" == "1" ]]; then
    local service
    service="$(service_for_device "$iface" || true)"
    if [[ -n "$service" ]] && command -v networksetup >/dev/null 2>&1; then
      step "Configuring $LAPTOP_IP on '$service' ($iface) ..."
      if networksetup -setmanual "$service" "$LAPTOP_IP" "$NETMASK" 2>>"$LOG_FILE"; then
        step "Laptop IP set to $LAPTOP_IP"
      else
        step "Could not set IP via networksetup (try: sudo networksetup -setmanual \"$service\" $LAPTOP_IP $NETMASK)"
        if [[ "$current_ip" != 2.* ]]; then
          fail "No 2.x address on $iface. Configure $LAPTOP_IP/$NETMASK manually, then retry."
        fi
      fi
    elif [[ "$current_ip" != 2.* ]]; then
      fail "No 2.x address on $iface. Set $LAPTOP_IP manually (System Settings → Network), then retry."
    else
      step "Using existing 2.x address $current_ip"
    fi
  else
    step "Laptop already has $LAPTOP_IP"
  fi

  step "Controller target: $CONTROLLER_IP (Art-Net UDP 6454)"
  if ping -c 1 -W 1000 "$CONTROLLER_IP" >/dev/null 2>&1; then
    step "Controller $CONTROLLER_IP answers ping"
  else
    step "No ICMP from $CONTROLLER_IP (often normal for Art-Net nodes) - continuing"
  fi
}

stop_port() {
  local pids
  pids="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    step "Stopping old process on port $PORT"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

start_server() {
  if api_ready; then
    step "Server already ready at $BASE_URL"
    return 0
  fi
  [[ -x "$PYTHON" ]] || fail "Virtualenv missing. Run ./scripts/bootstrap.sh first."
  [[ -f "$RUN_SCRIPT" ]] || fail "Missing $RUN_SCRIPT"

  stop_port
  step "Starting ORNG server ..."
  (
    cd "$REPO_ROOT"
    SKIP_BUILD="$SKIP_BUILD" HOST_ADDRESS="$HOST_ADDRESS" PORT="$PORT" \
      nohup bash "$RUN_SCRIPT" >"$SERVER_LOG" 2>&1 &
    echo $! >"$LOG_DIR/server.pid"
  )

  local deadline=$((SECONDS + HEALTH_TIMEOUT_SEC))
  while (( SECONDS < deadline )); do
    if api_ready; then
      step "API ready"
      return 0
    fi
    sleep 2
  done
  fail "Server did not become ready within ${HEALTH_TIMEOUT_SEC}s. See $SERVER_LOG"
}

state_json() {
  curl -fsS "$BASE_URL/api/state"
}

jq_bool() {
  # Tiny JSON bool extractor without requiring jq.
  # usage: echo "$json" | jq_bool '.output.armed'  -- simplified path via python
  local expr="$1"
  "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); print('true' if ($expr) else 'false')"
}

initialize_show_safe() {
  local state transport armed blackout udp net
  state="$(state_json)"
  transport="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["transport"])' <<<"$state")"
  armed="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["armed"])' <<<"$state")"
  blackout="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["engine"]["blackout"])' <<<"$state")"
  udp="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["udp_active"])' <<<"$state")"
  net="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["network_allowed"])' <<<"$state")"

  if [[ "$blackout" == "True" && "$armed" == "True" && "$transport" == "artnet" && ( "$udp" == "True" || "$net" == "True" ) ]]; then
    step "Already READY (Art-Net + Armed + Blackout) - skipping re-activate"
    return 0
  fi

  step "Face OFF + Blackout ON ..."
  json_post "/api/commands/face" "{\"enabled\":false,\"client_command_id\":\"venue-face-off-$STAMP\"}" >/dev/null || true
  json_post "/api/commands/blackout" "{\"enabled\":true,\"client_command_id\":\"venue-blackout-$STAMP\"}" >/dev/null

  state="$(state_json)"
  armed="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["armed"])' <<<"$state")"
  if [[ "$armed" == "True" ]]; then
    step "Disarming before Art-Net (re)activate ..."
    json_post "/api/output/disarm" "{\"client_command_id\":\"venue-disarm-$STAMP\"}" >/dev/null
    json_post "/api/commands/blackout" "{\"enabled\":true,\"client_command_id\":\"venue-blackout2-$STAMP\"}" >/dev/null
  fi

  state="$(state_json)"
  transport="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["transport"])' <<<"$state")"
  udp="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["udp_active"])' <<<"$state")"
  net="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["network_allowed"])' <<<"$state")"
  if [[ "$transport" != "artnet" || ( "$udp" != "True" && "$net" != "True" ) ]]; then
    step "Activating Art-Net ..."
    if ! json_post "/api/output/activate-artnet" "{\"confirmed\":true,\"client_command_id\":\"venue-activate-$STAMP\"}" >/dev/null; then
      fail "Art-Net activate failed (HTTP error). Check Setup / activation blockers."
    fi
  else
    step "Art-Net already active"
  fi

  state="$(state_json)"
  armed="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["armed"])' <<<"$state")"
  if [[ "$armed" != "True" ]]; then
    step "Arming output (Blackout stays ON) ..."
    if ! json_post "/api/output/arm" "{\"confirmed\":true,\"client_command_id\":\"venue-arm-$STAMP\"}" >/dev/null; then
      fail "Arm failed. Check /api/output/arm-blockers"
    fi
  else
    step "Already armed"
  fi

  state="$(state_json)"
  armed="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["armed"])' <<<"$state")"
  blackout="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["engine"]["blackout"])' <<<"$state")"
  udp="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["udp_active"])' <<<"$state")"
  net="$("$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["output"]["network_allowed"])' <<<"$state")"

  [[ "$blackout" == "True" ]] || fail "Blackout is OFF after boot - aborting."
  [[ "$armed" == "True" ]] || fail "Arm failed - output not armed."
  if [[ "$udp" != "True" && "$net" != "True" ]]; then
    step "Warning: UDP/network flag not clearly true - check Setup"
  fi
  step "READY: Art-Net up, Armed=true, Blackout=ON, target=$CONTROLLER_IP"
}

open_admin_ui() {
  [[ "$NO_CHROME" == "1" ]] && return 0
  local url="$BASE_URL/"
  step "Opening browser: $url"
  if [[ -d "/Applications/Google Chrome.app" ]]; then
    open -a "Google Chrome" "$url" || open "$url"
  else
    open "$url"
  fi
}

echo ""
echo "=== ORNG LED - venue start (macOS) ==="
echo "Log: $LOG_FILE"
echo ""

cd "$REPO_ROOT"
ensure_network
start_server
initialize_show_safe
open_admin_ui

echo ""
echo "All set. In the browser: turn Blackout OFF, then you are live."
echo "Server log: $SERVER_LOG"
echo ""
