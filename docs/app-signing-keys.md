# Application signing keys

## Active production public keys

Canonical public registry (committed, public only):

`config/app-trusted-keys.production.json`

| key_id | fingerprint (sha256/16) | status |
|--------|-------------------------|--------|
| prod-20261025-02 | a3eb2f705d199258 | active |

Private keys live **only** under `%LOCALAPPDATA%\ORNG-LED-CONTROL\signing\` (outside the repo). Never copy private material into project ZIPs.

Worker var `APP_TRUSTED_KEYS` and device `ORNG_APP_TRUSTED_KEYS` must mirror the same public map.

## Revoked

| key_id | fingerprint | reason |
|--------|-------------|--------|
| prod-20261025-01 | 78c25d350de89eaa | Private key appeared in shared ZIP archives (`.local/ops/signing/…private.bin`). Fully compromised — never sign with it again. |

## Test key

`test-ed25519-1` is for unit/integration only. Allowed only with explicit DEV/TEST flags and never when `ENVIRONMENT=production` / `ORNG_PRODUCTION=1`.

## Rotation

1. Generate new `prod-YYYYMMDD-NN` outside the repo.
2. Add public key to `config/app-trusted-keys.production.json` and Worker `APP_TRUSTED_KEYS`.
3. Keep previous public key until all devices verify new releases.
4. Revoke by removing from trust maps and documenting in this file.
