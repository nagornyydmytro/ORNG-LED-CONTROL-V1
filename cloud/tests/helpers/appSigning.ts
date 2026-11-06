/**
 * Test-only Ed25519 signer matching TRUSTED_APP_KEYS[test-ed25519-1].
 * Never use in production.
 */
import {
  buildSigningManifest,
  canonicalManifestBytes,
  type AppSigningManifest,
} from "../../src/app/signing";
import { TEST_APP_KEY_ID } from "../../src/app/trusted_keys";
import { bytesToB64, sha256Bytes } from "../../src/protocol/crypto";

/** PKCS8 private key paired with test-ed25519-1 public key. */
const TEST_PRIVATE_PKCS8_B64 = "MC4CAQAwBQYDK2VwBCIEIMP0wuUiNAi/QM2mfBQU/BtkAR+UYbKgIuUKfKYnXS0V";

let cachedPrivate: CryptoKey | null = null;

async function testPrivateKey(): Promise<CryptoKey> {
  if (cachedPrivate) return cachedPrivate;
  const raw = Uint8Array.from(atob(TEST_PRIVATE_PKCS8_B64), (c) => c.charCodeAt(0));
  cachedPrivate = await crypto.subtle.importKey("pkcs8", raw, { name: "Ed25519" }, false, [
    "sign",
  ]);
  return cachedPrivate;
}

export async function signAppManifest(manifest: AppSigningManifest): Promise<string> {
  const key = await testPrivateKey();
  const data = canonicalManifestBytes(manifest);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, key, data));
  return bytesToB64(sig);
}

export async function makeSignedAppPackage(opts: {
  version: string;
  build_id: string;
  platform?: string;
  arch?: string;
  channel?: string;
  packageBytes?: Uint8Array;
  notes?: string | null;
}): Promise<{
  packageBytes: Uint8Array;
  manifest: AppSigningManifest;
  signature_b64: string;
  key_id: string;
  package_base64: string;
}> {
  const packageBytes =
    opts.packageBytes ?? new TextEncoder().encode(`fake-app-pkg-${opts.version}-${opts.build_id}`);
  const content_sha256 = await sha256Bytes(packageBytes);
  const manifest = buildSigningManifest({
    version: opts.version,
    build_id: opts.build_id,
    platform: opts.platform ?? "raspberry",
    arch: opts.arch ?? "arm64",
    channel: opts.channel ?? "stable",
    content_sha256,
    package_size: packageBytes.byteLength,
    notes: opts.notes ?? null,
  });
  const signature_b64 = await signAppManifest(manifest);
  return {
    packageBytes,
    manifest,
    signature_b64,
    key_id: TEST_APP_KEY_ID,
    package_base64: bytesToB64(packageBytes),
  };
}
