# Raspberry runtime contract (canonical)

**Status:** contract for Phase 4 zero-touch bootstrap.  
**Hardware acceptance:** `pending` until the checklist in `docs/raspberry-acceptance-checklist.md` is executed on real hardware.

## Supported hardware / OS

| Item | Requirement |
|------|-------------|
| Board | Raspberry Pi 3 Model B+ (and later 64-bit Pi models) |
| Architecture | **arm64** (`aarch64`) only — 32-bit armv7 is unsupported |
| OS | **Raspberry Pi OS Lite 64-bit (Bookworm)** or newer Lite 64-bit |
| Kernel | stock Raspberry Pi OS kernel with USB HID / NetworkManager |

## Python runtime (honest mismatch closure)

Raspberry Pi OS Bookworm ships **system Python 3.11**.  
Application package `requires-python` remains **`>=3.12`**.

| Layer | Interpreter |
|-------|-------------|
| Bootstrap / `orngctl` / helper host scripts | System `python3` (≥3.11 OK for install tooling only) |
| Managed app release | **Must** provide a release-local interpreter: `releases/<id>/runtime/bin/python` **or** `releases/<id>/.venv/bin/python` reporting **≥3.12** |

Bootstrap **refuses** to set `current.json` unless the staged release’s runtime Python version is ≥3.12.  
The device **never** installs from PyPI at runtime. Offline `wheelhouse/` + release runtime only.

Build/publish for `platform=raspberry` `arch=arm64` must ship that runtime (or a prebuilt `.venv`) inside the signed package. Leaving “system Python will do” is a contract violation.

## System packages (install-time / `--apply` on the Pi)

Documented intents (scripts emit commands; this Phase 4 task does not run them on the host):

- `python3`, `python3-venv` (bootstrap host only)
- `network-manager`
- `systemd`
- optional: `iproute2`, `iputils-ping` for diagnostics

`evdev` comes from the release wheelhouse (`orng-led-control[linux]`), not apt `python3-evdev`, unless explicitly chosen later.

## Filesystem layout

| Path | Role | Owner | Mode |
|------|------|-------|------|
| `/opt/orng-led-control` | App root (`bootstrap/`, `releases/`, pointers) | `orng-led:orng-led` | `0755` |
| `/opt/orng-led-control/bootstrap/` | External helper (not replaceable by release) | `orng-led` | `0755` |
| `/var/lib/orng-led-control` | Data root (scenes, token, cloud metadata) | `orng-led` | `0700` |
| `/etc/orng-led-control` | Env / templates | `root:orng-led` | `0750` |
| `/etc/orng-led-control/device.env` | Non-secret env (or `0600` if secrets later) | `root:orng-led` | `0640` / `0600` |
| journald | Logs (`journalctl -u orng-led-control`) | — | — |

`current.json` **never** points at a git checkout.

## User / permissions

- System user/group: `orng-led` (no interactive login, no home shell)
- Supplementary group: `input` (USB `/dev/input/event*`) via udev/group strategy
- Helper runs as `orng-led`; release child inherits

## Networking

| Link | Role |
|------|------|
| Wi‑Fi | Internet, DNS, **default route**, cloud HTTPS/WSS outbound only |
| Ethernet (`eth0` or configured name) | Art-Net only: **`2.0.0.10/8`**, **no gateway**, **no DNS**, `never-default` |
| Art-Net target | `2.0.0.11:6454` via eth, source `2.0.0.10` |

Cloud never emits Art-Net. Backend listens locally (default `127.0.0.1` or configured host).

## Boot posture

Every boot / install / update / restart:

- **blackout = true**
- **output_armed = false**
- never auto-arm

## Cloud

- Outbound only: HTTPS + WSS to device host
- No inbound cloud control of shell/apt/reboot
- Pairing credentials under data root; never in release tree or logs
