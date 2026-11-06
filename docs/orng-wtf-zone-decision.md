# Zone `orng.wtf` — account decision (Phase 6)

**Do not transfer the zone or change nameservers without explicit owner confirmation.**

## Facts (verified 2026-10-25)

| Check | Result |
|-------|--------|
| Wrangler login | `orng.events.bot@gmail.com` |
| Visible accounts | **1** — `c8415eaa4895941ba681d8ba1fe2e88e` |
| Zones in this account | **0** (`orng.wtf` absent) |
| LED Worker / D1 / R2 | Already live in `c8415eaa…` |
| Production hosts needed | `light.orng.wtf`, `device.light.orng.wtf` |

`orng.wtf` already serves other ORNG production records (`staff`, `menu`, `promo`, `prices`, `terminal`, …). Blind zone transfer can break them.

## Preferred path (no zone move)

1. Identify the **zone-owning** Cloudflare account (Dashboard → Websites → `orng.wtf`).
2. Prefer deploying the LED Worker **in that same account** (or grant this bot account Workers + DNS edit on the zone).
3. Add **exact** custom domains only:
   - `light.orng.wtf` → Worker (behind Access)
   - `device.light.orng.wtf` → same Worker (no Access)
4. Do **not** change apex NS, do **not** create a duplicate zone, do **not** use cross-account CNAME/proxy unless WebSocket + Access JWT + host separation are proven.

## Alternative (higher risk)

Move the entire `orng.wtf` zone into `c8415eaa…` only after a full DNS inventory and owner sign-off. Not recommended as the default.

## Manual steps when zone-owning account is unavailable from this login

1. Log into the account that owns `orng.wtf`.
2. Export DNS inventory (all records) — sanitized.
3. Either:
   - **A.** Invite `orng.events.bot@gmail.com` with permissions to manage Workers/D1/R2/Access/DNS for `light.*` only, then redeploy Worker there; **or**
   - **B.** Create D1 + R2 + Worker in the zone account and migrate bindings (preferred if bot must stay out of apex DNS).
4. Create Access application for `light.orng.wtf` only (allow owner email, 24h session, no Everyone, no bypass).
5. Set Worker secrets/vars `ACCESS_AUD`, `ACCESS_TEAM_DOMAIN`.
6. Smoke TLS + admin/device host separation; then disable `workers_dev`.

## Current status

Custom domains + Access: **blocked** until zone-owning account is available.  
`workers.dev` remains diagnostic only — not production control plane.
