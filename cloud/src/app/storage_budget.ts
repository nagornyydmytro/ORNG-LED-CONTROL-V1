/**
 * ORNG R2 storage budget — check BEFORE put() to avoid GB-month peak spikes.
 *
 * Free tier is account-wide; ORNG hard budget leaves a 2GB safety margin.
 * Status is `conditional` until correct-account usage is verified at deploy time.
 */

import type { Env } from "../env";

/** Combined hard budget for scene + app ORNG buckets (8 GB of 10 GB free). */
export const ORNG_HARD_BUDGET_BYTES = 8_000_000_000;
export const ORNG_SOFT_WARN_RATIO = 0.7;
export const ORNG_HARD_BLOCK_RATIO = 0.9;

export const APP_RETAIN_PER_TARGET = 10;
export const SCENE_RETAIN_PER_SCENE = 50;

export type StorageGuaranteeStatus = "guaranteed_for_orng_budget" | "conditional";

export class StorageBudgetError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "StorageBudgetError";
  }
}

export interface BucketUsage {
  binding: "SCENE_RELEASES" | "APP_RELEASES";
  bucket: string;
  bytes: number;
  objects: number;
}

export interface StorageReport {
  buckets: BucketUsage[];
  total_bytes: number;
  hard_budget_bytes: number;
  soft_warn_bytes: number;
  hard_block_bytes: number;
  percent: number;
  level: "ok" | "warn" | "error";
  guarantee_status: StorageGuaranteeStatus;
  guarantee_reason: string;
}

async function listAllObjects(
  bucket: R2Bucket,
): Promise<{ bytes: number; objects: number; keys: string[] }> {
  let cursor: string | undefined;
  let bytes = 0;
  let objects = 0;
  const keys: string[] = [];
  do {
    const page = await bucket.list({ cursor, limit: 1000 });
    for (const obj of page.objects) {
      bytes += obj.size;
      objects += 1;
      keys.push(obj.key);
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return { bytes, objects, keys };
}

export async function measureOrngStorage(env: Env): Promise<StorageReport> {
  const scene = await listAllObjects(env.SCENE_RELEASES);
  const app = await listAllObjects(env.APP_RELEASES);
  const total = scene.bytes + app.bytes;
  const soft = Math.floor(ORNG_HARD_BUDGET_BYTES * ORNG_SOFT_WARN_RATIO);
  const block = Math.floor(ORNG_HARD_BUDGET_BYTES * ORNG_HARD_BLOCK_RATIO);
  const percent = ORNG_HARD_BUDGET_BYTES
    ? Math.round((total / ORNG_HARD_BUDGET_BYTES) * 1000) / 10
    : 0;
  let level: "ok" | "warn" | "error" = "ok";
  if (total >= block) level = "error";
  else if (total >= soft) level = "warn";
  return {
    buckets: [
      {
        binding: "SCENE_RELEASES",
        bucket: "orng-led-scene-releases",
        bytes: scene.bytes,
        objects: scene.objects,
      },
      {
        binding: "APP_RELEASES",
        bucket: "orng-led-app-releases",
        bytes: app.bytes,
        objects: app.objects,
      },
    ],
    total_bytes: total,
    hard_budget_bytes: ORNG_HARD_BUDGET_BYTES,
    soft_warn_bytes: soft,
    hard_block_bytes: block,
    percent,
    level,
    // Account c8415eaa… measured empty (2 ORNG buckets only). Stay conditional
    // until custom domains live on the final zone-owning production account.
    guarantee_status: "conditional",
    guarantee_reason:
      "Account c8415eaa measured: only orng-led-*-releases buckets, 0 bytes; arm64 candidate ~71MB fits 8GB hard budget. Conditional until zone-owning production account + Access are finalized.",
  };
}

/** Fail closed before put() if unique new object would exceed hard budget. */
export async function assertWithinBudget(
  env: Env,
  newUniqueObjectSize: number,
): Promise<StorageReport> {
  const report = await measureOrngStorage(env);
  if (newUniqueObjectSize < 0) {
    throw new StorageBudgetError(400, "bad_size", "Negative object size");
  }
  if (report.total_bytes + newUniqueObjectSize > ORNG_HARD_BUDGET_BYTES) {
    throw new StorageBudgetError(
      507,
      "storage_budget_exceeded",
      `ORNG R2 budget exceeded: have ${report.total_bytes}, need +${newUniqueObjectSize}, hard=${ORNG_HARD_BUDGET_BYTES}`,
    );
  }
  return report;
}

export interface PruneCandidate {
  key: string;
  protected: boolean;
  reason?: string;
}

/**
 * Dry-run prune preview. Never auto-deletes assigned/current/previous/staged.
 * Caller must pass protected keys from D1 assignments + device update state.
 */
export function planAppRetentionPrune(opts: {
  keys: string[];
  protectedKeys: Set<string>;
  retainPerTarget?: number;
}): { deletable: PruneCandidate[]; retained: PruneCandidate[]; orphans: string[] } {
  const retain = opts.retainPerTarget ?? APP_RETAIN_PER_TARGET;
  // keys: app/{platform}/{arch}/{channel}/{version}-{build}.tar.gz
  const groups = new Map<string, string[]>();
  const orphans: string[] = [];
  for (const key of opts.keys) {
    const m = /^app\/([^/]+)\/([^/]+)\/([^/]+)\//.exec(key);
    if (!m) {
      orphans.push(key);
      continue;
    }
    const g = `${m[1]}/${m[2]}/${m[3]}`;
    const list = groups.get(g) ?? [];
    list.push(key);
    groups.set(g, list);
  }
  const deletable: PruneCandidate[] = [];
  const retained: PruneCandidate[] = [];
  for (const [, list] of groups) {
    const sorted = [...list].sort().reverse();
    sorted.forEach((key, idx) => {
      if (opts.protectedKeys.has(key)) {
        retained.push({ key, protected: true, reason: "assigned_or_rollback" });
        return;
      }
      if (idx < retain) {
        retained.push({ key, protected: false, reason: "within_retain_window" });
      } else {
        deletable.push({ key, protected: false, reason: "beyond_retain_window" });
      }
    });
  }
  return { deletable, retained, orphans };
}
