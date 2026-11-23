import {
  extractDeviceToken,
  fallbackDeviceAuth,
  isDatabaseReadLimit,
  isDurableVolumeLimit,
  queryTokenForbidden,
  verifyDeviceToken,
  type DeviceAuth,
} from "../auth/device";
import {
  SCOPE_APP_READ_ASSIGNED,
  SCOPE_RUNTIME_CONNECT,
  SCOPE_SCENE_LIST,
  SCOPE_SCENE_PUBLISH,
  SCOPE_SCENE_READ,
  SCOPE_SCENE_READ_ASSIGNED,
  SCOPE_STATE_REPORT,
  hasScope,
} from "../auth/scopes";
import {
  AppReleaseError,
  getAppAssignment,
  getAppManifestAuthorized,
  getAppPackageAuthorized,
  publicAppAssignment,
  reportAppUpdateResult,
} from "../app/releases";
import { ackDeskCommands, pollQueuedCommands, saveDeskSnapshot, takeFreshQueuedCommands, type DeskSnapshot } from "../db/deskLink";
import { touchHeartbeat } from "../db/installations";
import { deviceStub, forwardAdminWs, forwardDeviceWs } from "../devices";
import type { Env } from "../env";
import { errorJson, json } from "../env";
import {
  PairingError,
  ackTokenRotation,
  completePairing,
  consumePairingEnvelope,
  getRotationEnvelope,
  prepareRotationEnvelope,
  startPairing,
} from "../pairing";
import { DESK_SERVICE_COMMANDS } from "../protocol/types";
import { locationIdForInstallation, readShowPrefs } from "../locations/showPrefs";
import {
  ReleaseError,
  getAssignment,
  getReleaseBundleAuthorized,
  listReleases,
  listScenes,
  publishRelease,
} from "../scenes/releases";

export async function handleDeviceRequest(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;

  if (path.startsWith("/admin")) {
    return errorJson(404, "not_found", "Admin routes are not available on the device host");
  }

  try {
    if (path === "/pair/start" && request.method === "POST") {
      const body = (await request.json()) as {
        installation_id?: string;
        client_challenge: string;
        public_key_jwk: JsonWebKey;
      };
      const ip =
        request.headers.get("cf-connecting-ip") ??
        request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
        null;
      const result = await startPairing(env, {
        clientChallenge: body.client_challenge,
        publicKeyJwk: body.public_key_jwk,
        installationIdHint: body.installation_id,
        ipHint: ip,
      });
      return json(result);
    }

    if (path === "/pair/complete" && request.method === "POST") {
      const body = (await request.json()) as {
        pairing_id: string;
        client_challenge: string;
      };
      const result = await completePairing(env, {
        pairingId: body.pairing_id,
        clientChallenge: body.client_challenge,
      });
      return json(result);
    }

    if (path === "/pair/consume" && request.method === "POST") {
      const body = (await request.json()) as {
        pairing_id: string;
        client_challenge: string;
      };
      const result = await consumePairingEnvelope(env, {
        pairingId: body.pairing_id,
        clientChallenge: body.client_challenge,
      });
      return json(result);
    }

    if (path === "/device/link" && request.method === "POST") {
      let auth: DeviceAuth;
      try {
        const checked = await requireDevice(request, env, [SCOPE_RUNTIME_CONNECT]);
        if (checked instanceof Response) return checked;
        auth = checked;
      } catch (err) {
        if (!isDatabaseReadLimit(err)) throw err;
        const fallback = await fallbackDeviceAuth(request, env);
        if (!fallback) return errorJson(401, "unauthorized", "Bearer token required");
        auth = fallback;
      }
      const body = (await request.json()) as {
        heartbeat?: Record<string, unknown>;
        effect?: DeskSnapshot;
        acks?: Array<{
          command_id?: string;
          status?: string;
          error_code?: string | null;
          error_message?: string | null;
        }>;
        wait_ms?: number;
      };
      const beat = body.heartbeat ?? {};
      const effect = body.effect ?? {};
      const desk: DeskSnapshot = {
        preset_id: str(beat.preset_id) ?? str(effect.preset_id) ?? null,
        look: str(effect.look) ?? str(beat.look) ?? null,
        program: str(effect.program) ?? str(beat.program) ?? null,
        bpm: beat.bpm ?? null,
        face_on: bool(beat.face_on) ?? null,
        strobe_held: bool(beat.strobe_held) ?? null,
        sweep_active: bool(beat.sweep_active) ?? null,
        vertical_sweep_active: bool(beat.vertical_sweep_active) ?? null,
        preset_time_s: num(beat.preset_time_s) ?? null,
        episode_index: num(beat.episode_index) ?? null,
        episode_count: num(beat.episode_count) ?? null,
        master_brightness: num(beat.master_brightness) ?? null,
        scene_name: str(beat.scene_name) ?? null,
        pad_presets: beat.pad_presets ?? null,
        capabilities: Array.isArray(beat.capabilities)
          ? beat.capabilities.filter((item): item is string => typeof item === "string")
          : null,
        armed: bool(beat.armed) ?? null,
        blackout: bool(beat.blackout) ?? null,
        transport: str(beat.transport) ?? null,
        current_scene_id: str(beat.current_scene_id) ?? null,
        session: str(effect.session) ?? null,
        sequence: num(effect.sequence) ?? null,
        age_ms: num(effect.age_ms) ?? null,
      };
      const waitMs = Math.min(2_500, Math.max(0, num(body.wait_ms) ?? 0));
      return postMailbox(env, auth.installationId, desk, beat, body.acks, waitMs);
    }

    if (path === "/ws/device" && request.headers.get("Upgrade")?.toLowerCase() === "websocket") {
      if (queryTokenForbidden(request, env)) {
        return errorJson(401, "query_token_forbidden", "Use Authorization: Bearer (query token disabled)");
      }
      const token = extractDeviceToken(request, env);
      if (!token) return errorJson(401, "unauthorized", "Bearer token required");
      let auth: DeviceAuth | null;
      try {
        auth = await verifyDeviceToken(env, token);
      } catch (err) {
        if (!isDatabaseReadLimit(err)) throw err;
        auth = await fallbackDeviceAuth(request, env);
      }
      if (!auth) return errorJson(401, "unauthorized", "Invalid device token");
      if (auth.revoked) return errorJson(403, "revoked", "Installation revoked");
      if (!hasScope(auth.scopes, SCOPE_RUNTIME_CONNECT)) {
        return errorJson(403, "forbidden", "Missing runtime:connect scope");
      }
      return forwardDeviceWs(env, auth.installationId, request);
    }

    if (path === "/device/executor/state" && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_PUBLISH]);
      if (auth instanceof Response) return auth;
      const executorId = await executorInstallationId(env, auth.installationId);
      if (!executorId) {
        return errorJson(404, "no_executor", "Location has no Raspberry executor");
      }
      const stub = deviceStub(env, executorId);
      const statusRes = await stub.fetch("https://device-do/status");
      const status = (await statusRes.json()) as {
        online?: boolean;
        last_state?: Record<string, unknown> | null;
      };
      return json({
        installation_id: executorId,
        online: Boolean(status.online),
        ...(status.last_state ?? {}),
      });
    }

    if (path === "/device/executor/command" && request.method === "POST") {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_PUBLISH]);
      if (auth instanceof Response) return auth;
      const executorId = await executorInstallationId(env, auth.installationId);
      if (!executorId) {
        return errorJson(404, "no_executor", "Location has no Raspberry executor");
      }
      if (executorId === auth.installationId) {
        return errorJson(409, "not_executor", "This desk is not the location executor");
      }
      const body = (await request.json()) as {
        type?: string;
        payload?: Record<string, unknown>;
      };
      if (!body.type) return errorJson(400, "bad_request", "type required");
      if (DESK_SERVICE_COMMANDS.has(body.type)) {
        return errorJson(
          403,
          "desk_forbidden",
          "Update, restart and output arming are not desk commands",
        );
      }
      const payload = body.payload ?? {};
      const requested = typeof payload.source === "string" ? payload.source : "";
      const source = requested.startsWith("cloud:desk:")
        ? requested
        : `cloud:desk:${auth.installationId}`;
      const stub = deviceStub(env, executorId);
      const res = await stub.fetch(
        new Request("https://device-do/command", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            type: body.type,
            installation_id: executorId,
            actor: `desk:${auth.installationId}`,
            source,
            payload: { ...payload, source },
          }),
        }),
      );
      const data = await res.json();
      return json(data, res.status);
    }

    if (
      path === "/device/executor/session" &&
      request.headers.get("Upgrade")?.toLowerCase() === "websocket"
    ) {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_READ, SCOPE_SCENE_PUBLISH]);
      if (auth instanceof Response) return auth;
      const executorId = await executorInstallationId(env, auth.installationId);
      if (!executorId) {
        return errorJson(404, "no_executor", "Location has no Raspberry executor");
      }
      const source = `cloud:desk:${crypto.randomUUID()}`;
      return forwardAdminWs(env, executorId, request, source);
    }

    if (path === "/device/location/show-prefs" && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_READ_ASSIGNED, SCOPE_SCENE_READ]);
      if (auth instanceof Response) return auth;
      const locationId = await locationIdForInstallation(env, auth.installationId);
      if (!locationId) return errorJson(404, "no_location", "Installation has no location");
      return json({ location_id: locationId, prefs: await readShowPrefs(env, locationId) });
    }

    if (path === "/device/assignment" && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_READ_ASSIGNED, SCOPE_SCENE_READ]);
      if (auth instanceof Response) return auth;
      const assignment = await getAssignment(env, auth.installationId);
      return json({ assignment });
    }

    if (path === "/device/rotation" && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_RUNTIME_CONNECT]);
      if (auth instanceof Response) return auth;
      return json(await getRotationEnvelope(env, auth.installationId));
    }

    if (path === "/device/rotation/prepare" && request.method === "POST") {
      const auth = await requireDevice(request, env, [SCOPE_RUNTIME_CONNECT]);
      if (auth instanceof Response) return auth;
      const body = (await request.json()) as { public_key_jwk?: JsonWebKey };
      if (!body.public_key_jwk) {
        return errorJson(400, "bad_request", "public_key_jwk required");
      }
      return json(await prepareRotationEnvelope(env, auth.installationId, body.public_key_jwk));
    }

    if (path === "/device/rotation/ack" && request.method === "POST") {
      const auth = await requireDevice(request, env, [SCOPE_RUNTIME_CONNECT]);
      if (auth instanceof Response) return auth;
      const body = (await request.json()) as { token?: string; new_token?: string };
      const newToken = body.token ?? body.new_token;
      if (!newToken) return errorJson(400, "bad_request", "token required");
      const result = await ackTokenRotation(env, auth.installationId, newToken);
      return json(result);
    }

    // Device-host catalog (desktop scopes)
    if (path === "/device/scenes" && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_LIST, SCOPE_SCENE_READ]);
      if (auth instanceof Response) return auth;
      return json({ scenes: await listScenes(env) });
    }

    const deviceReleases = /^\/device\/scenes\/([^/]+)\/releases$/.exec(path);
    if (deviceReleases && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_READ, SCOPE_SCENE_LIST]);
      if (auth instanceof Response) return auth;
      const sceneId = decodeURIComponent(deviceReleases[1]!);
      return json({ releases: await listReleases(env, sceneId) });
    }

    const devicePublish = /^\/device\/scenes\/([^/]+)\/publish$/.exec(path);
    if (devicePublish && request.method === "POST") {
      const auth = await requireDevice(request, env, [SCOPE_SCENE_PUBLISH]);
      if (auth instanceof Response) return auth;
      const sceneId = decodeURIComponent(devicePublish[1]!);
      const body = (await request.json()) as {
        name?: string;
        base_version?: number | null;
        min_app_version?: string | null;
        bundle_base64?: string;
        bundle?: string;
      };
      const b64 = body.bundle_base64 ?? body.bundle;
      if (!b64) return errorJson(400, "bad_request", "bundle_base64 required");
      const binary = atob(b64);
      const bundleBytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bundleBytes[i] = binary.charCodeAt(i);
      const result = await publishRelease(env, {
        sceneId,
        sceneName: body.name ?? sceneId,
        bundleBytes,
        baseVersion: body.base_version ?? null,
        publishedBy: `device:${auth.installationId}`,
        minAppVersion: body.min_app_version ?? null,
      });
      return json({
        release: result.release,
        idempotent: result.idempotent,
        assignments_updated: result.assignments_updated,
      });
    }

    const dl = /^\/device\/releases\/([^/]+)\/(\d+)$/.exec(path);
    if (dl && request.method === "GET") {
      const auth = await requireDevice(request, env, [
        SCOPE_SCENE_READ_ASSIGNED,
        SCOPE_SCENE_READ,
      ]);
      if (auth instanceof Response) return auth;
      const sceneId = decodeURIComponent(dl[1]!);
      const version = Number(dl[2]);
      const bundle = await getReleaseBundleAuthorized(env, {
        installationId: auth.installationId,
        sceneId,
        version,
        scopes: auth.scopes,
        revoked: auth.revoked,
      });
      const headers: Record<string, string> = {
        "content-type": "application/json",
        "x-content-hash": bundle.manifest.content_sha256,
        "x-scene-id": sceneId,
        "x-version": String(version),
      };
      if (bundle.manifest.min_app_version) {
        headers["x-min-app-version"] = bundle.manifest.min_app_version;
      }
      return new Response(bundle.body.body, { headers });
    }

    // --- Phase 3 APPLICATION updates (download ACL only; publish is Access-admin) ---
    if (path === "/device/app/assignment" && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_APP_READ_ASSIGNED]);
      if (auth instanceof Response) return auth;
      const assignment = await getAppAssignment(env, auth.installationId);
      return json({
        assignment: assignment ? publicAppAssignment(assignment) : null,
      });
    }

    const appManifestDl =
      /^\/device\/app\/releases\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)\/manifest$/.exec(path);
    if (appManifestDl && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_APP_READ_ASSIGNED]);
      if (auth instanceof Response) return auth;
      const platform = decodeURIComponent(appManifestDl[1]!);
      const arch = decodeURIComponent(appManifestDl[2]!);
      const version = decodeURIComponent(appManifestDl[3]!);
      const buildId = decodeURIComponent(appManifestDl[4]!);
      const contract = await getAppManifestAuthorized(env, {
        installationId: auth.installationId,
        platform,
        arch,
        version,
        build_id: buildId,
        scopes: auth.scopes,
        revoked: auth.revoked,
        trust: { env, hostHeader: request.headers.get("host") },
      });
      return json(contract);
    }

    const appPackageDl =
      /^\/device\/app\/releases\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)\/package$/.exec(path);
    if (appPackageDl && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_APP_READ_ASSIGNED]);
      if (auth instanceof Response) return auth;
      const platform = decodeURIComponent(appPackageDl[1]!);
      const arch = decodeURIComponent(appPackageDl[2]!);
      const version = decodeURIComponent(appPackageDl[3]!);
      const buildId = decodeURIComponent(appPackageDl[4]!);
      const pkg = await getAppPackageAuthorized(env, {
        installationId: auth.installationId,
        platform,
        arch,
        version,
        build_id: buildId,
        scopes: auth.scopes,
        revoked: auth.revoked,
        trust: { env, hostHeader: request.headers.get("host") },
      });
      const headers: Record<string, string> = {
        "content-type": "application/gzip",
        "content-length": String(pkg.release.package_size),
        "x-content-sha256": pkg.release.content_sha256,
        "x-content-hash": pkg.release.content_sha256,
        "x-app-version": pkg.release.version,
        "x-build-id": pkg.release.build_id,
        "x-platform": pkg.release.platform,
        "x-arch": pkg.release.arch,
        "x-channel": pkg.release.channel,
        "x-key-id": pkg.release.key_id,
      };
      return new Response(pkg.body.body, { headers });
    }

    // Legacy single endpoint → package bytes (deprecated; prefer /manifest + /package).
    const appDl =
      /^\/device\/app\/releases\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)$/.exec(path);
    if (appDl && request.method === "GET") {
      const auth = await requireDevice(request, env, [SCOPE_APP_READ_ASSIGNED]);
      if (auth instanceof Response) return auth;
      const platform = decodeURIComponent(appDl[1]!);
      const arch = decodeURIComponent(appDl[2]!);
      const version = decodeURIComponent(appDl[3]!);
      const buildId = decodeURIComponent(appDl[4]!);
      const pkg = await getAppPackageAuthorized(env, {
        installationId: auth.installationId,
        platform,
        arch,
        version,
        build_id: buildId,
        scopes: auth.scopes,
        revoked: auth.revoked,
        trust: { env, hostHeader: request.headers.get("host") },
      });
      const successor = `/device/app/releases/${encodeURIComponent(platform)}/${encodeURIComponent(arch)}/${encodeURIComponent(version)}/${encodeURIComponent(buildId)}/package`;
      return new Response(pkg.body.body, {
        headers: {
          "content-type": "application/gzip",
          "content-length": String(pkg.release.package_size),
          "x-content-sha256": pkg.release.content_sha256,
          "x-content-hash": pkg.release.content_sha256,
          "x-app-version": pkg.release.version,
          "x-build-id": pkg.release.build_id,
          "x-platform": pkg.release.platform,
          "x-arch": pkg.release.arch,
          "x-channel": pkg.release.channel,
          "x-key-id": pkg.release.key_id,
          deprecation: "true",
          link: `<${successor}>; rel="successor-version"`,
          "x-deprecated-endpoint":
            "Use GET .../manifest then GET .../package",
        },
      });
    }

    if (path === "/device/app/update-result" && request.method === "POST") {
      const auth = await requireDevice(request, env, [
        SCOPE_APP_READ_ASSIGNED,
        SCOPE_STATE_REPORT,
      ]);
      if (auth instanceof Response) return auth;
      const body = (await request.json()) as {
        event_type?: "applied" | "failed" | "rolled_back" | "staged" | "cancelled";
        command_id?: string;
        version?: string;
        build_id?: string;
        error_message?: string;
        detail?: Record<string, unknown>;
      };
      if (!body.event_type) {
        return errorJson(400, "bad_request", "event_type required");
      }
      const result = await reportAppUpdateResult(env, {
        installationId: auth.installationId,
        eventType: body.event_type,
        commandId: body.command_id ?? null,
        detail: {
          ...(body.detail ?? {}),
          version: body.version,
          build_id: body.build_id,
          error_message: body.error_message,
        },
      });
      // Also finalize DO pending map when online (best-effort).
      if (result.terminal && body.command_id) {
        try {
          await deviceStub(env, auth.installationId).fetch(
            new Request("https://device-do/update-result", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                command_id: body.command_id,
                event_type: body.event_type,
              }),
            }),
          );
        } catch {
          /* DO may be cold; command_log already updated */
        }
      }
      return json({ ok: true, ...result });
    }

    // Device Bearer must NEVER publish application releases (Access-admin only).
    if (path === "/device/app/releases/publish" || path.startsWith("/admin/app/")) {
      return errorJson(403, "forbidden", "Application release publish requires Access admin");
    }

    if (path === "/" || path === "/health") {
      return json({ service: "orng-led-device", ok: true });
    }

    return errorJson(404, "not_found", "Unknown device route");
  } catch (err) {
    if (
      err instanceof PairingError ||
      err instanceof ReleaseError ||
      err instanceof AppReleaseError
    ) {
      return errorJson(err.status, err.code, err.message);
    }
    console.error("device error", err);
    return errorJson(500, "internal", "Internal error");
  }
}
async function executorInstallationId(env: Env, callerId: string): Promise<string | null> {
  const caller = await env.DB.prepare(
    `SELECT id, platform, location_id FROM installations WHERE id = ?`,
  )
    .bind(callerId)
    .first<{ id: string; platform: string; location_id: string | null }>();
  if (!caller || caller.platform === "raspberry" || !caller.location_id) return null;
  let explicit: string | null = null;
  try {
    const location = await env.DB.prepare(
      `SELECT executor_installation_id FROM locations WHERE id = ?`,
    )
      .bind(caller.location_id)
      .first<{ executor_installation_id: string | null }>();
    explicit = location?.executor_installation_id ?? null;
  } catch {
    explicit = null;
  }
  if (explicit) {
    const named = await env.DB.prepare(
      `SELECT id, platform, revoked_at FROM installations WHERE id = ?`,
    )
      .bind(explicit)
      .first<{ id: string; platform: string; revoked_at: string | null }>();
    if (!named || named.revoked_at || named.platform !== "raspberry" || named.id === callerId) {
      return null;
    }
    return named.id;
  }
  const rows = await env.DB.prepare(
    `SELECT id FROM installations
     WHERE location_id = ? AND platform = 'raspberry' AND revoked_at IS NULL`,
  )
    .bind(caller.location_id)
    .all<{ id: string }>();
  const found = (rows.results ?? []).filter((row) => row.id !== callerId);
  return found.length === 1 ? found[0]!.id : null;
}

async function requireDevice(
  request: Request,
  env: Env,
  requiredAnyScope: string[],
): Promise<DeviceAuth | Response> {
  if (queryTokenForbidden(request, env)) {
    return errorJson(401, "query_token_forbidden", "Use Authorization: Bearer (query token disabled)");
  }
  const token = extractDeviceToken(request, env);
  if (!token) return errorJson(401, "unauthorized", "Bearer token required");
  const auth = await verifyDeviceToken(env, token);
  if (!auth) return errorJson(401, "unauthorized", "Invalid device token");
  if (auth.revoked) return errorJson(403, "revoked", "Installation revoked");
  if (!requiredAnyScope.some((s) => hasScope(auth.scopes, s))) {
    return errorJson(403, "forbidden", `Missing required scope (${requiredAnyScope.join(" | ")})`);
  }
  return auth;
}

async function postMailbox(
  env: Env,
  installationId: string,
  desk: DeskSnapshot,
  heartbeat: Record<string, unknown>,
  acks: Array<{ command_id?: string }> | undefined,
  waitMs = 0,
): Promise<Response> {
  try {
    const stub = deviceStub(env, installationId);
    const res = await stub.fetch(
      new Request("https://device-do/mailbox", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          installation_id: installationId,
          desk,
          heartbeat,
          acks: acks ?? [],
        }),
      }),
    );
    const data = (await res.json()) as { commands?: unknown };
    return json({
      ok: true,
      installation_id: installationId,
      commands: Array.isArray(data.commands) ? data.commands : [],
    });
  } catch (err) {
    if (!isDurableVolumeLimit(err)) throw err;
    return linkThroughDatabase(env, installationId, desk, heartbeat, acks, waitMs);
  }
}

async function linkThroughDatabase(
  env: Env,
  installationId: string,
  desk: DeskSnapshot,
  heartbeat: Record<string, unknown>,
  acks: Array<{ command_id?: string; status?: string; error_code?: string | null; error_message?: string | null }> | undefined,
  waitMs = 0,
): Promise<Response> {
  await saveDeskSnapshot(env, installationId, desk);
  await touchHeartbeat(env, installationId, {
    armed: typeof heartbeat.armed === "boolean" ? heartbeat.armed : undefined,
    blackout: typeof heartbeat.blackout === "boolean" ? heartbeat.blackout : undefined,
    transport: typeof heartbeat.transport === "string" ? heartbeat.transport : undefined,
    current_scene_id: typeof heartbeat.current_scene_id === "string" ? heartbeat.current_scene_id : undefined,
    app_version: typeof heartbeat.app_version === "string" ? heartbeat.app_version : undefined,
    build_id: typeof heartbeat.build_id === "string" ? heartbeat.build_id : undefined,
    arch: typeof heartbeat.arch === "string" ? heartbeat.arch : undefined,
  }).catch((err) => {
    if (!isDatabaseReadLimit(err)) throw err;
  });
  await ackDeskCommands(env, installationId, acks ?? []).catch(() => undefined);
  const commands = await pollQueuedCommands(
    async () => {
      try {
        return await takeFreshQueuedCommands(env, installationId);
      } catch (err) {
        if (!isDatabaseReadLimit(err)) throw err;
        return [];
      }
    },
    waitMs,
  );
  return json({
    ok: true,
    installation_id: installationId,
    commands,
    relay: "database",
  });
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function bool(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}
