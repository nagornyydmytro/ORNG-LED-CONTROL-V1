# Workers integration tests

## Goal

Real `pair → approve → cold-start complete → Bearer WSS → publish → assign → sync → download ACL → track_latest v2 → admin WSS press/keepalive/release → disconnect source-scoped → no momentary replay` against **workerd** + Durable Objects + hibernation WebSockets.

Phase 3 APPLICATION releases (separate from scenes): `tests/workers/app-releases.e2e.test.ts` covers Access-only publish, unsigned rejected, download **manifest+package** contract / wrong platform, track_latest, idempotent publish, bulk rollout, rollback, revoked denied, audit, reconnect `update-result` ACK (idempotent). Control-plane fake ACKs in tests are **not** a real OS apply.

## How it runs

`npm test` uses Vitest 4 **projects** in `vitest.config.ts`:

| Project | Runtime | Files |
|---------|---------|-------|
| `unit` | Node + memoryEnv | `tests/*.test.ts` (excludes `workers/`) |
| `workers` | `@cloudflare/vitest-plugin` (Miniflare/workerd) | `tests/workers/**/*.test.ts` |

Workers DO WebSockets require shared storage (Cloudflare known issue):

```text
vitest run --maxWorkers=1 --no-isolate
```

D1 migrations: `readD1Migrations` + `applyD1Migrations` (`tests/workers/apply-migrations.ts`).

## What uses real DO / WebSocketPair

| Test | Real `DEVICE_DO` | Real `WebSocketPair` / hibernation WS |
|------|------------------|----------------------------------------|
| `tests/workers/control-plane.e2e.test.ts` (main flow) | **Yes** (`env.DEVICE_DO` + `exports.default.fetch` upgrade) | **Yes** (device Bearer WSS + admin WSS tabs) |
| `tests/workers/app-releases.e2e.test.ts` (Phase 3 app) | **Yes** (reconnect desired-app commands) | **Yes** (device Bearer WSS) |
| `tests/workers/control-plane.e2e.test.ts` (ACCESS 503) | N/A | No (HTTP only) |
| `tests/app-releases.test.ts` | FakeDeviceDONamespace | No |
| `tests/device-do.test.ts` offline/status | In-process MemoryDOState | No (skipped on Node) |
| `tests/device-do.test.ts` `runIf(WebSocketPair)` | In-process only | Only if host has `WebSocketPair` (skipped on Node) |
| `tests/control-plane.test.ts` | FakeDeviceDONamespace | No |

## Commands

```powershell
cd cloud
npm test                 # unit + workers (--maxWorkers=1 --no-isolate)
npm run test:unit        # memory-env only
npm run test:workers     # workerd e2e only
```

## Note on package rename

Cloudflare renamed `@cloudflare/vitest-pool-workers` → `@cloudflare/vitest-plugin` (Vitest ≥4.1). This repo uses the new package.
