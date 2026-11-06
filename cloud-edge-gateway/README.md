# orng-led-edge-gateway

Stateless cross-account edge Worker for **Notifications** (`a0ccabcaebdd05adca6a91cb7fddbfee`).

Proxies:

- `light.orng.wtf` → Events origin (Access on this host)
- `device.light.orng.wtf` → Events origin (device tokens; no Access login)

Origin: `https://orng-led-control.orng-events-bot.workers.dev` (Events `c8415eaa…`).

## Hard rules

- **No** D1 / R2 / Durable Objects / KV / Queues
- **No** copy of `orng-led-control`
- **No** scene/app releases storage
- Do **not** run the main cloud `wrangler deploy` while logged into Notifications
- Legacy Notifications D1 `76890f54…` must not be touched

## Secrets

```bash
# while wrangler whoami == Notifications a0ccabca…
npx wrangler secret put GATEWAY_HMAC_SECRET
```

Same secret value must be set on Events Worker (`GATEWAY_HMAC_SECRET`).

## Custom Domains / Access

Added only after local tests + Events origin deploy. Access allow-policy email is asked interactively before creation.
