# Application releases (Phase 3)

Scene releases and application releases are **separate**:

| | Scene release | App release |
|--|---------------|-------------|
| Contents | `scene.yaml`, presets, patch, layout | Python package, `frontend_dist`, wheelhouse, launcher |
| Touches | Assigned scene under data root | Managed `releases/<version-build>/` only |
| Never touches | App code | Scenes, credentials, cloud metadata, `runtime.yaml` |

## Version

Single SemVer source: repository root `VERSION` (currently `0.2.0`).

`build_id` for production builds is an immutable git SHA or CI build id. Dirty trees are refused unless `--allow-dirty`, and then `build_id` embeds a content-hash suffix so two dirty payloads cannot share an identity.

## Runnable release layout

```
releases/<version>-<build_id>/
  RELEASE.json       # identity + entrypoint + health + runtime (required)
  run.py             # prefers .venv interpreter for this release
  orng_led/          # application package
  frontend_dist/     # Vite production assets (served via ORNG_FRONTEND_DIST)
  wheelhouse/        # offline dependency wheels (no PyPI at runtime)
  requirements.txt   # bootstrap pointer for per-release venv
  .venv/             # created on install/first-run from wheelhouse (not in tarball)
```

A release must start without a git checkout, Node.js, or PyPI access. The external helper launches the **release** interpreter (`.venv` / `runtime`), never a random global `orng_led` install.

## Managed install layout

| OS | App root | Data root |
|----|----------|-----------|
| Windows | `%LOCALAPPDATA%\ORNG-LED-CONTROL\` | `...\ORNG-LED-CONTROL\data\` |
| macOS | `~/Library/Application Support/ORNG-LED-CONTROL/app` | `.../data` |
| Linux/Raspberry | `/opt/orng-led-control/` | `/var/lib/orng-led-control/` |

App root holds `bootstrap/`, `releases/`, `current.json`, `previous.json`, `update-state.json`, `apply-request.json`.  
Data root holds scenes, credentials, cloud metadata.

**Developer `.git` checkouts always refuse remote self-update**, even if `current.json` or `ORNG_INSTALL_MODE=managed` is set (test-only escape: `ORNG_ALLOW_MANAGED_IN_GIT=1`).

Installers (`scripts/managed-install.ps1` / `.sh`) never point `current.json` at the repo and never write into the source tree. Systemd unit is a static template under `scripts/systemd/` (not auto-enabled).

## Format & signing

- Format: `orng-app-release-v1` (deterministic gzip tar + Ed25519 over canonical signing manifest).
- Signed manifest includes: `format`, exact `files`+hashes, `entrypoint`, `launcher`, `health_endpoint`, `runtime`, identity fields, `min_updater_version`, `updater_protocol_version`.
- Trusted public keys: `backend/orng_led/updater/trusted_keys.py` and `cloud/src/app/trusted_keys.ts` (+ rotatable `ORNG_APP_TRUSTED_KEYS` / `APP_TRUSTED_KEYS`).
- Test private key lives **only** in `backend/tests/fixtures/app_signing.py` and `cloud/tests/helpers/appSigning.ts` — never in runtime.
- Production refuses the test key (fail-closed). Dev/test requires explicit `ORNG_DEV_ALLOW_TEST_APP_KEYS=1` / Worker `DEV_ALLOW_TEST_APP_KEYS`.
- Build: `python scripts/build-app-release.py --platform … --arch … --out dist-app` with `ORNG_APP_SIGNING_KEY`, or `--allow-test-key` for local/CI only.

## Cloud

- Migrations `0003_app_releases.sql` + `0004_installation_app_fields.sql` (arch / install_mode / updater_protocol_version on installations)
- R2 binding `APP_RELEASES` — objects `app/by-hash/{content_sha256}.tar.gz` (concurrent publish safe)
- Admin Access publishes; device Bearer only `app:read_assigned`
- Heartbeat persists `platform`, `arch`, `install_mode`, `updater_protocol_version`, app version/build — no arm64 fallback for unknown arch

### Device download contract

```
GET /device/app/releases/:platform/:arch/:version/:build_id/manifest
→ {
    "manifest": { ...signed fields including content_sha256, package_size, files... },
    "signature_b64": "...",
    "key_id": "...",
    "content_sha256": "...",
    "package_size": 12345,
    "package_url": "/device/app/releases/.../package"
  }

GET /device/app/releases/:platform/:arch/:version/:build_id/package
→ application/gzip bytes
  Content-Length, x-content-sha256
```

Legacy `GET .../:build_id` (no suffix) still returns the package with `Deprecation: true`.

### Update result / ACK

- `accepted` ACK ≠ `applied`. Worker must **not** drop pending on `accepted` for app updates.
- Terminal: WSS `type: update_result` or REST `POST /device/app/update-result` with `applied` | `failed` | `rolled_back` (+ original `command_id`).
- Pending device result clears only after successful send / 2xx; network failure retries; redelivery is idempotent.
- Fake-device control-plane tests are labeled as fake — not a real OS apply.

### auto_stage

DB column remains `auto_update`. API exposes **`auto_stage`**: when true, cloud sends `stage_app_update` only. The device applies when safe (`apply_app_update`) after blackout/disarm confirmation and reports outcome via `update_result`. This is **not** silent full auto-apply. Developer checkouts never auto-update.

## State machine & helper

`idle → checking → downloading → verifying → staged → [waiting_for_safe_state] → applying → restarting → health_check → healthy | rolling_back → rolled_back | failed`

1. Runtime `apply` confirms safe lighting (holds released, blackout, dark frames, disarmed) or aborts.
2. Writes `apply-request.json` and returns **`restarting`** (API can flush).
3. External `bootstrap/updater_helper.py` (outside replaceable release) stops child, swaps `current.json`, starts new release, polls `/api/health` for expected version/build + `ready=true` + `blackout=true` + `output_armed=false`.
4. Success → pending `applied`; failure → previous pointer + health; previous also unhealthy → `failed`. Never auto-arm.

Staging unpacks to `releases/.staging/<uuid>`, verifies fully, then atomic rename. Current/previous are never deleted by stage/cancel. Async + interprocess locks; no `threading.RLock` across `await`.

## Production blockers

- Real R2 bucket + Cloudflare deploy
- Production Ed25519 public key (replace `prod-placeholder`)
- Access AUD / DNS
- Physical Raspberry acceptance
- Windows Authenticode / Apple notarization (explicitly out of scope)
