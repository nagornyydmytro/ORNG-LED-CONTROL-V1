# ORNG LED — Cloudflare control plane (Phase 2 + Phase 3 app releases)

TypeScript Worker: **commands + scene releases + signed APPLICATION releases**. Art-Net / DMX stays on the device.

## Hosts

| Host | Role |
|------|------|
| `light.orng.wtf` (prod) / `localhost:8787` (local) | Admin login form + API + static assets (`admin-dist`) |
| `device.light.orng.wtf` (prod) / `localhost:8788` (local) | Pairing + device WSS + scoped device APIs |

Same Worker, strict path/host split. Device host **never** serves `/admin/*` or the login form.

Edge gateway (Notifications account, separate project) proxies Custom Domains → this Worker with HMAC; no D1/R2/DO there.

## Bindings

- **D1** `DB` — installations, credentials (hash only), pairing envelopes, scene + app release metadata, audit
- **R2** `SCENE_RELEASES` — immutable scene bundles `scenes/{id}/v{n}/{hash}.bundle`
- **R2** `APP_RELEASES` — signed app packages at content-addressed `app/by-hash/{sha256}.tar.gz` (+ `.manifest.json`)
  - **PRODUCTION BLOCKER**: create real bucket `orng-led-app-releases` before prod (placeholder name in wrangler)
- **Durable Object** `DeviceDO` — hibernation WebSocket per installation
- **Durable Object** `PairingRateLimitDO` — per-IP pairing rate limits (survives isolate restart)
- **Durable Object** `AdminLoginGuardDO` — login rate limit + temporary lockout
- **Assets** `ASSETS` — `admin-dist` (or point at `../frontend/dist-cloud`)

## Security notes (hardened)

- **Admin auth**: custom login (no Cloudflare Access). Password only as salted `ADMIN_PASSWORD_HASH` secret; signed `HttpOnly; Secure; SameSite=Strict` session cookie + CSRF double-submit. SPA shell is public; all `/admin/*` APIs and admin WSS require session. Lockout after repeated failures (Events DO only).
- **Pairing**: device ECDH public key → Worker stores encrypted `credential_envelope` in D1. Admin `/admin/pair/approve` returns **no** plaintext token. Device `/pair/complete` + `/pair/consume`.
- **WSS**: production rejects `?token=`; use `Authorization: Bearer`. Query tokens only if `DEV_ALLOW_QUERY_TOKEN=1` on localhost.
- **Scopes**: Raspberry `runtime:connect`, `scene:read_assigned`, `app:read_assigned`, `state:report`. Desktop adds `scene:list`, `scene:read`, `scene:publish`.
- **Scene downloads**: ACL checked **before** R2 get (assigned scene + desired/current/rollback window, or `scene:read`).
- **App releases**: logged-in admin publishes (Ed25519-signed; unsigned rejected). Device download contract: `GET .../manifest` then `GET .../package` (legacy bare path deprecated). ACL before R2; arch from heartbeat columns.
- **Trusted keys**: `APP_TRUSTED_KEYS` JSON map (rotatable). `test-ed25519-1` only with DEV flag + localhost — production hosts fail-closed.
- **auto_stage**: assignment flag stages via `stage_app_update` (DB column still `auto_update`); device applies when safe and reports `update_result` / REST `/device/app/update-result`.
- **Rotation**: admin initiates only; old token valid until device ACK; list shows `rotation_pending`.

## Local (Wrangler)

```powershell
cd cloud
npm install
copy .dev.vars.example .dev.vars
npm run db:migrate:local
npm run typecheck
npm test
npm run dev
```

Default: `http://127.0.0.1:8787`. Path fallback routes `/admin/*` as admin and `/pair/*` + `/ws/device` as device when Host is ambiguous (workers.dev).

For a second local device host, run another wrangler process or send `Host: localhost:8788` on device calls.

### Local admin bypass

Set `DEV_BYPASS_ACCESS=1` in `.dev.vars` and call admin APIs with `X-Dev-Access-Email`. Host must be localhost/127.0.0.1. Production uses cookie login only (`ADMIN_PASSWORD_HASH` + `ADMIN_SESSION_SECRET` secrets).

### Admin UI assets

Wrangler serves `./admin-dist` (Vue cloud admin SPA).

```powershell
cd ../frontend
npm install
npm run build:cloud
# writes to cloud/admin-dist
cd ../cloud
npm run dev
```

Local fallback HTML appears only when the `ASSETS` binding is absent (unit tests).

### Tests

```powershell
cd cloud
npm test                 # unit (Node) + workers (workerd DO/WSS)
npm run test:unit
npm run test:workers
```

Real Durable Object + WebSocket e2e lives in `tests/workers/` via `@cloudflare/vitest-plugin`. See `tests/INTEGRATION.md`.

### Deploy (requires Cloudflare auth) — do not run from agents unless asked

```powershell
npx wrangler whoami
# create real D1 + R2, replace database_id in wrangler.jsonc
npm run db:migrate:remote
npx wrangler deploy --dry-run   # validate bundle
# npx wrangler deploy           # production — explicit only
```

If `whoami` fails: stop here — local stack is complete; do not invent account IDs.

## Protocol

See `docs/cloud-architecture.md`, `docs/device-pairing.md`, `docs/scene-releases.md`.
