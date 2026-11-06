/** Hashing helpers — tokens and pairing codes are never stored plaintext. */

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

export async function sha256Hex(input: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", textEncoder.encode(input));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Bytes(data: ArrayBuffer | Uint8Array): Promise<string> {
  const buf = data instanceof Uint8Array ? data : new Uint8Array(data);
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Cryptographically random 8-char alphanumeric pairing code (display once). */
export function generatePairingCode(): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => alphabet[b % alphabet.length]).join("");
}

/** Long device token — never returned to admin browser; only inside encrypted envelope. */
export function generateDeviceToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

export interface CredentialEnvelopeV1 {
  v: 1;
  alg: "ECDH-ES+A256GCM";
  epk: JsonWebKey;
  iv: string;
  ct: string;
}

/** Generate an ECDH P-256 key pair for device-side envelope decryption. */
export async function generateDeviceKeyPair(): Promise<{
  publicKeyJwk: JsonWebKey;
  privateKey: CryptoKey;
}> {
  const pair = (await crypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" },
    true,
    ["deriveBits"],
  )) as CryptoKeyPair;
  const publicKeyJwk = (await crypto.subtle.exportKey("jwk", pair.publicKey)) as JsonWebKey;
  return { publicKeyJwk, privateKey: pair.privateKey };
}

/** workers-types use `$public`; browsers use `public` — support both via cast. */
function ecdhDeriveAlg(publicKey: CryptoKey): Parameters<SubtleCrypto["deriveBits"]>[0] {
  return { name: "ECDH", public: publicKey } as Parameters<SubtleCrypto["deriveBits"]>[0];
}

/** Encrypt plaintext for a device ECDH public key (ephemeral ECDH + AES-GCM). */
export async function encryptCredentialEnvelope(
  devicePublicKeyJwk: JsonWebKey,
  plaintext: string,
): Promise<Uint8Array> {
  if (devicePublicKeyJwk.kty !== "EC" || devicePublicKeyJwk.crv !== "P-256") {
    throw new Error("device public key must be EC P-256 JWK");
  }
  const deviceKey = await crypto.subtle.importKey(
    "jwk",
    devicePublicKeyJwk,
    { name: "ECDH", namedCurve: "P-256" },
    false,
    [],
  );
  const ephemeral = (await crypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" },
    true,
    ["deriveBits"],
  )) as CryptoKeyPair;
  const sharedBits = await crypto.subtle.deriveBits(
    ecdhDeriveAlg(deviceKey),
    ephemeral.privateKey,
    256,
  );
  const aesKey = await crypto.subtle.importKey(
    "raw",
    await crypto.subtle.digest("SHA-256", sharedBits),
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt"],
  );
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    aesKey,
    textEncoder.encode(plaintext),
  );
  const epk = (await crypto.subtle.exportKey("jwk", ephemeral.publicKey)) as JsonWebKey;
  const envelope: CredentialEnvelopeV1 = {
    v: 1,
    alg: "ECDH-ES+A256GCM",
    epk,
    iv: bytesToB64(iv),
    ct: bytesToB64(new Uint8Array(ciphertext)),
  };
  return textEncoder.encode(JSON.stringify(envelope));
}

/** Decrypt a credential envelope with the device private key (tests / device agents). */
export async function decryptCredentialEnvelope(
  privateKey: CryptoKey,
  envelopeBytes: Uint8Array,
): Promise<string> {
  const envelope = JSON.parse(textDecoder.decode(envelopeBytes)) as CredentialEnvelopeV1;
  if (envelope.v !== 1 || envelope.alg !== "ECDH-ES+A256GCM" || !envelope.epk) {
    throw new Error("unsupported credential envelope");
  }
  const epk = await crypto.subtle.importKey(
    "jwk",
    envelope.epk,
    { name: "ECDH", namedCurve: "P-256" },
    false,
    [],
  );
  const sharedBits = await crypto.subtle.deriveBits(ecdhDeriveAlg(epk), privateKey, 256);
  const aesKey = await crypto.subtle.importKey(
    "raw",
    await crypto.subtle.digest("SHA-256", sharedBits),
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
  const iv = b64ToBytes(envelope.iv);
  const ct = b64ToBytes(envelope.ct);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, aesKey, ct);
  return textDecoder.decode(plain);
}

export function bytesToB64(data: Uint8Array): string {
  let s = "";
  for (let i = 0; i < data.length; i++) s += String.fromCharCode(data[i]!);
  return btoa(s);
}

export function b64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export function envelopeToB64(bytes: Uint8Array): string {
  return bytesToB64(bytes);
}

export function envelopeFromB64(b64: string): Uint8Array {
  return b64ToBytes(b64);
}
