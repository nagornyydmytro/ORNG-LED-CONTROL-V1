/**
 * Salted password hash verify for the single admin account.
 * Secret format only — never store or log plaintext passwords.
 *
 * ADMIN_PASSWORD_HASH = v1$pbkdf2-sha256$<iterations>$<salt_b64url>$<hash_b64url>
 */

import { timingSafeEqual } from "../protocol/crypto";

const textEncoder = new TextEncoder();

export const PASSWORD_HASH_PREFIX = "v1$pbkdf2-sha256$";
/**
 * Cloudflare Workers reject PBKDF2 iteration counts above 100_000
 * (`NotSupportedError` → HTTP 1101). Use the platform maximum.
 */
export const DEFAULT_PBKDF2_ITERATIONS = 100_000;
export const MIN_PBKDF2_ITERATIONS = 100_000;
export const MAX_PBKDF2_ITERATIONS = 100_000;

function b64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function b64urlDecode(input: string): Uint8Array {
  const padded = input.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((input.length + 3) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export type ParsedPasswordHash = {
  iterations: number;
  salt: Uint8Array;
  hash: Uint8Array;
};

export function parsePasswordHash(encoded: string): ParsedPasswordHash | null {
  const raw = encoded.trim();
  if (!raw.startsWith(PASSWORD_HASH_PREFIX)) return null;
  const parts = raw.split("$");
  // v1, pbkdf2-sha256, iterations, salt, hash
  if (parts.length !== 5) return null;
  const iterations = Number(parts[2]);
  if (
    !Number.isFinite(iterations) ||
    iterations < MIN_PBKDF2_ITERATIONS ||
    iterations > MAX_PBKDF2_ITERATIONS
  ) {
    return null;
  }
  try {
    const salt = b64urlDecode(parts[3]!);
    const hash = b64urlDecode(parts[4]!);
    if (salt.byteLength < 16 || hash.byteLength < 32) return null;
    return { iterations, salt, hash };
  } catch {
    return null;
  }
}

async function pbkdf2Sha256(
  password: string,
  salt: Uint8Array,
  iterations: number,
  length = 32,
): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: salt as BufferSource,
      iterations,
    },
    key,
    length * 8,
  );
  return new Uint8Array(bits);
}

/** Build a secret-ready hash string (run offline; never commit plaintext password). */
export async function hashPassword(
  password: string,
  iterations = DEFAULT_PBKDF2_ITERATIONS,
): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const hash = await pbkdf2Sha256(password, salt, iterations);
  return `${PASSWORD_HASH_PREFIX}${iterations}$${b64urlEncode(salt)}$${b64urlEncode(hash)}`;
}

/**
 * Constant-time verify against encoded hash.
 * Always runs PBKDF2 when hash parses, to avoid short-circuit timing on bad secrets.
 */
export async function verifyPassword(password: string, encodedHash: string): Promise<boolean> {
  const parsed = parsePasswordHash(encodedHash);
  if (!parsed) {
    // Burn cycles with a dummy derive so missing config is not a fast reject oracle.
    const dummySalt = new Uint8Array(16);
    await pbkdf2Sha256(password || "x", dummySalt, DEFAULT_PBKDF2_ITERATIONS);
    return false;
  }
  const derived = await pbkdf2Sha256(password, parsed.salt, parsed.iterations, parsed.hash.byteLength);
  if (derived.byteLength !== parsed.hash.byteLength) return false;
  const a = b64urlEncode(derived);
  const b = b64urlEncode(parsed.hash);
  return timingSafeEqual(a, b);
}
