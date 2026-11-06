# Cloud deploy status (Phase 6)

Updated: 2026-10-25

## Correct account (trusted)

| Item | Value |
|------|-------|
| Account | `c8415eaa4895941ba681d8ba1fe2e88e` |
| Owner email | `orng.events.bot@gmail.com` |
| Zone `orng.wtf` | **NOT in this account** (zones=0) — see `docs/orng-wtf-zone-decision.md` |
| D1 `orng-led-control` | `7cc1c733-0a31-4a35-a31c-58edb29f1bfc` |
| R2 scene | `orng-led-scene-releases` (Standard, private, **0 objects**) |
| R2 app | `orng-led-app-releases` (Standard, private, **0 objects**) |
| Account R2 buckets | **only** the two ORNG buckets above |
| Worker version | `d3beeb96-4ba5-4f3c-9dcc-d13ac071828b` |
| workers.dev | diagnostic only (`orng-led-control.…workers.dev`) |
| Access / custom domains | **blocked** — zone-owning account unavailable from this login |
| Installations | 0 |
| Scene publish | **not published** (blocked on Access/custom domains) |
| App release | **not published** |
| Signing | `prod-20261025-02` (public); `prod-20261025-01` **revoked** |
| hardware_acceptance | **pending** |

## Arm64 hardware-candidate (QEMU proof)

| Item | Value |
|------|-------|
| Runtime | python-build-standalone CPython **3.12.7** aarch64 (`20241016` install_only) |
| PBS sha256 | `bba3c6be6153f715f2941da34f3a6a69c2d0035c9c5396bc5bb68c6d2bd1065a` |
| Channel | `hardware-candidate` (test key; not production stable) |
| Package | `orng-app-0.2.0-dirty-ab7ea8c7bfd3-657ef91560ae-raspberry-arm64.tar.gz` |
| Package sha256 | `8945aa31dd9392fc343ad3da37339491f7d55a6743ac91233e2773e13c15b45f` |
| Package size | 71 347 237 bytes (&lt; 80 MB) |
| QEMU health | ready / blackout / disarmed / degraded bootstrap-empty |
| R2 guarantee | **conditional** (this account empty; production may move with zone) |

## Stale wrong-account (do not use / do not auto-delete)

Account `a0ccabca…` — see prior Phase 5 notes. Never deploy there.

## Blockers before `ready_without_hardware`

1. Access zone-owning account for `orng.wtf` **without** NS/zone transfer by default
2. Custom domains `light.orng.wtf` + `device.light.orng.wtf` + Access on admin only
3. Publish ORNG BAR scene after Access is live
4. Re-verify R2 account-wide usage on the final production account
