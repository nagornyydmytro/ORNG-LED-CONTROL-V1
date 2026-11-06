# Cloud architecture (Phase 2)

## Principle

**Cloudflare is a control plane, not a lighting engine.**

- No Art-Net, no DMX frames, no universe rendering in Workers.
- Admin UI / API → allowlisted commands → Durable Object → device WebSocket → local `AppRuntime` / `InputDispatcher`.
- Scene **content** is versioned immutable bundles in R2; devices pull and apply locally with a safe blackout/disarm swap.

## Components

```text
[Admin browser] --Access JWT--> light.orng.wtf (Worker admin)
                                      |
                                      +--> D1 (metadata, hashes, audit)
                                      +--> R2 (scene bundles)
                                      +--> DeviceDO (per installation)
                                              |
[Device agent] <---Bearer token WSS---- device.light.orng.wtf
       |
       +--> local Art-Net (e.g. 2.0.0.11:6454)
       +--> USB / evdev keyboard
```

## Hosts

| Hostname | Serves |
|----------|--------|
| `light.orng.wtf` | Admin REST + `/admin/ws/device/:id` + Vue admin SPA (`admin-dist`) |
| `device.light.orng.wtf` | `/pair/*`, `/ws/device`, device release download |

Local Wrangler uses `localhost:8787` / `8788` (or path fallback on a single port).

## Auth

- **Admin:** Cloudflare Access JWT (`ACCESS_AUD` + `ACCESS_TEAM_DOMAIN`). CORS limited to `ADMIN_ORIGIN`.
- **Device:** long-lived token; only **SHA-256 hash** stored in D1. Rotate / revoke invalidate immediately.
- Tokens are **never** returned from `GET /admin/devices`.

## Commands

Versioned envelopes (`protocol_version`, `message_id`, `type`, `device_id`, `sent_at`, `payload`).

Allowlist only (see `cloud/src/protocol/types.ts`): state, scene activate/sync, pad/preset, blackout, arm/disarm, momentary strobe/sweep, face, episode/speed, `release_source`.

- Commands are **idempotent** by `command_id`.
- Momentary **press** is **not** replayed after reconnect.
- Cloud holds use `source: cloud:<admin>`, short lease + keepalive; admin socket close releases **only** that source (never linux/web holds).

## Local app integration

- `backend/orng_led/cloud/` — bundle pack/unpack, credentials path, asyncio WSS agent stub.
- Enable agent: `ORNG_CLOUD_DEVICE_URL` + device token file.
- UI: `/cloud` panel; pad components can use `ControlTransport` (`local` | `cloud`).

## Production blockers (if undeployed)

1. Cloudflare account login (`wrangler whoami`)
2. Real D1 `database_id` + R2 bucket
3. DNS for `light.orng.wtf` / `device.light.orng.wtf`
4. Cloudflare Access application on **admin** hostname (device host must stay token-auth, not public admin)
