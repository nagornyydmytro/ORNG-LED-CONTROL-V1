import { hashToken } from "../auth/device";
import { defaultScopesForPlatform } from "../auth/scopes";
import {
  encryptCredentialEnvelope,
  envelopeToB64,
  generateDeviceToken,
  generatePairingCode,
  sha256Hex,
  timingSafeEqual,
} from "../protocol/crypto";
import type { Env } from "../env";
import { nowIso, uuid } from "../env";
import type { Platform } from "../db/installations";
import { writeAudit } from "../db/audit";

const PAIR_TTL_MS = 10 * 60 * 1000;

export async function hashPairingCode(code: string): Promise<string> {
  return sha256Hex(`orng-pair:${code.toUpperCase()}`);
}

function rateLimitStub(env: Env, ip: string | null): DurableObjectStub {
  const key = `ip:${(ip ?? "unknown").trim() || "unknown"}`;
  const id = env.PAIRING_RATE_LIMIT_DO.idFromName(key);
  return env.PAIRING_RATE_LIMIT_DO.get(id);
}

async function checkIpRateLimit(
  env: Env,
  ip: string | null,
  opts?: { pairingId?: string; expiresAt?: string; register?: boolean },
): Promise<void> {
  const stub = rateLimitStub(env, ip);
  const res = await stub.fetch(
    new Request("https://pairing-rl/check", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        pairing_id: opts?.pairingId,
        expires_at: opts?.expiresAt,
        register: opts?.register ?? false,
      }),
    }),
  );
  if (res.status === 429) {
    const body = (await res.json().catch(() => ({}))) as { reason?: string };
    throw new PairingError(
      429,
      body.reason === "too_many_sessions" ? "too_many_sessions" : "rate_limited",
      "Too many pairing attempts from this IP",
    );
  }
  if (!res.ok) {
    throw new PairingError(503, "rate_limit_unavailable", "Pairing rate limiter unavailable");
  }
}

export async function startPairing(
  env: Env,
  opts: {
    clientChallenge: string;
    publicKeyJwk: JsonWebKey;
    installationIdHint?: string | null;
    ipHint?: string | null;
  },
): Promise<{ pairing_id: string; code: string; expires_at: string }> {
  if (!opts.clientChallenge || opts.clientChallenge.length < 8) {
    throw new PairingError(400, "bad_challenge", "client_challenge required (min 8 chars)");
  }
  if (!opts.publicKeyJwk || opts.publicKeyJwk.kty !== "EC" || opts.publicKeyJwk.crv !== "P-256") {
    throw new PairingError(400, "bad_public_key", "public_key_jwk must be EC P-256");
  }

  const pairingId = uuid();
  const code = generatePairingCode();
  const codeHash = await hashPairingCode(code);
  const expiresAt = new Date(Date.now() + PAIR_TTL_MS).toISOString();
  const createdAt = nowIso();

  // Rate limit by IP before inserting (challenge change cannot bypass).
  await checkIpRateLimit(env, opts.ipHint ?? null, {
    pairingId,
    expiresAt,
    register: true,
  });

  await env.DB.prepare(
    `INSERT INTO pairing_sessions
      (id, code_hash, client_challenge, installation_id_pending, expires_at, used_at,
       created_at, ip_hint, public_key_jwk, credential_envelope, envelope_delivered_at,
       envelope_consumed_at, approved_at)
     VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, NULL)`,
  )
    .bind(
      pairingId,
      codeHash,
      opts.clientChallenge,
      opts.installationIdHint ?? null,
      expiresAt,
      createdAt,
      opts.ipHint ?? null,
      JSON.stringify(opts.publicKeyJwk),
    )
    .run();

  return { pairing_id: pairingId, code, expires_at: expiresAt };
}

export async function approvePairing(
  env: Env,
  opts: {
    pairingId: string;
    code: string;
    name: string;
    platform: Platform;
    actor: string;
    installationId?: string | null;
  },
): Promise<{ installation_id: string; approved: true; pairing: "approved" }> {
  const session = await env.DB.prepare(`SELECT * FROM pairing_sessions WHERE id = ?`)
    .bind(opts.pairingId)
    .first<{
      id: string;
      code_hash: string;
      client_challenge: string;
      installation_id_pending: string | null;
      expires_at: string;
      used_at: string | null;
      approved_at: string | null;
      public_key_jwk: string | null;
      envelope_consumed_at: string | null;
    }>();

  if (!session) throw new PairingError(404, "not_found", "Pairing session not found");
  if (session.used_at || session.approved_at) {
    throw new PairingError(409, "used", "Pairing session already approved");
  }
  if (session.envelope_consumed_at) {
    throw new PairingError(409, "used", "Pairing session already consumed");
  }
  if (Date.parse(session.expires_at) < Date.now()) {
    throw new PairingError(410, "expired", "Pairing session expired");
  }
  if (!session.public_key_jwk) {
    throw new PairingError(400, "missing_public_key", "Pairing session has no device public key");
  }

  const codeHash = await hashPairingCode(opts.code);
  if (!timingSafeEqual(codeHash, session.code_hash)) {
    throw new PairingError(403, "bad_code", "Invalid pairing code");
  }

  const installationId =
    opts.installationId?.trim() ||
    session.installation_id_pending?.trim() ||
    uuid();
  const token = generateDeviceToken();
  const tokenHash = await hashToken(token);
  const scopes = defaultScopesForPlatform(opts.platform);
  const createdAt = nowIso();

  let publicKeyJwk: JsonWebKey;
  try {
    publicKeyJwk = JSON.parse(session.public_key_jwk) as JsonWebKey;
  } catch {
    throw new PairingError(400, "bad_public_key", "Stored public_key_jwk is invalid");
  }

  const envelopeBytes = await encryptCredentialEnvelope(publicKeyJwk, token);

  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO installations
        (id, name, platform, capabilities_json, armed, blackout, created_at)
       VALUES (?, ?, ?, '{}', 0, 1, ?)
       ON CONFLICT(id) DO UPDATE SET
         name = excluded.name,
         platform = excluded.platform,
         revoked_at = NULL`,
    ).bind(installationId, opts.name, opts.platform, createdAt),
    env.DB.prepare(
      `INSERT INTO device_credentials
        (installation_id, token_hash, created_at, rotated_at, scopes_json,
         rotation_pending, pending_token_hash, rotation_envelope, rotation_started_at)
       VALUES (?, ?, ?, NULL, ?, 0, NULL, NULL, NULL)
       ON CONFLICT(installation_id) DO UPDATE SET
         token_hash = excluded.token_hash,
         rotated_at = excluded.created_at,
         scopes_json = excluded.scopes_json,
         rotation_pending = 0,
         pending_token_hash = NULL,
         rotation_envelope = NULL,
         rotation_started_at = NULL`,
    ).bind(installationId, tokenHash, createdAt, JSON.stringify(scopes)),
    env.DB.prepare(
      `UPDATE pairing_sessions SET
         used_at = ?,
         approved_at = ?,
         installation_id_pending = ?,
         credential_envelope = ?
       WHERE id = ?`,
    ).bind(createdAt, createdAt, installationId, envelopeBytes, opts.pairingId),
  ]);

  await writeAudit(env, opts.actor, "pair.approve", installationId, {
    pairing_id: opts.pairingId,
    platform: opts.platform,
    name: opts.name,
  });
  await bindInstallationToExistingLocation(env, installationId);

  // No plaintext token returned to admin.
  return { installation_id: installationId, approved: true, pairing: "approved" };
}

/**
 * Bind a new installation to a location that already exists.
 * One location → that location. Several → only an explicit location_id.
 * Never INSERT a location from pairing.
 */
async function bindInstallationToExistingLocation(env: Env, installationId: string): Promise<void> {
  try {
    const locations = await env.DB.prepare(`SELECT id FROM locations`).all<{ id: string }>();
    const rows = locations.results ?? [];
    if (rows.length !== 1) return;
    await env.DB.prepare(
      `UPDATE installations SET location_id = ? WHERE id = ? AND (location_id IS NULL OR location_id = '')`,
    )
      .bind(rows[0]!.id, installationId)
      .run();
  } catch {
    // locations table is absent in environments that have not migrated.
  }
}

/**
 * Device completes pairing: verifies challenge/session; returns encrypted envelope.
 * Repeatable until consume; after consume one-shot.
 */
export async function completePairing(
  env: Env,
  opts: { pairingId: string; clientChallenge: string },
): Promise<{
  installation_id: string;
  credential_envelope_b64: string;
  consumed: false;
}> {
  const session = await env.DB.prepare(`SELECT * FROM pairing_sessions WHERE id = ?`)
    .bind(opts.pairingId)
    .first<{
      id: string;
      client_challenge: string;
      installation_id_pending: string | null;
      expires_at: string;
      used_at: string | null;
      approved_at: string | null;
      credential_envelope: ArrayBuffer | Uint8Array | null;
      envelope_delivered_at: string | null;
      envelope_consumed_at: string | null;
    }>();

  if (!session) throw new PairingError(404, "not_found", "Pairing session not found");
  if (!session.used_at && !session.approved_at) {
    throw new PairingError(409, "not_approved", "Pairing not yet approved");
  }
  if (!session.installation_id_pending) {
    throw new PairingError(409, "not_approved", "Pairing not yet approved");
  }
  if (session.envelope_consumed_at) {
    throw new PairingError(410, "token_consumed", "Credential envelope already consumed");
  }
  if (Date.parse(session.expires_at) < Date.now()) {
    throw new PairingError(410, "expired", "Pairing session expired");
  }
  if (session.client_challenge !== opts.clientChallenge) {
    throw new PairingError(403, "bad_challenge", "client_challenge mismatch");
  }
  if (!session.credential_envelope) {
    throw new PairingError(410, "token_consumed", "Credential envelope missing");
  }

  const bytes =
    session.credential_envelope instanceof Uint8Array
      ? session.credential_envelope
      : new Uint8Array(session.credential_envelope);

  const deliveredAt = session.envelope_delivered_at ?? nowIso();
  if (!session.envelope_delivered_at) {
    await env.DB.prepare(
      `UPDATE pairing_sessions SET envelope_delivered_at = ? WHERE id = ?`,
    )
      .bind(deliveredAt, opts.pairingId)
      .run();
  }

  return {
    installation_id: session.installation_id_pending,
    credential_envelope_b64: envelopeToB64(bytes),
    consumed: false,
  };
}

/** Device confirms local decrypt success — one-shot consume. */
export async function consumePairingEnvelope(
  env: Env,
  opts: { pairingId: string; clientChallenge: string },
): Promise<{ ok: true; installation_id: string }> {
  const session = await env.DB.prepare(`SELECT * FROM pairing_sessions WHERE id = ?`)
    .bind(opts.pairingId)
    .first<{
      id: string;
      client_challenge: string;
      installation_id_pending: string | null;
      expires_at: string;
      envelope_consumed_at: string | null;
      ip_hint: string | null;
    }>();

  if (!session) throw new PairingError(404, "not_found", "Pairing session not found");
  if (session.envelope_consumed_at) {
    // Idempotent ACK
    return { ok: true, installation_id: session.installation_id_pending ?? "" };
  }
  if (!session.installation_id_pending) {
    throw new PairingError(409, "not_approved", "Pairing not yet approved");
  }
  if (session.client_challenge !== opts.clientChallenge) {
    throw new PairingError(403, "bad_challenge", "client_challenge mismatch");
  }

  const now = nowIso();
  await env.DB.prepare(
    `UPDATE pairing_sessions SET
       envelope_consumed_at = ?,
       credential_envelope = NULL
     WHERE id = ?`,
  )
    .bind(now, opts.pairingId)
    .run();

  // Release active session slot in rate limiter
  try {
    const stub = rateLimitStub(env, session.ip_hint);
    await stub.fetch(
      new Request("https://pairing-rl/release", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ pairing_id: opts.pairingId }),
      }),
    );
  } catch {
    /* best-effort */
  }

  return { ok: true, installation_id: session.installation_id_pending };
}

export async function revokeInstallation(
  env: Env,
  installationId: string,
  actor: string,
): Promise<void> {
  const now = nowIso();
  await env.DB.batch([
    env.DB.prepare(`UPDATE installations SET revoked_at = ? WHERE id = ?`).bind(
      now,
      installationId,
    ),
    env.DB.prepare(`DELETE FROM device_credentials WHERE installation_id = ?`).bind(
      installationId,
    ),
  ]);
  await writeAudit(env, actor, "device.revoke", installationId, {});
}

/**
 * Admin initiates rotation only — no plaintext token to browser.
 * Old token remains valid until device ACK.
 *
 * Without public_key_jwk: marks rotation_pending; device must call
 * {@link prepareRotationEnvelope} with its ECDH public key.
 * With public_key_jwk: encrypts immediately (tests / assisted flows).
 */
export async function initiateTokenRotation(
  env: Env,
  installationId: string,
  actor: string,
  opts?: { publicKeyJwk?: JsonWebKey | null },
): Promise<{ installation_id: string; rotation_pending: true; envelope_ready: boolean }> {
  const inst = await env.DB.prepare(`SELECT id, revoked_at FROM installations WHERE id = ?`)
    .bind(installationId)
    .first<{ id: string; revoked_at: string | null }>();
  if (!inst) throw new PairingError(404, "not_found", "Installation not found");
  if (inst.revoked_at) throw new PairingError(403, "revoked", "Installation revoked");

  const now = nowIso();

  if (!opts?.publicKeyJwk) {
    await env.DB.prepare(
      `UPDATE device_credentials SET
         rotation_pending = 1,
         pending_token_hash = NULL,
         rotation_envelope = NULL,
         rotation_started_at = ?
       WHERE installation_id = ?`,
    )
      .bind(now, installationId)
      .run();
    await writeAudit(env, actor, "device.rotate_pending", installationId, {
      envelope_ready: false,
    });
    return { installation_id: installationId, rotation_pending: true, envelope_ready: false };
  }

  const token = generateDeviceToken();
  const pendingHash = await hashToken(token);
  const rotationEnvelope = await encryptCredentialEnvelope(opts.publicKeyJwk, token);

  await env.DB.prepare(
    `UPDATE device_credentials SET
       rotation_pending = 1,
       pending_token_hash = ?,
       rotation_envelope = ?,
       rotation_started_at = ?
     WHERE installation_id = ?`,
  )
    .bind(pendingHash, rotationEnvelope, now, installationId)
    .run();

  await writeAudit(env, actor, "device.rotate_pending", installationId, {
    envelope_ready: true,
  });
  return { installation_id: installationId, rotation_pending: true, envelope_ready: true };
}

/**
 * Device supplies ECDH public key after admin initiated rotation without a key.
 * Generates pending token + encrypted envelope (never returned to admin browser).
 */
export async function prepareRotationEnvelope(
  env: Env,
  installationId: string,
  publicKeyJwk: JsonWebKey,
): Promise<{ rotation_pending: true; envelope_ready: true }> {
  if (!publicKeyJwk || publicKeyJwk.kty !== "EC" || publicKeyJwk.crv !== "P-256") {
    throw new PairingError(400, "bad_public_key", "public_key_jwk must be EC P-256");
  }
  const row = await env.DB.prepare(
    `SELECT rotation_pending, rotation_envelope FROM device_credentials WHERE installation_id = ?`,
  )
    .bind(installationId)
    .first<{ rotation_pending: number; rotation_envelope: ArrayBuffer | null }>();
  if (!row || !row.rotation_pending) {
    throw new PairingError(409, "no_rotation", "No rotation pending");
  }
  if (row.rotation_envelope) {
    return { rotation_pending: true, envelope_ready: true };
  }

  const token = generateDeviceToken();
  const pendingHash = await hashToken(token);
  const rotationEnvelope = await encryptCredentialEnvelope(publicKeyJwk, token);
  const now = nowIso();
  await env.DB.prepare(
    `UPDATE device_credentials SET
       pending_token_hash = ?,
       rotation_envelope = ?,
       rotation_started_at = COALESCE(rotation_started_at, ?)
     WHERE installation_id = ?`,
  )
    .bind(pendingHash, rotationEnvelope, now, installationId)
    .run();
  return { rotation_pending: true, envelope_ready: true };
}

/**
 * Device fetches pending rotation envelope (Bearer = current token).
 */
export async function getRotationEnvelope(
  env: Env,
  installationId: string,
): Promise<{ rotation_pending: boolean; credential_envelope_b64: string | null }> {
  const row = await env.DB.prepare(
    `SELECT rotation_pending, rotation_envelope FROM device_credentials WHERE installation_id = ?`,
  )
    .bind(installationId)
    .first<{ rotation_pending: number; rotation_envelope: ArrayBuffer | Uint8Array | null }>();

  if (!row || !row.rotation_pending) {
    return { rotation_pending: false, credential_envelope_b64: null };
  }
  if (!row.rotation_envelope) {
    return { rotation_pending: true, credential_envelope_b64: null };
  }
  const bytes =
    row.rotation_envelope instanceof Uint8Array
      ? row.rotation_envelope
      : new Uint8Array(row.rotation_envelope);
  return {
    rotation_pending: true,
    credential_envelope_b64: envelopeToB64(bytes),
  };
}

/**
 * Device ACK: activate pending token, invalidate old. Idempotent.
 */
export async function ackTokenRotation(
  env: Env,
  installationId: string,
  newToken: string,
): Promise<{ ok: true; activated: boolean }> {
  const pendingHash = await hashToken(newToken);
  const row = await env.DB.prepare(
    `SELECT token_hash, pending_token_hash, rotation_pending
     FROM device_credentials WHERE installation_id = ?`,
  )
    .bind(installationId)
    .first<{
      token_hash: string;
      pending_token_hash: string | null;
      rotation_pending: number;
    }>();

  if (!row) throw new PairingError(404, "not_found", "Credentials not found");

  // Already activated (idempotent): new token is now the active hash
  if (!row.rotation_pending && timingSafeEqual(row.token_hash, pendingHash)) {
    return { ok: true, activated: false };
  }

  if (!row.rotation_pending || !row.pending_token_hash) {
    throw new PairingError(409, "no_rotation", "No rotation pending");
  }
  if (!timingSafeEqual(row.pending_token_hash, pendingHash)) {
    throw new PairingError(403, "bad_token", "Pending token mismatch");
  }

  const now = nowIso();
  await env.DB.prepare(
    `UPDATE device_credentials SET
       token_hash = ?,
       rotated_at = ?,
       rotation_pending = 0,
       pending_token_hash = NULL,
       rotation_envelope = NULL,
       rotation_started_at = NULL
     WHERE installation_id = ?`,
  )
    .bind(pendingHash, now, installationId)
    .run();

  return { ok: true, activated: true };
}

/** @deprecated Use initiateTokenRotation — kept name for older imports during transition. */
export async function rotateToken(
  env: Env,
  installationId: string,
  actor: string,
): Promise<{ installation_id: string; rotation_pending: true }> {
  return initiateTokenRotation(env, installationId, actor);
}

export class PairingError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "PairingError";
  }
}
