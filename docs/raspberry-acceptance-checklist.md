# Raspberry hardware acceptance checklist

**Status: `hardware_acceptance: pending`**

Do not mark acceptance complete until every item below is executed on the physical Raspberry Pi 3 Model B+ after arrival. Unit/integration tests do **not** substitute for this list.

## Pre-flight hardware

- [ ] Photo of board (model marking Pi 3 Model B+)
- [ ] RAM / SD capacity recorded
- [ ] Power supply adequate (official or ≥2.5A 5V micro-USB)
- [ ] Case / cooling notes

## OS image

- [ ] Raspberry Pi OS Lite 64-bit (Bookworm+) flashed
- [ ] First boot succeeds
- [ ] `uname -m` → `aarch64`
- [ ] Wi‑Fi connected; SSH works
- [ ] Time sync / NTP OK (`timedatectl`)

## Bootstrap install

- [ ] `bootstrap.sh --check` passes on device
- [ ] Signed `raspberry/arm64` release present (not test-key in production mode)
- [ ] `bootstrap.sh --apply` (or documented apply path) completes
- [ ] `current.json` points under `/opt/orng-led-control/releases/…` (not a git tree)
- [ ] systemd unit installed; service starts; journal healthy
- [ ] Boot posture: blackout + disarmed (`orngctl status` / `/api/health`)

## Cloud pairing + scene

- [ ] `orngctl pair start` → code visible
- [ ] Approve at `light.orng.wtf`
- [ ] `orngctl pair complete`
- [ ] Admin shows device online
- [ ] Assign scene (e.g. ORNG BAR); device applies release
- [ ] Still blackout + disarmed after assign

## App update

- [ ] Stage assigned app update
- [ ] Apply with confirmation / safe lighting
- [ ] Health OK on new version
- [ ] Rollback path verified once

## USB keyboard

- [ ] Enumeration (`orngctl keyboard list`)
- [ ] All ORNG BAR mapped keys
- [ ] Simultaneous H/V holds
- [ ] Unplug → holds released; runtime survives
- [ ] Replug without app restart

## Ethernet Art-Net

- [ ] Static `2.0.0.10/8` on Ethernet; Wi‑Fi keeps default route
- [ ] `ip route get 2.0.0.11` → via eth, src `2.0.0.10`
- [ ] Ping controller (optional corroboration)
- [ ] UDP 6454 diagnostic without arm
- [ ] Manual blackout off + arm → Art-Net frames observed on controller
- [ ] Disarm / blackout restore

## Resilience

- [ ] Power-cycle recovery (blackout/disarmed, last scene)
- [ ] Wi‑Fi loss: local keyboard + Art-Net still work
- [ ] Cloud reconnect without momentary replay
- [ ] Interrupted update recovery / rollback
- [ ] Final **30–60 minute** soak (no crash loop, no auto-arm)

## Sign-off

| Field | Value |
|-------|--------|
| Date | |
| Operator | |
| App version/build | |
| Scene | |
| Result | **pending** / pass / fail |
| Notes | |
