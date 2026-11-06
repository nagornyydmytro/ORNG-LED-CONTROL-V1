/**
 * Trusted Ed25519 public keys for APPLICATION release signatures.
 *
 * Production must NOT trust test-ed25519-1 unless an explicit DEV/TEST flag
 * is set AND the request host is localhost (fail-closed otherwise).
 *
 * Rotatable keys: set env/wrangler var APP_TRUSTED_KEYS to a JSON map
 * `{ "key_id": "<raw-32-byte-public-key-base64>", ... }`.
 */

import type { Env } from "../env";
import { isLocalhostHost, isProductionAdminHost, isProductionEnvironment } from "../env";

export interface TrustedAppKey {
  /** Raw 32-byte Ed25519 public key, standard base64. */
  publicKeyRawB64: string;
  /** Human-readable note (never used for crypto). */
  note: string;
  /** When false, key is documentation-only and must not verify. */
  active: boolean;
}

export interface AppTrustContext {
  env?: Env;
  hostHeader?: string | null;
}

/** Test key — matches private key in tests/helpers/appSigning.ts (never ship as prod). */
export const TEST_APP_KEY_ID = "test-ed25519-1";

/** Active production key — public only; matches config/app-trusted-keys.production.json */
export const PROD_APP_KEY_ID = "prod-20261025-02";

/** Compromised — never trust. */
export const REVOKED_APP_KEY_IDS = new Set(["prod-20261025-01"]);

const BUILTIN_KEYS: Readonly<Record<string, TrustedAppKey>> = {
  [TEST_APP_KEY_ID]: {
    publicKeyRawB64: "qmIQ+29RPmXtiyrTCSXNzUnzv/7dtNNQjV5SlWM5DkU=",
    note: "Unit/integration test key only — never use in production",
    active: true,
  },
  [PROD_APP_KEY_ID]: {
    publicKeyRawB64: "ISrcfybhnOYYpf0cvAKch2Ag2RBT0R4MOdXUadU7WWk=",
    note: "Production Ed25519 (prod-20261025-02); private outside repo",
    active: true,
  },
};

/** @deprecated Prefer resolveTrustedAppKey with trust context. */
export const TRUSTED_APP_KEYS: Readonly<Record<string, TrustedAppKey>> = BUILTIN_KEYS;

function isProductionDeviceHost(hostHeader: string | null | undefined, env: Env): boolean {
  const portHost = (hostHeader ?? "").toLowerCase().trim();
  const hostOnly = portHost.split(":")[0] ?? "";
  const prod = (env.DEVICE_HOST || "device.light.orng.wtf").toLowerCase();
  return hostOnly === prod || portHost === prod || hostOnly === "device.light.orng.wtf";
}

/** True when Host is a configured production admin/device hostname. */
export function isProductionAppHost(
  hostHeader: string | null | undefined,
  env?: Env,
): boolean {
  if (!env || !hostHeader) return false;
  return (
    isProductionAdminHost(hostHeader, env) || isProductionDeviceHost(hostHeader, env)
  );
}

/**
 * Test signing key is allowed only with an explicit DEV/TEST flag AND
 * localhost (or missing host in unit memoryEnv). Production hosts are
 * always fail-closed for the test key.
 */
export function allowTestAppSigningKey(
  env?: Env,
  hostHeader?: string | null,
): boolean {
  if (!env) return false;
  if (isProductionEnvironment(env)) return false;
  const flag =
    env.DEV_ALLOW_TEST_APP_KEYS === "1" || env.DEV_BYPASS_ACCESS === "1";
  if (!flag) return false;
  if (isProductionAppHost(hostHeader, env)) return false;
  if (hostHeader == null || hostHeader === "") {
    // Unit/memoryEnv paths often omit Host; flag alone is enough off production hosts.
    return true;
  }
  return isLocalhostHost(hostHeader);
}

function parseEnvTrustedKeys(raw: string | undefined): Record<string, TrustedAppKey> {
  if (!raw || !raw.trim()) return {};
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const out: Record<string, TrustedAppKey> = {};
    for (const [keyId, value] of Object.entries(parsed)) {
      const b64 = typeof value === "string" ? value.trim() : "";
      if (!keyId.trim() || !b64) continue;
      out[keyId] = {
        publicKeyRawB64: b64,
        note: "Configured via APP_TRUSTED_KEYS",
        active: true,
      };
    }
    return out;
  } catch {
    return {};
  }
}

/**
 * Resolve a trusted public key for verification.
 * Env APP_TRUSTED_KEYS overrides / extends builtins. Test key gated by allowTestAppSigningKey.
 */
export function resolveTrustedAppKey(
  keyId: string,
  opts?: AppTrustContext,
): TrustedAppKey | null {
  if (!keyId?.trim()) return null;
  if (REVOKED_APP_KEY_IDS.has(keyId)) return null;
  const envKeys = parseEnvTrustedKeys(opts?.env?.APP_TRUSTED_KEYS);

  if (keyId === TEST_APP_KEY_ID) {
    if (!allowTestAppSigningKey(opts?.env, opts?.hostHeader)) {
      return null;
    }
    // Env can override the test public key for local experiments.
    return envKeys[keyId] ?? BUILTIN_KEYS[TEST_APP_KEY_ID] ?? null;
  }

  const fromEnv = envKeys[keyId];
  if (fromEnv?.active && fromEnv.publicKeyRawB64) return fromEnv;

  const builtin = BUILTIN_KEYS[keyId];
  if (!builtin || !builtin.active || !builtin.publicKeyRawB64) return null;
  return builtin;
}

/** @deprecated Use resolveTrustedAppKey with trust context. */
export function getTrustedAppKey(keyId: string): TrustedAppKey | null {
  // Legacy no-context path: never expose test key (fail-closed).
  if (keyId === TEST_APP_KEY_ID) return null;
  const builtin = BUILTIN_KEYS[keyId];
  if (!builtin || !builtin.active || !builtin.publicKeyRawB64) return null;
  return builtin;
}
