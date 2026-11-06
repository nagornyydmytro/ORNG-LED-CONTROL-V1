/**
 * Ed25519 verify for APPLICATION release manifests (WebCrypto).
 * Signature covers canonical JSON of the signing manifest (includes package hash).
 */
import { b64ToBytes, bytesToB64 } from "../protocol/crypto";
import {
  resolveTrustedAppKey,
  type AppTrustContext,
} from "./trusted_keys";

export const APP_MANIFEST_SCHEMA = 1;
export const CURRENT_UPDATER_PROTOCOL_VERSION = 1;

export const APP_PLATFORMS = [
  "raspberry",
  "desktop_windows",
  "desktop_macos",
  "linux",
] as const;
export type AppPlatform = (typeof APP_PLATFORMS)[number];

export const APP_ARCHES = ["arm64", "amd64", "armv7"] as const;
export type AppArch = (typeof APP_ARCHES)[number];

export const APP_CHANNELS = ["stable", "beta", "dev", "hardware-candidate"] as const;
export type AppChannel = (typeof APP_CHANNELS)[number];

/** Fields covered by Ed25519 signature (canonical key order via stableStringify). */
export interface AppSigningManifest {
  schema: typeof APP_MANIFEST_SCHEMA;
  version: string;
  build_id: string;
  platform: AppPlatform | string;
  arch: AppArch | string;
  channel: AppChannel | string;
  content_sha256: string;
  package_size: number;
  min_updater_version: string | null;
  updater_protocol_version: number;
  notes: string | null;
}

export class AppSignError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "AppSignError";
  }
}

/** Stable JSON for signing — sorted object keys, UTF-8 bytes. */
export function canonicalManifestBytes(manifest: AppSigningManifest): Uint8Array {
  return new TextEncoder().encode(stableStringify(manifest));
}

export function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_k, v) => {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const sorted: Record<string, unknown> = {};
      for (const key of Object.keys(v as object).sort()) {
        sorted[key] = (v as Record<string, unknown>)[key];
      }
      return sorted;
    }
    return v;
  });
}

export async function importEd25519PublicKey(rawB64: string): Promise<CryptoKey> {
  const raw = b64ToBytes(rawB64.trim());
  if (raw.byteLength !== 32) {
    throw new AppSignError(400, "invalid_key", "Ed25519 public key must be 32 bytes");
  }
  return crypto.subtle.importKey("raw", raw, { name: "Ed25519" }, false, ["verify"]);
}

/**
 * Verify signature over canonical manifest. Rejects unsigned / unknown key_id /
 * inactive keys / invalid base64 or length / bad signature.
 * Pass trust context so production hosts reject the test key (fail-closed).
 */
export async function verifyAppReleaseSignature(opts: {
  manifest: AppSigningManifest;
  signatureB64: string;
  keyId: string;
  trust?: AppTrustContext;
}): Promise<void> {
  if (!opts.signatureB64 || !opts.signatureB64.trim()) {
    throw new AppSignError(400, "unsigned", "Application releases must be signed");
  }
  if (!opts.keyId || !opts.keyId.trim()) {
    throw new AppSignError(400, "unsigned", "key_id required for signed app release");
  }
  const trusted = resolveTrustedAppKey(opts.keyId, opts.trust);
  if (!trusted) {
    throw new AppSignError(400, "unknown_key", `Untrusted or inactive key_id: ${opts.keyId}`);
  }
  let signature: Uint8Array;
  try {
    signature = b64ToBytes(opts.signatureB64.trim());
  } catch {
    throw new AppSignError(400, "invalid_signature", "signature_b64 is not valid base64");
  }
  if (signature.byteLength !== 64) {
    throw new AppSignError(400, "invalid_signature", "Ed25519 signature must be 64 bytes");
  }
  let key: CryptoKey;
  try {
    key = await importEd25519PublicKey(trusted.publicKeyRawB64);
  } catch (err) {
    if (err instanceof AppSignError) throw err;
    throw new AppSignError(400, "invalid_key", "Trusted public key is invalid");
  }
  const data = canonicalManifestBytes(opts.manifest);
  const ok = await crypto.subtle.verify({ name: "Ed25519" }, key, signature, data);
  if (!ok) {
    throw new AppSignError(400, "bad_signature", "Manifest signature verification failed");
  }
}

/** Build signing manifest from publish fields (package hash must already be computed). */
export function buildSigningManifest(opts: {
  version: string;
  build_id: string;
  platform: string;
  arch: string;
  channel: string;
  content_sha256: string;
  package_size: number;
  min_updater_version?: string | null;
  updater_protocol_version?: number;
  notes?: string | null;
}): AppSigningManifest {
  return {
    schema: APP_MANIFEST_SCHEMA,
    version: opts.version,
    build_id: opts.build_id,
    platform: opts.platform,
    arch: opts.arch,
    channel: opts.channel,
    content_sha256: opts.content_sha256,
    package_size: opts.package_size,
    min_updater_version: opts.min_updater_version ?? null,
    updater_protocol_version:
      opts.updater_protocol_version ?? CURRENT_UPDATER_PROTOCOL_VERSION,
    notes: opts.notes ?? null,
  };
}

export function assertAppPlatform(platform: string): asserts platform is AppPlatform {
  if (!(APP_PLATFORMS as readonly string[]).includes(platform)) {
    throw new AppSignError(400, "invalid_platform", `Unsupported platform: ${platform}`);
  }
}

export function assertAppArch(arch: string): asserts arch is AppArch {
  if (!(APP_ARCHES as readonly string[]).includes(arch)) {
    throw new AppSignError(400, "invalid_arch", `Unsupported arch: ${arch}`);
  }
}

export function assertAppChannel(channel: string): asserts channel is AppChannel {
  if (!(APP_CHANNELS as readonly string[]).includes(channel)) {
    throw new AppSignError(400, "invalid_channel", `Unsupported channel: ${channel}`);
  }
}

export { bytesToB64 };
