# R2 billing / free-tier runbook (ORNG LED CONTROL)

## Free tier (account-wide)

Cloudflare R2 free allowance is **shared across the entire account** (not per bucket).

ORNG policy:

- Hard ORNG budget: **8 GB** across `orng-led-scene-releases` + `orng-led-app-releases`
- Remaining **~2 GB**: safety margin for other account buckets / spikes
- Storage class: **Standard** only
- Buckets: **private** (no `r2.dev`, no public custom domain)
- Upload path: Worker ACL only; budget checked **before** `put()`

## Size model (apps)

| Assumption | Value |
|------------|-------|
| Max package | 80 MB (hard code limit); target ≤60 MB |
| Targets | 4 (e.g. raspberry/arm64, windows/amd64, macos/arm64, macos/amd64) |
| Retained per target | 10 |
| Max app storage | 80×4×10 = **3.2 GB** |

Scene bundles (tens of KB) are negligible. Device count does **not** multiply storage (immutable shared objects). Downloads are Class B ops, not extra copies / egress fees.

## Guarantee status

`guaranteed_for_orng_budget` only when:

1. Correct-account total R2 usage verified
2. ORNG hard guard deployed
3. Buckets private
4. Other buckets leave safety margin
5. Real arm64 package measured
6. Retention tests pass

Otherwise: **`conditional`**.

## Prune

- Dry-run preview + explicit confirm only
- Never prune assigned / current / previous / staged / applying
- D1 + R2 deletes must be idempotent and paired
