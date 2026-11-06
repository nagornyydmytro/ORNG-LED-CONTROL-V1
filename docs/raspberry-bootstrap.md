# Raspberry Pi bootstrap (cloud + Art-Net)

> **Status:** Phase 4 automation + contract. **Hardware acceptance is NOT done** — see `docs/raspberry-acceptance-checklist.md` (`hardware_acceptance: pending`).

Canonical contract: **`docs/raspberry-runtime-contract.md`**.

## Quick path (on the Pi, after OS install)

```bash
# Always start with check / dry-run (no host mutation from this repo task either)
./scripts/raspberry/preflight.sh
./scripts/raspberry/bootstrap.sh --dry-run --json
./scripts/raspberry/bootstrap.sh --root /tmp/orng-root --apply --release /path/to/verified/raspberry-arm64-release --allow-test-key  # lab only

# Production on device (explicit):
# ./scripts/raspberry/bootstrap.sh --apply --release /path/to/signed/release
```

`orngctl` (local diagnostics / pairing):

```bash
./scripts/raspberry/orngctl status --json
./scripts/raspberry/orngctl doctor --json
./scripts/raspberry/orngctl pair start
# approve at light.orng.wtf
./scripts/raspberry/orngctl pair complete
./scripts/raspberry/orngctl network check   # plan only
./scripts/raspberry/orngctl blackout
./scripts/raspberry/orngctl disarm
```

Network Art-Net Ethernet planner (never auto-applied from CI host):

```bash
./scripts/raspberry/network.sh --json
```

## Layout

| Path | Role |
|------|------|
| `/opt/orng-led-control` | App + bootstrap helper + releases |
| `/var/lib/orng-led-control` | Scenes, token, cloud metadata |
| `/etc/orng-led-control/device.env` | Non-secret env |
| `scripts/systemd/orng-led-control.service` | Static unit → external helper |

## Python

Bookworm system Python is 3.11. Managed releases **must** ship `runtime/` or `.venv` with **Python ≥3.12**. Bootstrap refuses `current.json` without that.

## Boot posture

Blackout + disarmed. Never auto-arm after boot/install/update/restart.
