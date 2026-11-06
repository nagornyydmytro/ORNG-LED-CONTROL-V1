# Device pairing

One-time code links a physical/desktop installation to the cloud control plane.

## Flow

1. **Device** `POST /pair/start` `{ client_challenge, installation_id? }`  
   → `{ pairing_id, code, expires_at }`  
   Code is **8 characters**, shown once. Server stores **code_hash** only. TTL ≈ **10 minutes**. Rate-limited by IP + challenge.

2. **Admin** (Access) `POST /admin/pair/approve`  
   `{ pairing_id, code, name, platform }`  
   Platforms: `raspberry` | `desktop_windows` | `desktop_macos` | `development`  
   Creates/updates installation, stores **token_hash** + encrypted credential envelope.  
   Response has **no plaintext token** — device decrypts via `/pair/complete`.

3. **Device** `POST /pair/complete` `{ pairing_id, client_challenge }`  
   → `{ installation_id, token }` **once**  
   Device saves token (Linux `0600` file / keyring / user config dir — **never** `runtime.yaml`).

4. **Device** WSS `GET /ws/device` with `Authorization: Bearer <token>` (or `?token=` once at upgrade).

## Revoke / rotate

- `POST /admin/devices/:id/revoke` — sets `revoked_at`, deletes credentials  
- `POST /admin/devices/:id/rotate` — new token once; old hash discarded  

`GET /admin/devices` never includes plaintext tokens or hashes.

## Local UI

`/cloud` panel (FastAPI frontend): start code → open **light.orng.wtf** (or local cloud admin) to approve → **Завершити після approve** → credential saved via `/api/cloud/pair/*`.

Local DEV approve helper appears only when `VITE_ORNG_CLOUD_DEV_APPROVE=true`.
